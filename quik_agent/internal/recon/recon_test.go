package recon

import (
	"reflect"
	"strings"
	"testing"
)

// ---- brief's verbatim test ----

func TestEvaluateOrphanOrder(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6"}},
		Acc: AccView{Orders: []Order{{Num: "555", Sec: "RIU6", Active: true}},
			PosAgeMs: 1000, OrdAgeMs: 1000},
		NowMs: 1,
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" || rep.Plan == nil {
		t.Fatalf("%+v", rep)
	}
	if rep.Plan.Steps[0].Kind != "cancel_order" || rep.Plan.Steps[0].OrderNum != "555" {
		t.Fatalf("%+v", rep.Plan.Steps)
	}
}

// ---- all-green OK ----

func TestEvaluateAllGreenOK(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: 2, OrderNums: []string{"1"}}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 2}},
			Orders:    []Order{{Num: "1", Sec: "RIU6", Active: true}},
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("%+v", rep)
	}
}

// ---- STALE gating ----

func TestEvaluateStaleTables(t *testing.T) {
	cases := []struct {
		name               string
		posAge, ordAge     int64
	}{
		{"pos age over threshold", 30_001, 100},
		{"ord age over threshold", 100, 30_001},
		{"both over threshold", 40_000, 40_000},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			in := Inputs{
				// A clear mismatch (orphan order) so we can prove checks still render
				// with real content even though the overall State is forced STALE.
				Robots: []RobotView{{ID: "r1", Symbol: "RIU6"}},
				Acc: AccView{
					Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true}},
					PosAgeMs: tc.posAge, OrdAgeMs: tc.ordAge,
				},
			}
			rep := Evaluate(in)
			if rep.State != "STALE" {
				t.Fatalf("state = %q, want STALE", rep.State)
			}
			if rep.Plan != nil {
				t.Fatalf("plan must be nil when STALE, got %+v", rep.Plan)
			}
			if len(rep.Orders) != 1 || rep.Orders[0].OrderNum != "555" || rep.Orders[0].Owner != "ORPHAN" {
				t.Fatalf("orders must still render with real data even when STALE, got %+v", rep.Orders)
			}
			if len(rep.Positions) != 1 {
				t.Fatalf("positions must still render even when STALE, got %+v", rep.Positions)
			}
		})
	}
}

// ---- MISSING order -> fix_state ----

func TestEvaluateMissingOrderFixState(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: 3, AvgPrice: 100000.5, OrderNums: []string{"999"}}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 3}}, // positions already match; isolates the order issue
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" || rep.Plan == nil {
		t.Fatalf("%+v", rep)
	}
	var found bool
	for _, oc := range rep.Orders {
		if oc.OrderNum == "999" {
			found = true
			if oc.Owner != "MISSING:r1" || oc.OK {
				t.Fatalf("order check = %+v, want Owner=MISSING:r1 OK=false", oc)
			}
		}
	}
	if !found {
		t.Fatalf("expected an OrderCheck for 999, got %+v", rep.Orders)
	}
	if len(rep.Plan.Steps) != 1 {
		t.Fatalf("expected exactly one step (no close_position since positions match), got %+v", rep.Plan.Steps)
	}
	s := rep.Plan.Steps[0]
	if s.Kind != "fix_state" || s.RobotID != "r1" || s.OrderNum != "999" || s.Symbol != "RIU6" {
		t.Fatalf("fix_state step = %+v", s)
	}
	if s.SetPos != 3 || s.SetAvg != 100000.5 {
		t.Fatalf("fix_state must reset to the robot's CURRENT position/avg, got %+v", s)
	}
	if !strings.Contains(s.Detail, "clear_working") {
		t.Fatalf("fix_state Detail must explain clear_working semantics, got %q", s.Detail)
	}
}

// ---- position off by manual offset -> OK ----

func TestEvaluatePositionOffsetByManualOK(t *testing.T) {
	in := Inputs{
		Robots:       []RobotView{{ID: "r1", Symbol: "RIU6", Position: 2}},
		ManualOffset: map[string]int64{"RIU6": 3},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 5}},
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("%+v", rep)
	}
	if len(rep.Positions) != 1 || !rep.Positions[0].OK {
		t.Fatalf("position check = %+v, want OK (manual offset accounts for the gap)", rep.Positions)
	}
}

// ---- position off WITHOUT offset -> signed close_position Qty ----

