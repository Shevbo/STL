package trade

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

// Emitter sends agent->STL frames. The link implements it (one method per Phase 2
// frame) so the manager never imports the link/stream code and stays unit-testable
// with a fake. All methods are best-effort; a send error is logged by the caller.
type Emitter interface {
	EmitOrderUpdate(*quikv1.OrderUpdate) error
	EmitTransReply(*quikv1.TransReply) error
	EmitExecutionUpdate(*quikv1.ExecutionUpdate) error
}

// bridgeAPI is the slice of *Bridge the manager needs. It lets execution.go and tests
// substitute a fake bridge.
type bridgeAPI interface {
	NextTransID() int64
	Place(p placeCmd) error
	Cancel(c cancelCmd) error
	Connected() bool
}

// workingOrder is one order the agent has sent to QUIK and not yet seen fully done.
type workingOrder struct {
	clientID string
	transID  int64
	orderNum string // assigned once QUIK reports it (trans_reply / order)
	code     string
	side     quikv1.Side
	price    float64
	qty      int64 // original quantity
	filled   int64
	balance  int64 // unfilled remainder reported by QUIK
	state    quikv1.OrderState
	done     bool // terminal (filled/cancelled/rejected)
}

func (w *workingOrder) restingQty() int64 {
	if w.done {
		return 0
	}
	if w.balance > 0 {
		return w.balance
	}
	// Before the first order event, the whole order is considered resting.
	return w.qty - w.filled
}

// ManagerConfig carries the static placement fields the manager needs but does not
// decide: class code and the trade account. Both come from config / keymaster, never
// hardcoded here.
type ManagerConfig struct {
	ClassCode string // e.g. SPBFUT
	Account   string // trade account (read from the configured env var by the caller)
}

// Manager is the order manager. It handles PlaceOrder/CancelOrder/KillSwitch, tracks
// working orders, enforces the hard limits via Guard, and translates Lua events into
// OrderUpdate/TransReply emitted to STL. Guard 3: nothing reaches the bridge unless an
// explicit command passed every limit AND the master flag is on.
type Manager struct {
	cfg    ManagerConfig
	bridge bridgeAPI
	guard  *Guard
	emit    Emitter
	logf    func(string, ...any)
	nowMsFn func() int64

	// book + priceStep feed the 1b maker loop's LOCAL order book. Set after
	// construction via SetBookSource; nil means executions cannot start.
	book      BookSource
	execCtx   context.Context
	execTick  time.Duration

	mu      sync.Mutex
	blocked bool // set by KillSwitch; new placements rejected until cleared

	// indexes into the same workingOrder
	byClient map[string]*workingOrder
	byTrans  map[int64]*workingOrder
	byOrder  map[string]*workingOrder

	// exec holds running maker executions keyed by parent client_id (1b).
	exec map[string]*execution
}

// NewManager builds the order manager. emit and bridge must be non-nil in production;
// tests may pass fakes. logf may be nil.
func NewManager(cfg ManagerConfig, bridge bridgeAPI, guard *Guard, emit Emitter, logf func(string, ...any)) *Manager {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	return &Manager{
		cfg:      cfg,
		bridge:   bridge,
		guard:    guard,
		emit:     emit,
		logf:     logf,
		nowMsFn:  func() int64 { return time.Now().UnixMilli() },
		execTick: 50 * time.Millisecond,
		byClient: map[string]*workingOrder{},
		byTrans:  map[int64]*workingOrder{},
		byOrder:  map[string]*workingOrder{},
		exec:     map[string]*execution{},
	}
}

// SetBookSource wires the LOCAL order book used by the 1b maker loop and the parent
// context the loops run under (cancelled on shutdown). Until set, StartExecution is
// rejected. *quikdde.Provider satisfies BookSource.
func (m *Manager) SetBookSource(ctx context.Context, book BookSource) {
	m.mu.Lock()
	m.book = book
	m.execCtx = ctx
	m.mu.Unlock()
}

