package recon

import (
	"reflect"
	"strings"
	"testing"
)

// ---- brief's verbatim invariant-#1 test ----

func TestEvaluateManualNeverAligned(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 15}},
			Orders: []Order{
				{Num: "555", Sec: "RIU6", Active: true, Tag: ""},      // manual
				{Num: "777", Sec: "RIU6", Active: true, Tag: "recon"}, // agent align
			},
			PosAgeMs: 100, OrdAgeMs: 100, PosAtMs: 1, OrdAtMs: 1,
		},
		NowMs: 1,
	}
	rep := Evaluate(in)
	if rep.State != "OK" {
		t.Fatalf("manual-only + agent align must be OK, got %s", rep.State)
	}
	if rep.Plan != nil && len(rep.Plan.Steps) != 0 {
		t.Fatalf("no align step may target manual/recon: %+v", rep.Plan.Steps)
	}
	if len(rep.Manual.Orders) != 1 || rep.Manual.Orders[0].OrderNum != "555" {
		t.Fatalf("manual order 555 must be in the Manual block: %+v", rep.Manual)
	}
	if rep.Manual.Orders[0].Sec != "RIU6" {
		t.Fatalf("manual order must carry its symbol: %+v", rep.Manual.Orders[0])
	}
	if len(rep.Manual.AccountNet) != 1 || rep.Manual.AccountNet[0].Net != 15 {
		t.Fatalf("account net 15 must be shown for context: %+v", rep.Manual.AccountNet)
	}
	// The "recon"-tagged order must be invisible to every robot-facing surface.
	for _, oc := range rep.Orders {
		if oc.OrderNum == "777" || oc.OrderNum == "555" {
			t.Fatalf("recon/manual orders must not appear as OrderChecks: %+v", oc)
		}
	}
	for _, m := range rep.Manual.Orders {
		if m.OrderNum == "777" {
			t.Fatalf("a recon-tagged order must NOT be classified manual: %+v", rep.Manual.Orders)
		}
	}
}

// TestInvariant1_ManualNeverInAlignPlan: even when a REAL robot mismatch produces a plan,
// no step may name a co-existing manual order or its symbol via that order.
func TestInvariant1_ManualNeverInAlignPlan(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders: []Order{
				{Num: "555", Sec: "RIU6", Active: true, Tag: ""},   // manual
				{Num: "999", Sec: "RIU6", Active: true, Tag: "r1"}, // robot r1 does not know -> ROBOT_ORPHAN
			},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" || rep.Plan == nil {
		t.Fatalf("expected MISMATCH with a plan: %+v", rep)
	}
	if len(rep.Plan.Steps) != 1 || rep.Plan.Steps[0].OrderNum != "999" {
		t.Fatalf("plan must target ONLY the robot orphan 999, got %+v", rep.Plan.Steps)
	}
	for _, s := range rep.Plan.Steps {
		if s.OrderNum == "555" {
			t.Fatalf("INVARIANT #1 VIOLATED: a step targets the manual order 555: %+v", s)
		}
	}
	// The manual order is still SHOWN, just never reconciled.
	if len(rep.Manual.Orders) != 1 || rep.Manual.Orders[0].OrderNum != "555" {
		t.Fatalf("manual order 555 must still appear in the Manual block: %+v", rep.Manual.Orders)
	}
}

// TestInvariant5_ManualNeverFlipsState: a fully self-consistent robot alongside heavy
// manual activity stays OK.
func TestInvariant5_ManualNeverFlipsState(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6", Position: 1, OrderNums: []string{"1"}}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 99}}, // account net wildly different from robot pos: irrelevant
			Orders: []Order{
				{Num: "1", Sec: "RIU6", Active: true, Tag: "r1"}, // robot's, known -> OK
				{Num: "50", Sec: "RIU6", Active: true, Tag: ""},  // manual
				{Num: "51", Sec: "SiU6", Active: true, Tag: ""},  // manual
			},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" {
		t.Fatalf("INVARIANT #5 VIOLATED: manual activity flipped State to %s: %+v", rep.State, rep)
	}
	if rep.Plan != nil {
		t.Fatalf("no plan when the only non-manual thing is a consistent robot: %+v", rep.Plan)
	}
	if len(rep.Manual.Orders) != 2 {
		t.Fatalf("both manual orders must be listed: %+v", rep.Manual.Orders)
	}
}