func TestEvaluatePositionMismatchSignedQty(t *testing.T) {
	cases := []struct {
		name         string
		robotPos     int64
		quikNet      int64
		wantQty      int64
		wantSubstr   string
	}{
		{"excess long -> SELL", 2, 5, 3, "SELL 3"},
		{"short -> BUY back", 5, 2, -3, "BUY 3"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			in := Inputs{
				Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: tc.robotPos}},
				Acc: AccView{
					Positions: []Position{{Sec: "RIU6", Net: tc.quikNet}},
					PosAgeMs:  100, OrdAgeMs: 100,
				},
			}
			rep := Evaluate(in)
			if rep.State != "MISMATCH" || rep.Plan == nil {
				t.Fatalf("%+v", rep)
			}
			if len(rep.Plan.Steps) != 1 {
				t.Fatalf("steps = %+v, want exactly one close_position", rep.Plan.Steps)
			}
			s := rep.Plan.Steps[0]
			if s.Kind != "close_position" || s.Symbol != "RIU6" || s.Qty != tc.wantQty {
				t.Fatalf("step = %+v, want close_position RIU6 qty=%d", s, tc.wantQty)
			}
			if !strings.Contains(s.Detail, tc.wantSubstr) {
				t.Fatalf("detail = %q, want it to contain %q", s.Detail, tc.wantSubstr)
			}
		})
	}
}

// ---- close_position suppressed when a MISSING/ORPHAN order explains the symbol ----

func TestEvaluateClosePositionSuppressedByOrderFinding(t *testing.T) {
	t.Run("suppressed by MISSING", func(t *testing.T) {
		in := Inputs{
			Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: 2, OrderNums: []string{"999"}}},
			Acc: AccView{
				Positions: []Position{{Sec: "RIU6", Net: 10}}, // would mismatch on its own
				PosAgeMs:  100, OrdAgeMs: 100,
			},
		}
		rep := Evaluate(in)
		if rep.State != "MISMATCH" || rep.Plan == nil {
			t.Fatalf("%+v", rep)
		}
		if !positionMismatchRendered(rep, "RIU6") {
			t.Fatalf("position check for RIU6 must still render as not-OK, got %+v", rep.Positions)
		}
		for _, s := range rep.Plan.Steps {
			if s.Kind == "close_position" {
				t.Fatalf("close_position must be suppressed when a MISSING order explains RIU6, got %+v", rep.Plan.Steps)
			}
		}
	})

	t.Run("suppressed by ORPHAN", func(t *testing.T) {
		in := Inputs{
			Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: 2}},
			Acc: AccView{
				Positions: []Position{{Sec: "RIU6", Net: 10}},
				Orders:    []Order{{Num: "42", Sec: "RIU6", Active: true}}, // orphan: nobody owns it
				PosAgeMs:  100, OrdAgeMs: 100,
			},
		}
		rep := Evaluate(in)
		if rep.State != "MISMATCH" || rep.Plan == nil {
			t.Fatalf("%+v", rep)
		}
		for _, s := range rep.Plan.Steps {
			if s.Kind == "close_position" {
				t.Fatalf("close_position must be suppressed when an ORPHAN order explains RIU6, got %+v", rep.Plan.Steps)
			}
		}
	})
}

func positionMismatchRendered(rep Report, symbol string) bool {
	for _, p := range rep.Positions {
		if p.Symbol == symbol {
			return !p.OK
		}
	}
	return false
}

// ---- paper robots contribute NOTHING ----

func TestEvaluatePaperRobotNeverContributes(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Symbol: "RIU6", Paper: true, Position: 100,
			OrderNums: []string{"777"},
			FillKeys:  []FillKey{{OrderNum: "777", Qty: 1, Price: 100}},
		}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 0}},
			Orders:    []Order{{Num: "777", Sec: "RIU6", Active: true}},
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)

	// Position: paper's 100 must not count -> robots sum 0, matches QUIK net 0.
	if len(rep.Positions) != 1 || rep.Positions[0].RobotsSum != 0 || !rep.Positions[0].OK {
		t.Fatalf("positions = %+v, want RobotsSum=0 OK=true (paper excluded)", rep.Positions)
	}
	// Order: paper's claimed order_num must NOT make it "owned" -> QUIK's active order is ORPHAN.
	if len(rep.Orders) != 1 || rep.Orders[0].Owner != "ORPHAN" {
		t.Fatalf("orders = %+v, want ORPHAN (paper robot does not own orders in QUIK matching)", rep.Orders)
	}
	// Trades: paper contributes no FillKeys to check at all.
	if len(rep.Trades) != 0 {
		t.Fatalf("trades = %+v, want none (paper robots contribute no fills)", rep.Trades)
	}
	if rep.State != "MISMATCH" || rep.Plan == nil {
		t.Fatalf("%+v", rep)
	}
	if len(rep.Plan.Steps) != 1 || rep.Plan.Steps[0].Kind != "cancel_order" {
		t.Fatalf("steps = %+v, want a single cancel_order for the orphan", rep.Plan.Steps)
	}
}