// ---- STL command handlers ----

// PlaceOrder handles a STL PlaceOrder. It enforces the master flag, the kill-switch
// block, and every hard limit BEFORE touching the bridge. On any violation it emits an
// OrderUpdate REJECTED and sends nothing to Lua.
func (m *Manager) PlaceOrder(req *quikv1.PlaceOrder) {
	if req == nil {
		return
	}
	m.mu.Lock()
	blocked := m.blocked
	working := m.totalWorkingLocked()
	m.mu.Unlock()

	if blocked {
		m.rejectPlace(req.GetClientId(), req.GetCode(), req.GetSide(), req.GetPrice(), req.GetQuantity(), ReasonBlocked)
		return
	}

	ok, reason := m.guard.CheckPlace(PlaceCheck{
		Code:           req.GetCode(),
		Price:          req.GetPrice(),
		Quantity:       req.GetQuantity(),
		CurrentWorking: working,
	})
	if !ok {
		m.rejectPlace(req.GetClientId(), req.GetCode(), req.GetSide(), req.GetPrice(), req.GetQuantity(), reason)
		return
	}

	// Reserve the daily-cap slot only now (atomic with the send decision).
	if ok, reason := m.guard.CommitPlace(); !ok {
		m.rejectPlace(req.GetClientId(), req.GetCode(), req.GetSide(), req.GetPrice(), req.GetQuantity(), reason)
		return
	}

	transID := m.bridge.NextTransID()
	wo := &workingOrder{
		clientID: req.GetClientId(),
		transID:  transID,
		code:     req.GetCode(),
		side:     req.GetSide(),
		price:    req.GetPrice(),
		qty:      req.GetQuantity(),
		balance:  req.GetQuantity(),
		state:    quikv1.OrderState_ORDER_STATE_PENDING,
	}
	m.mu.Lock()
	m.byClient[wo.clientID] = wo
	m.byTrans[transID] = wo
	m.mu.Unlock()

	// PENDING: sent to QUIK, awaiting reply.
	m.emitOrderUpdate(wo, "")

	cmd := placeCmd{
		TransID:  transID,
		ClientID: req.GetClientId(),
		Class:    m.cfg.ClassCode,
		Sec:      req.GetCode(),
		Op:       opFromSide(req.GetSide()),
		Price:    formatPrice(req.GetPrice()),
		Qty:      req.GetQuantity(),
		Account:  m.cfg.Account,
	}
	if err := m.bridge.Place(cmd); err != nil {
		m.logf("trade: place send failed (trans=%d): %v", transID, err)
		m.mu.Lock()
		wo.state = quikv1.OrderState_ORDER_STATE_REJECTED
		wo.done = true
		m.mu.Unlock()
		m.emitOrderUpdate(wo, "bridge send failed: "+err.Error())
	}
}

// CancelOrder handles a STL CancelOrder. It resolves order_num via the explicit field
// or the client_id mapping. With nothing to cancel it is a no-op (idempotent). Cancel
// is allowed even when blocked (a kill-switch needs to cancel).
func (m *Manager) CancelOrder(req *quikv1.CancelOrder) {
	if req == nil {
		return
	}
	wo := m.resolveForCancel(req.GetClientId(), req.GetOrderId())
	if wo == nil {
		m.logf("trade: cancel for unknown order (client=%q order=%q)", req.GetClientId(), req.GetOrderId())
		return
	}
	m.sendCancel(wo)
}