// ---- robot order the robot KNOWS -> OK ----

func TestEvaluateRobotKnownOrderOK(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6", Position: 2, OrderNums: []string{"1"}}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 2}},
			Orders:    []Order{{Num: "1", Sec: "RIU6", Active: true, Tag: "r1"}},
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("%+v", rep)
	}
	if len(rep.Orders) != 1 || rep.Orders[0].Owner != "r1" || !rep.Orders[0].OK {
		t.Fatalf("orders = %+v, want one owner=r1 OK=true", rep.Orders)
	}
	if len(rep.Manual.Orders) != 0 {
		t.Fatalf("a robot order must not appear in the Manual block: %+v", rep.Manual.Orders)
	}
	if len(rep.RobotChecks) != 1 || !rep.RobotChecks[0].OrdersOK || !rep.RobotChecks[0].TradesOK {
		t.Fatalf("robot check = %+v, want OrdersOK/TradesOK true", rep.RobotChecks)
	}
	if rep.RobotChecks[0].Position != 2 {
		t.Fatalf("robot check must show the robot's believed position, got %+v", rep.RobotChecks[0])
	}
}

// ---- robot-tagged order the robot does NOT know -> ROBOT_ORPHAN + cancel_order ----

func TestEvaluateRobotOrphanCancels(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" || rep.Plan == nil {
		t.Fatalf("%+v", rep)
	}
	if len(rep.Orders) != 1 || rep.Orders[0].Owner != "r1" || rep.Orders[0].OK {
		t.Fatalf("orders = %+v, want owner=r1 OK=false (ROBOT_ORPHAN)", rep.Orders)
	}
	if len(rep.Plan.Steps) != 1 || rep.Plan.Steps[0].Kind != "cancel_order" || rep.Plan.Steps[0].OrderNum != "555" {
		t.Fatalf("steps = %+v, want a single cancel_order for 555", rep.Plan.Steps)
	}
	if !strings.Contains(rep.Plan.Steps[0].Detail, "ROBOT_ORPHAN") {
		t.Fatalf("detail should name the ROBOT_ORPHAN condition, got %q", rep.Plan.Steps[0].Detail)
	}
	if len(rep.RobotChecks) != 1 || rep.RobotChecks[0].OrdersOK {
		t.Fatalf("robot check OrdersOK must be false, got %+v", rep.RobotChecks)
	}
}

// ---- "recon"-tagged order -> agent align, skipped entirely ----

func TestEvaluateReconTagSkipped(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders:   []Order{{Num: "777", Sec: "RIU6", Active: true, Tag: "recon"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("a recon-tagged order alone must be OK/no-plan, got %+v", rep)
	}
	if len(rep.Orders) != 0 {
		t.Fatalf("a recon order must produce no OrderCheck, got %+v", rep.Orders)
	}
	if len(rep.Manual.Orders) != 0 {
		t.Fatalf("a recon order must NOT be classified manual, got %+v", rep.Manual.Orders)
	}
}

// ---- unknown tag (not a deployed robot) -> treated as MANUAL ----

func TestEvaluateUnknownTagIsManual(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders:   []Order{{Num: "888", Sec: "RIU6", Active: true, Tag: "human-42"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("an unknown-tag order is not ours to touch -> OK, got %+v", rep)
	}
	if len(rep.Orders) != 0 {
		t.Fatalf("unknown-tag order must produce no robot OrderCheck, got %+v", rep.Orders)
	}
	if len(rep.Manual.Orders) != 1 || rep.Manual.Orders[0].OrderNum != "888" {
		t.Fatalf("unknown-tag order must land in the Manual block, got %+v", rep.Manual.Orders)
	}
}

// ---- MISSING order -> fix_state (preserves SetPos/SetAvg/clear_working) ----

func TestEvaluateMissingOrderFixState(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6", Position: 3, AvgPrice: 100000.5, OrderNums: []string{"999"}}},
		Acc: AccView{
			PosAgeMs: 100, OrdAgeMs: 100,
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
		t.Fatalf("expected exactly one fix_state step, got %+v", rep.Plan.Steps)
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
	if len(rep.RobotChecks) != 1 || rep.RobotChecks[0].OrdersOK {
		t.Fatalf("robot check OrdersOK must be false on a MISSING, got %+v", rep.RobotChecks)
	}
}

// ---- trades: forward match / no-match / reverse-unrecorded ----

func TestEvaluateTradeForwardMatchTradesOK(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Tag: "r1", Symbol: "RIU6",
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100}},
		}},
		Acc: AccView{
			Trades:   []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("a clean forward trade match must be OK, got %+v", rep)
	}
	if len(rep.Trades) != 1 || !rep.Trades[0].Matched || rep.Trades[0].TradeID != "t1" {
		t.Fatalf("trades = %+v, want matched against t1", rep.Trades)
	}
	if !rep.RobotChecks[0].TradesOK {
		t.Fatalf("TradesOK must be true on a clean match, got %+v", rep.RobotChecks[0])
	}
}