// ---- Plan.ID stability ----

func TestEvaluatePlanIDStableAndChanges(t *testing.T) {
	base := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep1 := Evaluate(base)
	rep2 := Evaluate(base)
	if rep1.Plan == nil || rep2.Plan == nil {
		t.Fatalf("expected a plan on both calls")
	}
	if rep1.Plan.ID != rep2.Plan.ID {
		t.Fatalf("plan id changed across identical calls: %q vs %q", rep1.Plan.ID, rep2.Plan.ID)
	}
	if rep1.Plan.ID == "" || len(rep1.Plan.ID) != 12 {
		t.Fatalf("plan id = %q, want 12 hex chars", rep1.Plan.ID)
	}

	// A different step set -> different ID.
	withExtra := base
	withExtra.Acc.Orders = append([]Order{}, base.Acc.Orders...)
	withExtra.Acc.Orders = append(withExtra.Acc.Orders, Order{Num: "556", Sec: "RIU6", Active: true})
	rep3 := Evaluate(withExtra)
	if rep3.Plan.ID == rep1.Plan.ID {
		t.Fatalf("plan id must change when steps change")
	}

	// Same steps, different ages -> different ID (ages are part of the hash).
	withDifferentAge := base
	withDifferentAge.Acc.PosAgeMs = 200
	rep4 := Evaluate(withDifferentAge)
	if rep4.Plan.ID == rep1.Plan.ID {
		t.Fatalf("plan id must change when PosAgeMs changes")
	}
}

// ---- deterministic ordering across shuffled input slices ----

func TestEvaluateDeterministicOrderingShuffledInputs(t *testing.T) {
	build := func(reverse bool) Inputs {
		robots := []RobotView{
			{ID: "r1", Symbol: "RIU6", Position: 2, OrderNums: []string{"1"}, FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100}}},
			{ID: "r2", Symbol: "GZU6", Position: 1, OrderNums: []string{"2"}},
		}
		positions := []Position{{Sec: "RIU6", Net: 2}, {Sec: "GZU6", Net: 5}} // GZU6 mismatches
		orders := []Order{{Num: "1", Sec: "RIU6", Active: true}, {Num: "99", Sec: "GZU6", Active: true}} // 99 orphan
		trades := []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100, TsMs: 1}}
		trans := []TransCheck{{TransID: 1, Status: "ok", OK: true}, {TransID: 2, Status: "rejected", OK: false}}
		manual := map[string]int64{"RIU6": 0, "GZU6": 0}
		priceStep := map[string]float64{"RIU6": 1, "GZU6": 1}

		if reverse {
			robots = []RobotView{robots[1], robots[0]}
			positions = []Position{positions[1], positions[0]}
			orders = []Order{orders[1], orders[0]}
			trans = []TransCheck{trans[1], trans[0]}
		}
		return Inputs{
			Robots: robots, HumanOrders: map[string]bool{},
			Acc: AccView{Positions: positions, Orders: orders, Trades: trades, PosAgeMs: 100, OrdAgeMs: 100},
			Trans: trans, ManualOffset: manual, PriceStep: priceStep,
		}
	}

	repA := Evaluate(build(false))
	repB := Evaluate(build(true))
	if !reflect.DeepEqual(repA, repB) {
		t.Fatalf("shuffled-input reports differ:\nA=%+v\nB=%+v", repA, repB)
	}
	if repA.Plan == nil || repB.Plan == nil || repA.Plan.ID != repB.Plan.ID {
		t.Fatalf("plan ids differ across shuffled inputs: %+v vs %+v", repA.Plan, repB.Plan)
	}
}

// ---- Trans: not-OK flips State to MISMATCH but generates no Step ----