// KillSwitch cancels ALL working orders, stops every running execution, and sets the
// blocked flag so new placements are rejected until explicitly cleared. Guard 3.
func (m *Manager) KillSwitch(req *quikv1.KillSwitch) {
	reason := ""
	if req != nil {
		reason = req.GetReason()
	}
	m.mu.Lock()
	m.blocked = true
	var toCancel []*workingOrder
	for _, wo := range m.byClient {
		if !wo.done {
			toCancel = append(toCancel, wo)
		}
	}
	var execs []*execution
	for _, e := range m.exec {
		execs = append(execs, e)
	}
	m.mu.Unlock()

	for _, e := range execs {
		e.stop("killswitch")
	}
	for _, wo := range toCancel {
		m.sendCancel(wo)
	}
	m.logf("trade: KILL-SWITCH engaged (reason=%q): cancelled %d working, blocked new placements", reason, len(toCancel))
}

// ClearBlock lifts the kill-switch block so placements are accepted again. There is no
// proto message for this in Slice 1; the operator clears it out-of-band (or a future
// command wires here). Exposed so the wiring/tests can re-enable trading.
func (m *Manager) ClearBlock() {
	m.mu.Lock()
	m.blocked = false
	m.mu.Unlock()
	m.logf("trade: kill-switch block cleared; placements accepted")
}

// Blocked reports whether the kill-switch block is engaged.
func (m *Manager) Blocked() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.blocked
}

// ---- 1b maker execution ----

// StartExecution begins passively working target_quantity near the touch (maker only,
// never crossing) until target is reached or worst_price (collar) is breached. It is
// gated by the master flag + kill-switch block + whitelist, exactly like a placement.
// The loop runs in its own goroutine and reads the LOCAL order book each tick.
func (m *Manager) StartExecution(req *quikv1.StartExecution) {
	if req == nil {
		return
	}
	clientID := req.GetClientId()
	m.mu.Lock()
	blocked := m.blocked
	book := m.book
	ctx := m.execCtx
	tick := m.execTick
	_, already := m.exec[clientID]
	m.mu.Unlock()

	if blocked {
		m.emitExecReject(clientID, req.GetCode(), ReasonBlocked)
		return
	}
	if !m.guard.Limits().TradingEnabled {
		m.emitExecReject(clientID, req.GetCode(), ReasonTradingDisabled)
		return
	}
	if !m.guard.Limits().whitelisted(req.GetCode()) {
		m.emitExecReject(clientID, req.GetCode(), ReasonNotWhitelisted)
		return
	}
	if req.GetTargetQuantity() <= 0 {
		m.emitExecReject(clientID, req.GetCode(), ReasonQtyNonPositive)
		return
	}
	if book == nil || ctx == nil {
		m.emitExecReject(clientID, req.GetCode(), "no local order book wired")
		return
	}
	if already {
		m.logf("trade: StartExecution ignored — already running for client=%q", clientID)
		return
	}

	buy := isBuy(req.GetSide())
	e := &execution{
		p: makerParams{
			clientID:   clientID,
			code:       req.GetCode(),
			buy:        buy,
			target:     req.GetTargetQuantity(),
			worstPrice: req.GetWorstPrice(),
			allowCross: req.GetAllowCross(),
			priceStep:  m.priceStepFor(req.GetCode()),
			minRequote: 200 * time.Millisecond,
		},
		book:   book,
		placer: m,
		logf:   m.logf,
		tick:   tick,
	}
	m.mu.Lock()
	m.exec[clientID] = e
	m.mu.Unlock()
	m.logf("trade: StartExecution client=%q code=%q target=%d worst=%.4f (maker, allow_cross=%v)",
		clientID, req.GetCode(), req.GetTargetQuantity(), req.GetWorstPrice(), req.GetAllowCross())
	go func() {
		e.run(ctx)
		m.mu.Lock()
		delete(m.exec, clientID)
		m.mu.Unlock()
	}()
}

// StopExecution stops a running maker execution by client_id (idempotent).
func (m *Manager) StopExecution(req *quikv1.StopExecution) {
	if req == nil {
		return
	}
	m.mu.Lock()
	e := m.exec[req.GetClientId()]
	m.mu.Unlock()
	if e == nil {
		return
	}
	e.stop("stop_execution")
}