func TestEvaluateTradeForwardNoMatchFlipsState(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Tag: "r1", Symbol: "RIU6",
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100}},
		}},
		Acc: AccView{
			// no QUIK trade at all -> forward unmatched
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" {
		t.Fatalf("an unmatched robot fill must flip State to MISMATCH, got %s", rep.State)
	}
	if rep.Plan == nil || len(rep.Plan.Steps) != 0 {
		t.Fatalf("a trade mismatch must produce a plan with ZERO steps (informational), got %+v", rep.Plan)
	}
	if len(rep.Trades) != 1 || rep.Trades[0].Matched {
		t.Fatalf("trades = %+v, want one unmatched", rep.Trades)
	}
	if rep.RobotChecks[0].TradesOK {
		t.Fatalf("TradesOK must be false, got %+v", rep.RobotChecks[0])
	}
}

func TestEvaluateTradeWrongTagDoesNotMatch(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Tag: "r1", Symbol: "RIU6",
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100}},
		}},
		Acc: AccView{
			// right order/qty/price but UNTAGGED -> must NOT match the robot (tag model)
			Trades:   []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100, Tag: ""}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" {
		t.Fatalf("an untagged trade must not satisfy a robot fill, expected MISMATCH, got %s", rep.State)
	}
	if len(rep.Trades) != 1 || rep.Trades[0].Matched {
		t.Fatalf("trades = %+v, want unmatched (tag must equal the robot)", rep.Trades)
	}
}

func TestEvaluateTradeReverseUnrecordedFlipsState(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}}, // robot recorded NO fills
		Acc: AccView{
			// QUIK has a trade tagged for r1 that r1 never recorded.
			Trades:   []Trade{{Num: "t9", OrderNum: "5", Sec: "RIU6", Qty: 1, Price: 100, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" {
		t.Fatalf("a tagged QUIK trade the robot never recorded must flip State, got %s", rep.State)
	}
	if rep.Plan == nil || len(rep.Plan.Steps) != 0 {
		t.Fatalf("reverse trade mismatch generates no step, got %+v", rep.Plan)
	}
	if rep.RobotChecks[0].TradesOK {
		t.Fatalf("TradesOK must be false on a reverse mismatch, got %+v", rep.RobotChecks[0])
	}
}

// ---- trade match ignores price (improvement must not unmatch) ----

// A marketable order fills BETTER than its limit price routinely (price
// improvement; live 10.07: buy placed @87330 filled @87310). The matcher keys
// on tag+order_num+qty ONLY — any price divergence still matches.
func TestEvaluateTradeMatchIgnoresPrice(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Tag: "r1", Symbol: "RIU6",
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 87330}},
		}},
		PriceStep: map[string]float64{"RIU6": 10},
		Acc: AccView{
			Trades:   []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 87310, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if len(rep.Trades) != 1 || !rep.Trades[0].Matched || rep.Trades[0].TradeID != "t1" {
		t.Fatalf("trades = %+v, want matched despite 2-step price improvement", rep.Trades)
	}
}

// Same without any PriceStep supplied — must still match (no exact-price fallback).
func TestEvaluateTradeMatchIgnoresPriceWithoutStep(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Tag: "r1", Symbol: "RIU6",
			FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100000}},
		}},
		Acc: AccView{
			Trades:   []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100005, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if len(rep.Trades) != 1 || !rep.Trades[0].Matched {
		t.Fatalf("trades = %+v, want matched (price is not part of identity)", rep.Trades)
	}
}