func TestEvaluateTransMismatchNoStep(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: 0}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 0}},
			PosAgeMs:  100, OrdAgeMs: 100,
		},
		Trans: []TransCheck{{TransID: 42, Status: "pending", Text: "stuck", OK: false}},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" {
		t.Fatalf("state = %q, want MISMATCH (a not-OK trans must surface)", rep.State)
	}
	if rep.Plan == nil {
		t.Fatalf("expected a non-nil plan for MISMATCH state")
	}
	if len(rep.Plan.Steps) != 0 {
		t.Fatalf("trans issues must generate NO step, got %+v", rep.Plan.Steps)
	}
	if len(rep.Trans) != 1 || rep.Trans[0].TransID != 42 || rep.Trans[0].Text != "stuck" {
		t.Fatalf("trans must pass through verbatim, got %+v", rep.Trans)
	}
}

// ---- Trades: unmatched is informational only, does not by itself flip State ----

func TestEvaluateTradeMismatchInformationalOnly(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Symbol: "RIU6", Position: 0,
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100}},
		}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 0}},
			PosAgeMs:  100, OrdAgeMs: 100,
			// No matching Trade at all -> TradeCheck Matched=false.
		},
	}
	rep := Evaluate(in)
	if len(rep.Trades) != 1 || rep.Trades[0].Matched {
		t.Fatalf("trades = %+v, want one unmatched entry", rep.Trades)
	}
	// Per the brief, trade mismatches are "informational only" (unlike Trans, which
	// explicitly flips State) — everything else here is clean, so State stays OK.
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("state = %+v, want OK/no-plan: an unmatched trade alone must not force MISMATCH", rep)
	}
}

// ---- trade fuzzy match within one price step ----

func TestEvaluateTradeMatchWithinPriceStep(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Symbol: "RIU6", Position: 0,
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100000}},
		}},
		PriceStep: map[string]float64{"RIU6": 10},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 0}},
			Trades:    []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100005}}, // within 1 step
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if len(rep.Trades) != 1 || !rep.Trades[0].Matched || rep.Trades[0].TradeID != "t1" {
		t.Fatalf("trades = %+v, want matched within price step", rep.Trades)
	}
}

func TestEvaluateTradeExactMatchWhenNoPriceStep(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Symbol: "RIU6", Position: 0,
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100000}},
		}},
		// No PriceStep entry for RIU6 -> must fall back to exact match.
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 0}},
			Trades:    []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100005}},
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if len(rep.Trades) != 1 || rep.Trades[0].Matched {
		t.Fatalf("trades = %+v, want unmatched (no price step -> exact match required)", rep.Trades)
	}
}

// ---- a single QUIK trade cannot satisfy two distinct fill keys ----

func TestEvaluateTradeMatchDoesNotDoubleCountSameTradeRow(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Symbol: "RIU6", Position: 0,
			// Two identical fill keys (e.g. two 1-lot partials at the same price).
			FillKeys: []FillKey{
				{OrderNum: "1", Qty: 1, Price: 100000},
				{OrderNum: "1", Qty: 1, Price: 100000},
			},
		}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 0}},
			// Only ONE matching trade row exists for that order/qty/price.
			Trades:   []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100000}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if len(rep.Trades) != 2 {
		t.Fatalf("trades = %+v, want 2 checks (one per fill key)", rep.Trades)
	}
	matchedCount := 0
	for _, tc := range rep.Trades {
		if tc.Matched {
			matchedCount++
		}
	}
	if matchedCount != 1 {
		t.Fatalf("trades = %+v, want exactly ONE matched (the single trade row must not satisfy both fill keys)", rep.Trades)
	}
}

// ---- human-owned order is OK, no step ----

func TestEvaluateHumanOwnedOrderOK(t *testing.T) {
	in := Inputs{
		Robots:      []RobotView{{ID: "r1", Symbol: "RIU6"}},
		HumanOrders: map[string]bool{"1": true},
		Acc: AccView{
			Orders:   []Order{{Num: "1", Sec: "RIU6", Active: true}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if len(rep.Orders) != 1 || rep.Orders[0].Owner != "human" || !rep.Orders[0].OK {
		t.Fatalf("orders = %+v, want human-owned OK", rep.Orders)
	}
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("%+v", rep)
	}
}

// ---- missing symbol in QUIK positions defaults to Net 0 ----

func TestEvaluateMissingSymbolInQuikPositionsDefaultsZero(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6", Position: 0}},
		Acc:    AccView{PosAgeMs: 100, OrdAgeMs: 100}, // no Positions rows at all
	}
	rep := Evaluate(in)
	if len(rep.Positions) != 1 || rep.Positions[0].Quik != 0 || !rep.Positions[0].OK {
		t.Fatalf("positions = %+v, want Quik=0 OK=true", rep.Positions)
	}
}