// priceStepFor looks up one price step for code from the local book (smallest gap
// between adjacent ask levels, else bid levels). Falls back to 0, which disables the
// re-quote threshold (any move re-quotes). The loop never crosses regardless.
func (m *Manager) priceStepFor(code string) float64 {
	m.mu.Lock()
	book := m.book
	m.mu.Unlock()
	if book == nil {
		return 0
	}
	b, ok := book.OrderBook(code)
	if !ok {
		return 0
	}
	if s := minGap(b.Asks); s > 0 {
		return s
	}
	return minGap(b.Bids)
}

func minGap(levels []quikdde.BookLevel) float64 {
	best := 0.0
	for i := 1; i < len(levels); i++ {
		g := levels[i].Price - levels[i-1].Price
		if g < 0 {
			g = -g
		}
		if g > 0 && (best == 0 || g < best) {
			best = g
		}
	}
	return best
}

// ---- execPlacer (drives child orders for the maker loop) ----

// placeChild submits one maker limit for the execution's remaining quantity. It reuses
// the full PlaceOrder path so every hard limit (incl. per-order qty and working cap)
// still applies to each child quote. The child's client_id namespaces the parent's so
// updates correlate but do not collide.
func (m *Manager) placeChild(parentClientID, code string, buy bool, price float64, qty int64) (string, error) {
	if m.guard.Limits().MaxContractsPerOrder > 0 && qty > m.guard.Limits().MaxContractsPerOrder {
		qty = m.guard.Limits().MaxContractsPerOrder // slice down to the per-order cap
	}
	childID := fmt.Sprintf("%s#%d", parentClientID, m.bridge.NextTransID())
	side := quikv1.Side_SIDE_BUY
	if !buy {
		side = quikv1.Side_SIDE_SELL
	}
	// Route through PlaceOrder so limits + tracking + OrderUpdate all happen. Map the
	// child back to the parent execution so its order/trade events feed the loop.
	m.mu.Lock()
	parent := m.exec[parentClientID]
	m.mu.Unlock()
	m.PlaceOrder(&quikv1.PlaceOrder{
		ClientId: childID,
		Code:     code,
		Side:     side,
		Price:    price,
		Quantity: qty,
	})
	if parent != nil {
		m.mu.Lock()
		m.exec[childID] = parent // child events route to the parent execution
		m.mu.Unlock()
	}
	return childID, nil
}

// cancelChild cancels a child order placed by the maker loop.
func (m *Manager) cancelChild(childID string) {
	m.CancelOrder(&quikv1.CancelOrder{ClientId: childID})
}

// emitExec forwards an ExecutionUpdate to STL.
func (m *Manager) emitExec(u *quikv1.ExecutionUpdate) {
	_ = m.emit.EmitExecutionUpdate(u)
}

// nowMs is the wall clock in unix ms (also satisfies execPlacer.nowMs).
func (m *Manager) nowMs() int64 {
	if m.nowMsFn != nil {
		return m.nowMsFn()
	}
	return time.Now().UnixMilli()
}

func (m *Manager) emitExecReject(clientID, code string, reason RejectReason) {
	m.logf("trade: StartExecution REJECTED (client=%q code=%q): %s", clientID, code, reason)
	_ = m.emit.EmitExecutionUpdate(&quikv1.ExecutionUpdate{
		ClientId: clientID,
		Code:     code,
		State:    "rejected",
		Text:     string(reason),
		TsUnixMs: m.nowMs(),
	})
}