// ---- a single QUIK trade cannot satisfy two distinct fill keys ----

func TestEvaluateTradeMatchDoesNotDoubleCountSameTradeRow(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{
			ID: "r1", Tag: "r1", Symbol: "RIU6",
			FillKeys: []FillKey{
				{OrderNum: "1", Qty: 1, Price: 100000},
				{OrderNum: "1", Qty: 1, Price: 100000},
			},
		}},
		Acc: AccView{
			Trades:   []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100000, Tag: "r1"}},
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
		t.Fatalf("trades = %+v, want exactly ONE matched (single row cannot satisfy both keys)", rep.Trades)
	}
	// The unsatisfied second fill flips TradesOK.
	if rep.RobotChecks[0].TradesOK {
		t.Fatalf("the un-matchable second fill must flip TradesOK, got %+v", rep.RobotChecks[0])
	}
}

// ---- purely-manual account: State OK, Manual populated, zero steps ----

func TestEvaluatePurelyManualAccount(t *testing.T) {
	orders := make([]Order, 0, 6)
	for _, num := range []string{"a", "b", "c", "d", "e", "f"} {
		orders = append(orders, Order{Num: num, Sec: "RIU6", Active: true, Tag: ""})
	}
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}}, // deployed but idle
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 15}},
			Orders:    orders,
			PosAgeMs:  100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" {
		t.Fatalf("purely-manual account must be OK, got %s", rep.State)
	}
	if rep.Plan != nil {
		t.Fatalf("no plan for a purely-manual account, got %+v", rep.Plan)
	}
	if len(rep.Manual.Orders) != 6 {
		t.Fatalf("all six manual orders must be listed, got %+v", rep.Manual.Orders)
	}
	if len(rep.Manual.AccountNet) != 1 || rep.Manual.AccountNet[0].Net != 15 {
		t.Fatalf("account net must be shown, got %+v", rep.Manual.AccountNet)
	}
	if len(rep.Orders) != 0 {
		t.Fatalf("no robot OrderChecks in a purely-manual account, got %+v", rep.Orders)
	}
}

// ---- STALE gating (checks still render; plan nil) ----

func TestEvaluateStaleTables(t *testing.T) {
	cases := []struct {
		name           string
		posAge, ordAge int64
	}{
		{"pos age over threshold", 30_001, 100},
		{"ord age over threshold", 100, 30_001},
		{"both over threshold", 40_000, 40_000},
		{"pos never published (-1 sentinel)", -1, 100},
		{"ord never published (-1 sentinel)", 100, -1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			in := Inputs{
				// A clear robot mismatch (ROBOT_ORPHAN) so we can prove checks still render
				// with real content even though the overall State is forced STALE.
				Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
				Acc: AccView{
					Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true, Tag: "r1"}},
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
			if len(rep.Orders) != 1 || rep.Orders[0].OrderNum != "555" || rep.Orders[0].Owner != "r1" || rep.Orders[0].OK {
				t.Fatalf("orders must still render with real data even when STALE, got %+v", rep.Orders)
			}
			if len(rep.RobotChecks) != 1 {
				t.Fatalf("robot checks must still render even when STALE, got %+v", rep.RobotChecks)
			}
		})
	}
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
			// An order tagged "r1" exists, but the paper robot must NOT own it (paper never
			// places real orders / carries no tag) -> unknown tag -> MANUAL.
			Orders:   []Order{{Num: "777", Sec: "RIU6", Active: true, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "OK" || rep.Plan != nil {
		t.Fatalf("paper robot contributes nothing -> OK, got %+v", rep)
	}
	if len(rep.Orders) != 0 {
		t.Fatalf("paper robot must own no orders, got %+v", rep.Orders)
	}
	if len(rep.Manual.Orders) != 1 || rep.Manual.Orders[0].OrderNum != "777" {
		t.Fatalf("the tag of a paper robot is unknown -> order is MANUAL, got %+v", rep.Manual.Orders)
	}
	if len(rep.Trades) != 0 {
		t.Fatalf("paper contributes no fills, got %+v", rep.Trades)
	}
	if len(rep.RobotChecks) != 0 {
		t.Fatalf("paper robots must not appear in RobotChecks, got %+v", rep.RobotChecks)
	}
}

// ---- Trans: not-OK flips State to MISMATCH, generates no step ----

func TestEvaluateTransMismatchNoStep(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
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
	if rep.Plan == nil || len(rep.Plan.Steps) != 0 {
		t.Fatalf("trans issues must generate NO step, got %+v", rep.Plan)
	}
	if len(rep.Trans) != 1 || rep.Trans[0].TransID != 42 || rep.Trans[0].Text != "stuck" {
		t.Fatalf("trans must pass through verbatim, got %+v", rep.Trans)
	}
}

// ---- heterogeneous plan step order: cancel_order < fix_state ----

func TestEvaluateHeterogeneousPlanStepOrder(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{
			// MISSING order on SiU6 -> fix_state.
			{ID: "r1", Tag: "r1", Symbol: "SiU6", Position: 1, OrderNums: []string{"777"}},
			// r2 owns nothing; a QUIK order tagged r2 it does not know -> ROBOT_ORPHAN cancel.
			{ID: "r2", Tag: "r2", Symbol: "RIU6"},
		},
		Acc: AccView{
			Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true, Tag: "r2"}},
			PosAgeMs: 100, OrdAgeMs: 100,
		},
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" || rep.Plan == nil {
		t.Fatalf("%+v", rep)
	}
	if len(rep.Plan.Steps) != 2 {
		t.Fatalf("steps = %+v, want exactly 2", rep.Plan.Steps)
	}
	s := rep.Plan.Steps
	if s[0].Kind != "cancel_order" || s[0].OrderNum != "555" || s[0].Symbol != "RIU6" {
		t.Fatalf("steps[0] = %+v, want cancel_order 555 RIU6", s[0])
	}
	if s[1].Kind != "fix_state" || s[1].RobotID != "r1" || s[1].OrderNum != "777" || s[1].Symbol != "SiU6" {
		t.Fatalf("steps[1] = %+v, want fix_state r1/777/SiU6", s[1])
	}
}

