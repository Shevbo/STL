package trade

import (
	"context"
	"sync"
	"time"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

// BookSource supplies the LOCAL order book for the maker loop (no STL round-trip per
// tick). *quikdde.Provider satisfies it via OrderBook(code). It is an interface so the
// re-quote / collar decisions can be unit-tested without a live book.
type BookSource interface {
	OrderBook(code string) (quikdde.Book, bool)
}

// quoteDecision is the pure output of evaluating the book against current state. It is
// what makes the maker loop testable: given a book + last quote + clock, decide whether
// to (re)quote, at what price, or to stop on the collar.
type quoteDecision struct {
	action     decisionAction
	price      float64 // target join price when action == quote
	collarHit  bool
}

type decisionAction int

const (
	decisionHold     decisionAction = iota // keep the current resting quote
	decisionQuote                          // place / replace at price
	decisionCollar                         // worst_price breached -> stop, cancel remainder
	decisionNoBook                         // no usable book this tick -> wait
)

// makerParams are the immutable inputs of one execution.
type makerParams struct {
	clientID   string
	code       string
	buy        bool
	target     int64
	worstPrice float64 // hard collar bound; never quote/fill beyond this
	allowCross bool    // taker fallback; default false (pure maker)
	priceStep  float64 // 1 price step; re-quote only on a move >= this
	minRequote time.Duration // anti-flicker: no more often than this (e.g. 200ms)
}

// bestJoinPrice returns the price to JOIN our own side's touch without crossing: best
// bid for a BUY, best ask for a SELL. ok=false when our side has no level.
func bestJoinPrice(book quikdde.Book, buy bool) (float64, bool) {
	if buy {
		if len(book.Bids) == 0 {
			return 0, false
		}
		return book.Bids[0].Price, true // bids are sorted best-first (highest)
	}
	if len(book.Asks) == 0 {
		return 0, false
	}
	return book.Asks[0].Price, true // asks are sorted best-first (lowest)
}

// beyondCollar reports whether a join price is worse than the worst_price bound for
// the side. For a BUY, prices ABOVE worst are beyond; for a SELL, prices BELOW worst.
func beyondCollar(buy bool, price, worst float64) bool {
	if worst <= 0 {
		return false
	}
	if buy {
		return price > worst
	}
	return price < worst
}

// decide is the pure re-quote/collar decision for one tick. It never crosses the
// spread: it only ever returns a join price on our own side. It re-quotes only when the
// touch moved by >= one price step AND the anti-flicker window has elapsed.
//
//   book        : current local order book
//   p           : immutable execution params
//   haveQuote   : whether we currently have a resting quote
//   lastPrice   : our current resting quote price (valid iff haveQuote)
//   lastQuoteAt : when we last (re)quoted
//   now         : current time
func decide(book quikdde.Book, p makerParams, haveQuote bool, lastPrice float64, lastQuoteAt, now time.Time) quoteDecision {
	join, ok := bestJoinPrice(book, p.buy)
	if !ok {
		return quoteDecision{action: decisionNoBook}
	}
	// Collar: never quote beyond worst_price. If our own touch has run past the
	// collar, stop (do not chase, do not become taker unless allow_cross — and even
	// then the manager, not this pure fn, would handle crossing).
	if beyondCollar(p.buy, join, p.worstPrice) {
		return quoteDecision{action: decisionCollar, collarHit: true}
	}
	if !haveQuote {
		return quoteDecision{action: decisionQuote, price: join}
	}
	// Already resting at lastPrice. Re-quote only if the touch moved by at least one
	// price step AND the anti-flicker window elapsed. With an unknown step (0) any
	// real move qualifies, but an identical price never does.
	delta := absf(join - lastPrice)
	threshold := p.priceStep - priceEps
	if threshold < priceEps {
		threshold = priceEps // unknown step: any nonzero move qualifies
	}
	if delta < threshold {
		return quoteDecision{action: decisionHold}
	}
	if p.minRequote > 0 && now.Sub(lastQuoteAt) < p.minRequote {
		return quoteDecision{action: decisionHold}
	}
	return quoteDecision{action: decisionQuote, price: join}
}

const priceEps = 1e-9

func absf(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

// execPlacer is the side of the manager the execution loop drives. The manager
// satisfies it; tests pass a fake to assert join-not-cross / re-quote / collar without
// a live bridge.
type execPlacer interface {
	// placeChild submits one maker limit for the execution's remaining quantity at
	// price, returning the client_id of the child order (for cancel/tracking).
	placeChild(parentClientID, code string, buy bool, price float64, qty int64) (childID string, err error)
	// cancelChild cancels a previously placed child order.
	cancelChild(childID string)
	// emitExec sends an ExecutionUpdate to STL.
	emitExec(*quikv1.ExecutionUpdate)
	// nowMs is the wall clock in unix ms (for ExecutionUpdate timestamps).
	nowMs() int64
}

// execution is one running 1b maker-working loop. It is driven both by a ticker
// (re-evaluate the book) and by order/trade events forwarded from the manager. It is
// gated behind an explicit, confirmed StartExecution and the master flag (checked in
// the manager before construction). StopExecution stops it.
type execution struct {
	p       makerParams
	book    BookSource
	placer  execPlacer
	logf    func(string, ...any)
	tick    time.Duration
	nowFn   func() time.Time

	cancel context.CancelFunc

	mu            sync.Mutex
	filled        int64
	sumPxQty      float64 // for avg price
	haveQuote     bool
	childID       string  // current child order's client_id ("" = none live)
	pendingCancel bool    // a cancel was sent for childID; await its terminal event
	lastPrice     float64
	lastQuoteAt   time.Time
	placedCount   int     // total child orders placed this execution (runaway backstop)
	stopped       bool
	stopReason    string
}

// maxChildPlacements caps how many child orders one execution may ever place. A
// correct single-child loop places ~ (re-quotes + 1); this is a hard backstop against
// any future logic bug causing a runaway. Tripping it stops the execution.
const maxChildPlacements = 50

func (e *execution) now() time.Time {
	if e.nowFn != nil {
		return e.nowFn()
	}
	return time.Now()
}

// run drives the loop until target reached, collar hit, or stop. It re-quotes on the
// tick cadence (book re-read) using the pure decide().
func (e *execution) run(ctx context.Context) {
	cctx, cancel := context.WithCancel(ctx)
	e.mu.Lock()
	e.cancel = cancel
	e.mu.Unlock()
	defer cancel()

	t := e.tick
	if t <= 0 {
		t = 50 * time.Millisecond
	}
	ticker := time.NewTicker(t)
	defer ticker.Stop()

	e.emit("working", "")
	for {
		select {
		case <-cctx.Done():
			return
		case <-ticker.C:
			if e.step() {
				return // terminal
			}
		}
	}
}

// step performs one evaluation tick. It returns true when the execution is terminal
// (target met, collar hit, or stopped).
func (e *execution) step() bool {
	e.mu.Lock()
	if e.stopped {
		e.mu.Unlock()
		return true
	}
	remaining := e.p.target - e.filled
	if remaining <= 0 {
		e.mu.Unlock()
		e.finish("done")
		return true
	}
	// A cancel is in flight: do NOTHING until the child's terminal event clears it.
	// This is the cancel-before-replace barrier that guarantees one live child.
	if e.pendingCancel {
		e.mu.Unlock()
		return false
	}
	haveChild := e.childID != ""
	lastPrice := e.lastPrice
	lastQuoteAt := e.lastQuoteAt
	e.mu.Unlock()

	book, ok := e.book.OrderBook(e.p.code)
	if !ok {
		return false // no book this tick; wait
	}
	dec := decide(book, e.p, haveChild, lastPrice, lastQuoteAt, e.now())
	switch dec.action {
	case decisionNoBook, decisionHold:
		return false
	case decisionCollar:
		e.finishCollar()
		return true
	case decisionQuote:
		if haveChild {
			// Price moved: cancel the current child and WAIT. The next tick, after the
			// cancel confirms (childID cleared), places the fresh quote. Never two live.
			e.cancelForRequote()
		} else {
			e.placeNew(dec.price, remaining)
		}
		return false
	}
	return false
}

// placeNew places ONE fresh maker limit at price (our own side's touch, never
// crossing) for the remaining quantity. Caller guarantees there is no live child and
// no pending cancel. A hard placement backstop stops the execution if it ever exceeds
// maxChildPlacements (defence against any future runaway).
func (e *execution) placeNew(price float64, remaining int64) {
	e.mu.Lock()
	if e.childID != "" || e.pendingCancel || e.stopped {
		e.mu.Unlock()
		return // invariant: never place while a child is live or a cancel is in flight
	}
	// Rate-limit re-places: after the first quote, never place again faster than
	// minRequote. The first ever quote (lastQuoteAt zero) always passes.
	if !e.lastQuoteAt.IsZero() && e.p.minRequote > 0 && e.now().Sub(e.lastQuoteAt) < e.p.minRequote {
		e.mu.Unlock()
		return
	}
	if e.placedCount >= maxChildPlacements {
		e.mu.Unlock()
		e.logf("exec %s: placement backstop hit (%d) — stopping", e.p.clientID, maxChildPlacements)
		e.stop("placement_backstop")
		return
	}
	e.placedCount++
	e.mu.Unlock()

	childID, err := e.placer.placeChild(e.p.clientID, e.p.code, e.p.buy, price, remaining)
	if err != nil {
		e.logf("exec %s: placeChild failed: %v", e.p.clientID, err)
		return
	}
	e.mu.Lock()
	e.childID = childID
	e.haveQuote = true
	e.lastPrice = price
	e.lastQuoteAt = e.now()
	e.mu.Unlock()
}

// cancelForRequote cancels the current child and sets pendingCancel so the loop waits
// for the terminal event before placing the next quote (cancel-before-replace). Idempotent.
func (e *execution) cancelForRequote() {
	e.mu.Lock()
	old := e.childID
	if old == "" || e.pendingCancel || e.stopped {
		e.mu.Unlock()
		return
	}
	e.pendingCancel = true
	e.mu.Unlock()
	e.placer.cancelChild(old)
}

// onTrade accumulates a partial fill toward target and emits progress. Called by the
// manager when a child order trades.
func (e *execution) onTrade(qty int64, price float64) {
	if qty <= 0 {
		return
	}
	e.mu.Lock()
	e.filled += qty
	e.sumPxQty += price * float64(qty)
	full := e.filled >= e.p.target
	e.mu.Unlock()
	e.emit("working", "")
	if full {
		e.finish("done")
	}
}

// onOrderEvent reacts to a child order becoming terminal: if the child was cancelled
// or filled, drop our quote handle so the next tick re-quotes the remainder.
func (e *execution) onOrderEvent(wo *workingOrder) {
	if wo == nil {
		return
	}
	e.mu.Lock()
	// Only the CURRENT child drives state. A terminal event for a previous child (a
	// lagging cancel confirmation) must NOT reset the quote — that was the runaway bug.
	if wo.clientID != e.childID {
		e.mu.Unlock()
		return
	}
	if wo.done {
		e.childID = ""
		e.pendingCancel = false
		e.haveQuote = false
	}
	e.mu.Unlock()
}

// stop halts the loop, cancels any resting child, and emits a terminal update. Called
// by StopExecution and KillSwitch.
func (e *execution) stop(reason string) {
	e.mu.Lock()
	if e.stopped {
		e.mu.Unlock()
		return
	}
	e.stopped = true
	e.stopReason = reason
	old := e.childID
	e.childID = ""
	e.haveQuote = false
	cancel := e.cancel
	e.mu.Unlock()
	if old != "" {
		e.placer.cancelChild(old)
	}
	if cancel != nil {
		cancel()
	}
	e.emit("stopped", reason)
}

func (e *execution) finish(state string) {
	e.mu.Lock()
	if e.stopped {
		e.mu.Unlock()
		return
	}
	e.stopped = true
	old := e.childID
	e.childID = ""
	cancel := e.cancel
	e.mu.Unlock()
	if old != "" && state != "done" {
		e.placer.cancelChild(old)
	}
	if cancel != nil {
		cancel()
	}
	e.emit(state, "")
}

func (e *execution) finishCollar() {
	e.mu.Lock()
	if e.stopped {
		e.mu.Unlock()
		return
	}
	e.stopped = true
	old := e.childID
	e.childID = ""
	e.haveQuote = false
	cancel := e.cancel
	e.mu.Unlock()
	if old != "" {
		e.placer.cancelChild(old) // cancel the remainder; do not chase
	}
	if cancel != nil {
		cancel()
	}
	e.emit("collar_hit", "worst_price breached")
}

func (e *execution) emit(state, text string) {
	e.mu.Lock()
	filled := e.filled
	var avg float64
	if filled > 0 {
		avg = e.sumPxQty / float64(filled)
	}
	e.mu.Unlock()
	e.placer.emitExec(&quikv1.ExecutionUpdate{
		ClientId: e.p.clientID,
		Code:     e.p.code,
		Target:   e.p.target,
		Filled:   filled,
		AvgPrice: avg,
		State:    state,
		Text:     text,
		TsUnixMs: e.placer.nowMs(),
	})
}

// Filled / AvgPrice expose progress for tests.
func (e *execution) Filled() int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.filled
}

func (e *execution) AvgPrice() float64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.filled == 0 {
		return 0
	}
	return e.sumPxQty / float64(e.filled)
}