// sendCancel issues a KILL_ORDER for a working order (needs an order_num from QUIK).
func (m *Manager) sendCancel(wo *workingOrder) {
	m.mu.Lock()
	orderNum := wo.orderNum
	code := wo.code
	m.mu.Unlock()
	if orderNum == "" {
		// QUIK has not yet acknowledged the order with a number; cannot KILL_ORDER
		// without ORDER_KEY. The trans_reply / order event will land soon; the
		// operator can retry, or the maker loop re-evaluates on the next tick.
		m.logf("trade: cancel deferred — no order_num yet (client=%q trans=%d)", wo.clientID, wo.transID)
		return
	}
	transID := m.bridge.NextTransID()
	if err := m.bridge.Cancel(cancelCmd{
		TransID:  transID,
		OrderNum: orderNum,
		Class:    m.cfg.ClassCode,
		Sec:      code,
	}); err != nil {
		m.logf("trade: cancel send failed (order=%s): %v", orderNum, err)
	}
}

func (m *Manager) resolveForCancel(clientID, orderID string) *workingOrder {
	m.mu.Lock()
	defer m.mu.Unlock()
	if orderID != "" {
		if wo := m.byOrder[orderID]; wo != nil {
			return wo
		}
	}
	if clientID != "" {
		if wo := m.byClient[clientID]; wo != nil {
			return wo
		}
	}
	return nil
}

// ---- Lua event handling (BridgeHandler) ----

// OnTransReply maps a QUIK OnTransReply to a TransReply frame and, on a non-zero
// result code, marks the order rejected. result_code 0 = accepted by QUIK.
func (m *Manager) OnTransReply(ev TransReplyEvent) {
	m.mu.Lock()
	wo := m.byTrans[ev.TransID]
	if wo != nil && ev.OrderNum != "" && wo.orderNum == "" {
		wo.orderNum = ev.OrderNum
		m.byOrder[ev.OrderNum] = wo
	}
	clientID := ""
	if wo != nil {
		clientID = wo.clientID
	}
	rejected := wo != nil && ev.ResultCode != 0
	if rejected {
		wo.state = quikv1.OrderState_ORDER_STATE_REJECTED
		wo.done = true
	}
	m.mu.Unlock()

	_ = m.emit.EmitTransReply(&quikv1.TransReply{
		ClientId:   clientID,
		TransId:    ev.TransID,
		ResultCode: ev.ResultCode,
		Text:       ev.Text,
		TsUnixMs:   m.nowMs(),
	})
	if rejected {
		m.emitOrderUpdate(wo, ev.Text)
	}
}

// OnOrder maps a QUIK OnOrder lifecycle update to an OrderUpdate. QUIK's balance is
// the unfilled remainder; filled = qty - balance. State maps active->ACTIVE,
// filled->FILLED, cancelled->CANCELLED, rejected->REJECTED; a partially filled active
// order surfaces as PARTIAL.
func (m *Manager) OnOrder(ev OrderEvent) {
	m.mu.Lock()
	wo := m.lookupLocked(ev.OrderNum, ev.TransID)
	if wo == nil {
		m.mu.Unlock()
		m.logf("trade: order event for untracked order (num=%s trans=%d)", ev.OrderNum, ev.TransID)
		return
	}
	if ev.OrderNum != "" && wo.orderNum == "" {
		wo.orderNum = ev.OrderNum
		m.byOrder[ev.OrderNum] = wo
	}
	if ev.Qty > 0 {
		wo.qty = ev.Qty
	}
	wo.balance = ev.Balance
	wo.filled = wo.qty - ev.Balance
	if wo.filled < 0 {
		wo.filled = 0
	}
	wo.state = mapOrderState(ev.State, wo.filled, wo.balance)
	switch wo.state {
	case quikv1.OrderState_ORDER_STATE_FILLED,
		quikv1.OrderState_ORDER_STATE_CANCELLED,
		quikv1.OrderState_ORDER_STATE_REJECTED:
		wo.done = true
	}
	clientID := wo.clientID
	ex := m.exec[clientID]
	m.mu.Unlock()

	m.emitOrderUpdate(wo, ev.Text)
	if ex != nil {
		ex.onOrderEvent(wo)
	}
}