// ---- Plan.ID stability + change sensitivity ----

func TestEvaluatePlanIDStableAndChanges(t *testing.T) {
	base := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true, Tag: "r1"}}, // ROBOT_ORPHAN
			PosAgeMs: 100, OrdAgeMs: 100,
			PosAtMs: 1_000_000, OrdAtMs: 1_000_000,
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
	withExtra.Acc.Orders = append(withExtra.Acc.Orders, Order{Num: "556", Sec: "RIU6", Active: true, Tag: "r1"})
	rep3 := Evaluate(withExtra)
	if rep3.Plan.ID == rep1.Plan.ID {
		t.Fatalf("plan id must change when steps change")
	}

	// Same steps, different AGES ALONE -> id UNCHANGED.
	withDifferentAge := base
	withDifferentAge.Acc.PosAgeMs = 200
	rep4 := Evaluate(withDifferentAge)
	if rep4.Plan.ID != rep1.Plan.ID {
		t.Fatalf("plan id must NOT change when only PosAgeMs changes")
	}

	// Same steps, different ABSOLUTE receipt stamp -> ID changes.
	withNewStamp := base
	withNewStamp.Acc.PosAtMs = 2_000_000
	rep5 := Evaluate(withNewStamp)
	if rep5.Plan.ID == rep1.Plan.ID {
		t.Fatalf("plan id must change when PosAtMs changes")
	}
}

// TestEvaluatePlanIDStableAcrossAgeDriftUsesAbsoluteStamps is the CRITICAL-fix regression:
// two polls of the SAME underlying tables (ages tick up, absolute stamps do not) must yield
// the identical plan id so a confirm computed 1500ms after the page polled still matches.
func TestEvaluatePlanIDStableAcrossAgeDriftUsesAbsoluteStamps(t *testing.T) {
	base := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Orders:   []Order{{Num: "555", Sec: "RIU6", Active: true, Tag: "r1"}},
			PosAgeMs: 100, OrdAgeMs: 100,
			PosAtMs: 1_000_000, OrdAtMs: 1_000_000,
		},
	}
	rep1 := Evaluate(base)

	drifted := base
	drifted.Acc.PosAgeMs += 1500
	drifted.Acc.OrdAgeMs += 1500
	rep2 := Evaluate(drifted)
	if rep1.Plan == nil || rep2.Plan == nil {
		t.Fatalf("expected a plan on both calls")
	}
	if rep1.Plan.ID != rep2.Plan.ID {
		t.Fatalf("plan id must survive 1500ms of pure age drift: %q vs %q", rep1.Plan.ID, rep2.Plan.ID)
	}

	newTable := base
	newTable.Acc.PosAtMs = 1_000_100
	rep3 := Evaluate(newTable)
	if rep3.Plan.ID == rep1.Plan.ID {
		t.Fatalf("plan id must change when PosAtMs changes (new table snapshot)")
	}
}

