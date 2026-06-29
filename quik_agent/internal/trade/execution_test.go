package trade

import (
	"testing"
	"time"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

func book(bids, asks [][2]float64) quikdde.Book {
	b := quikdde.Book{Code: "RIU6"}
	for _, lv := range bids {
		b.Bids = append(b.Bids, quikdde.BookLevel{Price: lv[0], Quantity: int64(lv[1])})
	}
	for _, lv := range asks {
		b.Asks = append(b.Asks, quikdde.BookLevel{Price: lv[0], Quantity: int64(lv[1])})
	}
	return b
}

// bestbid 99990, bestask 100010, step 10.
func sampleBook() quikdde.Book {
	return book(
		[][2]float64{{99990, 5}, {99980, 7}},
		[][2]float64{{100010, 4}, {100020, 6}},
	)
}

func makerBuy() makerParams {
	return makerParams{
		clientID:   "p1",
		code:       "RIU6",
		buy:        true,
		target:     3,
		worstPrice: 100200, // generous collar
		priceStep:  10,
		minRequote: 200 * time.Millisecond,
	}
}

func TestDecideJoinNotCross(t *testing.T) {
	p := makerBuy()
	t0 := time.Unix(0, 0)
	// No quote yet -> place at OUR side's best (best bid 99990), never the ask.
	dec := decide(sampleBook(), p, false, 0, t0, t0)
	if dec.action != decisionQuote {
		t.Fatalf("action = %v, want quote", dec.action)
	}
	if dec.price != 99990 {
		t.Fatalf("join price = %v, want 99990 (best bid, not ask)", dec.price)
	}

	// SELL joins the best ask, not the bid.
	ps := p
	ps.buy = false
	ps.worstPrice = 99800
	dec = decide(sampleBook(), ps, false, 0, t0, t0)
	if dec.action != decisionQuote || dec.price != 100010 {
		t.Fatalf("sell join = %+v, want quote @100010 (best ask)", dec)
	}
}

func TestDecideRequoteThreshold(t *testing.T) {
	p := makerBuy()
	t0 := time.Unix(100, 0)

	// Resting at 99990; book unchanged -> hold.
	dec := decide(sampleBook(), p, true, 99990, t0, t0.Add(time.Second))
	if dec.action != decisionHold {
		t.Fatalf("unchanged book action = %v, want hold", dec.action)
	}

	// Touch moves up by exactly one step (99990->100000) after the window elapsed.
	moved := book([][2]float64{{100000, 5}}, [][2]float64{{100010, 4}})
	dec = decide(moved, p, true, 99990, t0, t0.Add(time.Second))
	if dec.action != decisionQuote || dec.price != 100000 {
		t.Fatalf("one-step move = %+v, want requote @100000", dec)
	}

	// Same one-step move but BEFORE the anti-flicker window -> hold.
	dec = decide(moved, p, true, 99990, t0, t0.Add(100*time.Millisecond))
	if dec.action != decisionHold {
		t.Fatalf("within flicker window action = %v, want hold", dec.action)
	}

	// Sub-step move (< one step) after the window -> still hold.
	tiny := book([][2]float64{{99995, 5}}, [][2]float64{{100010, 4}})
	dec = decide(tiny, p, true, 99990, t0, t0.Add(time.Second))
	if dec.action != decisionHold {
		t.Fatalf("sub-step move action = %v, want hold", dec.action)
	}
}

func TestDecideCollarStop(t *testing.T) {
	p := makerBuy()
	p.worstPrice = 100000 // tight collar
	t0 := time.Unix(0, 0)

	// Best bid climbs to 100010, beyond the 100000 buy collar -> collar stop.
	hot := book([][2]float64{{100010, 5}}, [][2]float64{{100020, 4}})
	dec := decide(hot, p, true, 99990, t0, t0.Add(time.Second))
	if dec.action != decisionCollar || !dec.collarHit {
		t.Fatalf("collar decision = %+v, want collar stop", dec)
	}

	// SELL collar: best ask falls below the worst (lower) bound -> stop.
	ps := makerBuy()
	ps.buy = false
	ps.worstPrice = 100000
	cold := book([][2]float64{{99980, 5}}, [][2]float64{{99990, 4}})
	dec = decide(cold, ps, false, 0, t0, t0)
	if dec.action != decisionCollar {
		t.Fatalf("sell collar = %+v, want collar stop", dec)
	}
}

func TestDecideNoBook(t *testing.T) {
	p := makerBuy()
	t0 := time.Unix(0, 0)
	// Empty bid side for a BUY -> no usable join.
	empty := book(nil, [][2]float64{{100010, 4}})
	dec := decide(empty, p, false, 0, t0, t0)
	if dec.action != decisionNoBook {
		t.Fatalf("action = %v, want no-book", dec.action)
	}
}

// fakePlacer records child placements and exec updates for accumulation tests.
type fakePlacer struct {
	placed  []float64
	cancels int
	updates []*quikv1.ExecutionUpdate
	nextID  int
}

func (f *fakePlacer) placeChild(_, _ string, _ bool, price float64, _ int64) (string, error) {
	f.nextID++
	f.placed = append(f.placed, price)
	return "child", nil
}
func (f *fakePlacer) cancelChild(string)                  { f.cancels++ }
func (f *fakePlacer) emitExec(u *quikv1.ExecutionUpdate)  { f.updates = append(f.updates, u) }
func (f *fakePlacer) nowMs() int64                        { return 0 }

func TestPartialAccumulation(t *testing.T) {
	fp := &fakePlacer{}
	e := &execution{
		p:      makerBuy(), // target 3
		placer: fp,
		logf:   func(string, ...any) {},
	}
	e.onTrade(1, 99990)
	if e.Filled() != 1 {
		t.Fatalf("filled = %d, want 1", e.Filled())
	}
	e.onTrade(2, 100000)
	if e.Filled() != 3 {
		t.Fatalf("filled = %d, want 3 (target reached)", e.Filled())
	}
	// avg = (99990*1 + 100000*2) / 3
	wantAvg := (99990.0*1 + 100000.0*2) / 3.0
	if got := e.AvgPrice(); got != wantAvg {
		t.Fatalf("avg = %v, want %v", got, wantAvg)
	}
	// Reaching target emits a terminal "done".
	if len(fp.updates) == 0 {
		t.Fatalf("expected an ExecutionUpdate on completion")
	}
	last := fp.updates[len(fp.updates)-1]
	if last.GetState() != "done" {
		t.Fatalf("final state = %q, want done", last.GetState())
	}
	if last.GetFilled() != 3 {
		t.Fatalf("final filled = %d, want 3", last.GetFilled())
	}
}

func TestRequotePlacesAndCancels(t *testing.T) {
	fp := &fakePlacer{}
	e := &execution{p: makerBuy(), placer: fp, logf: func(string, ...any) {}}

	e.requote(99990, 3)
	if len(fp.placed) != 1 || fp.placed[0] != 99990 {
		t.Fatalf("first requote placed = %v, want [99990]", fp.placed)
	}
	if fp.cancels != 0 {
		t.Fatalf("first requote should not cancel, got %d", fp.cancels)
	}
	// Second requote cancels the resting child then places the new price.
	e.requote(100000, 3)
	if fp.cancels != 1 {
		t.Fatalf("second requote cancels = %d, want 1", fp.cancels)
	}
	if len(fp.placed) != 2 || fp.placed[1] != 100000 {
		t.Fatalf("second requote placed = %v, want last 100000", fp.placed)
	}
}

func TestStopCancelsAndEmits(t *testing.T) {
	fp := &fakePlacer{}
	e := &execution{p: makerBuy(), placer: fp, logf: func(string, ...any) {}}
	e.requote(99990, 3)
	e.stop("operator")
	if fp.cancels != 1 {
		t.Fatalf("stop should cancel resting child, cancels=%d", fp.cancels)
	}
	if len(fp.updates) == 0 || fp.updates[len(fp.updates)-1].GetState() != "stopped" {
		t.Fatalf("stop should emit stopped update, got %v", fp.updates)
	}
	// Idempotent.
	e.stop("again")
	if fp.cancels != 1 {
		t.Fatalf("second stop must be a no-op, cancels=%d", fp.cancels)
	}
}