// OnTrade records a fill. QUIK also emits an OnOrder with the new balance, so the
// authoritative filled count comes from OnOrder; OnTrade is used to feed avg price to
// the maker loop and as a fast partial signal.
func (m *Manager) OnTrade(ev TradeEvent) {
	m.mu.Lock()
	wo := m.byOrder[ev.OrderNum]
	var ex *execution
	if wo != nil {
		ex = m.exec[wo.clientID]
	}
	m.mu.Unlock()
	if ex != nil {
		if px, ok := parsePrice(ev.Price); ok {
			ex.onTrade(ev.Qty, px)
		}
	}
}

func (m *Manager) lookupLocked(orderNum string, transID int64) *workingOrder {
	if orderNum != "" {
		if wo := m.byOrder[orderNum]; wo != nil {
			return wo
		}
	}
	if transID != 0 {
		if wo := m.byTrans[transID]; wo != nil {
			return wo
		}
	}
	return nil
}

// totalWorkingLocked sums resting quantity across all non-terminal orders. Caller
// holds m.mu.
func (m *Manager) totalWorkingLocked() int64 {
	var sum int64
	for _, wo := range m.byClient {
		sum += wo.restingQty()
	}
	return sum
}

// ---- emit helpers ----

func (m *Manager) rejectPlace(clientID, code string, side quikv1.Side, price float64, qty int64, reason RejectReason) {
	m.logf("trade: PlaceOrder REJECTED (client=%q code=%q): %s", clientID, code, reason)
	_ = m.emit.EmitOrderUpdate(&quikv1.OrderUpdate{
		ClientId: clientID,
		Code:     code,
		Side:     side,
		State:    quikv1.OrderState_ORDER_STATE_REJECTED,
		Price:    price,
		Quantity: qty,
		Filled:   0,
		Text:     string(reason),
		TsUnixMs: m.nowMs(),
	})
}

func (m *Manager) emitOrderUpdate(wo *workingOrder, text string) {
	m.mu.Lock()
	upd := &quikv1.OrderUpdate{
		ClientId: wo.clientID,
		OrderId:  wo.orderNum,
		Code:     wo.code,
		Side:     wo.side,
		State:    wo.state,
		Price:    wo.price,
		Quantity: wo.qty,
		Filled:   wo.filled,
		Text:     text,
		TsUnixMs: m.nowMs(),
	}
	m.mu.Unlock()
	_ = m.emit.EmitOrderUpdate(upd)
}

// ---- pure helpers ----

func opFromSide(s quikv1.Side) string {
	if s == quikv1.Side_SIDE_SELL {
		return "S"
	}
	return "B"
}

func isBuy(s quikv1.Side) bool { return s != quikv1.Side_SIDE_SELL }

// formatPrice renders a price for the QUIK transaction PRICE field. QUIK accepts a
// dot-decimal string; trailing zeros are trimmed but at least one decimal is kept for
// fractional prices. Integers (e.g. RI index futures) render without a decimal point.
func formatPrice(p float64) string {
	s := strconv.FormatFloat(p, 'f', -1, 64)
	return s
}

func parsePrice(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	s = strings.ReplaceAll(s, ",", ".")
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// mapOrderState maps the Lua state string + fill counters to the proto OrderState.
func mapOrderState(state string, filled, balance int64) quikv1.OrderState {
	switch strings.ToLower(strings.TrimSpace(state)) {
	case "filled":
		return quikv1.OrderState_ORDER_STATE_FILLED
	case "cancelled", "canceled":
		return quikv1.OrderState_ORDER_STATE_CANCELLED
	case "rejected":
		return quikv1.OrderState_ORDER_STATE_REJECTED
	case "active":
		if filled > 0 && balance > 0 {
			return quikv1.OrderState_ORDER_STATE_PARTIAL
		}
		return quikv1.OrderState_ORDER_STATE_ACTIVE
	default:
		if filled > 0 && balance > 0 {
			return quikv1.OrderState_ORDER_STATE_PARTIAL
		}
		return quikv1.OrderState_ORDER_STATE_ACTIVE
	}
}