// ---- determinism across shuffled input slices ----

func TestEvaluateDeterministicOrderingShuffledInputs(t *testing.T) {
	build := func(reverse bool) Inputs {
		robots := []RobotView{
			{ID: "r1", Tag: "r1", Symbol: "RIU6", Position: 2, OrderNums: []string{"1"}, FillKeys: []FillKey{{OrderNum: "1", Qty: 1, Price: 100}}},
			{ID: "r2", Tag: "r2", Symbol: "GZU6", Position: 1, OrderNums: []string{"2"}},
		}
		positions := []Position{{Sec: "RIU6", Net: 2}, {Sec: "GZU6", Net: 5}}
		orders := []Order{
			{Num: "1", Sec: "RIU6", Active: true, Tag: "r1"},  // r1 knows it -> OK
			{Num: "99", Sec: "GZU6", Active: true, Tag: "r2"}, // r2 does NOT know it -> ROBOT_ORPHAN
			{Num: "70", Sec: "RIU6", Active: true, Tag: ""},   // manual
		}
		trades := []Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Qty: 1, Price: 100, Tag: "r1"}}
		trans := []TransCheck{{TransID: 1, Status: "ok", OK: true}, {TransID: 2, Status: "rejected", OK: false}}
		priceStep := map[string]float64{"RIU6": 1, "GZU6": 1}

		if reverse {
			robots = []RobotView{robots[1], robots[0]}
			positions = []Position{positions[1], positions[0]}
			orders = []Order{orders[2], orders[1], orders[0]}
			trans = []TransCheck{trans[1], trans[0]}
		}
		return Inputs{
			Robots: robots,
			Acc:    AccView{Positions: positions, Orders: orders, Trades: trades, PosAgeMs: 100, OrdAgeMs: 100},
			Trans:  trans, PriceStep: priceStep,
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
	// Sanity on the fixture: r2 orphan cancel + r2 MISSING fix_state = 2 steps; the manual
	// order 70 must NOT be among them (invariant #1).
	if len(repA.Plan.Steps) != 2 {
		t.Fatalf("steps = %+v, want 2 (r2 orphan cancel + r2 MISSING fix)", repA.Plan.Steps)
	}
	for _, s := range repA.Plan.Steps {
		if s.OrderNum == "70" {
			t.Fatalf("manual order 70 must never be a step: %+v", repA.Plan.Steps)
		}
	}
}

// ---- RobotChecks: per-robot summary, sorted by ID ----

func TestEvaluateRobotChecksSortedAndPopulated(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{
			{ID: "z", Tag: "z", Symbol: "GZU6", Position: -3},
			{ID: "a", Tag: "a", Symbol: "RIU6", Position: 7},
			{ID: "p", Tag: "p", Symbol: "SiU6", Position: 1, Paper: true}, // excluded
		},
		Acc: AccView{PosAgeMs: 100, OrdAgeMs: 100},
	}
	rep := Evaluate(in)
	if len(rep.RobotChecks) != 2 {
		t.Fatalf("paper robot must be excluded from RobotChecks, got %+v", rep.RobotChecks)
	}
	if rep.RobotChecks[0].ID != "a" || rep.RobotChecks[1].ID != "z" {
		t.Fatalf("RobotChecks must be sorted by ID, got %+v", rep.RobotChecks)
	}
	if rep.RobotChecks[0].Position != 7 || rep.RobotChecks[1].Position != -3 {
		t.Fatalf("RobotChecks must carry believed positions, got %+v", rep.RobotChecks)
	}
	for _, rc := range rep.RobotChecks {
		if !rc.OrdersOK || !rc.TradesOK {
			t.Fatalf("idle robot must be self-consistent, got %+v", rc)
		}
	}
}
