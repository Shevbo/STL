package status

import (
	"errors"
	"testing"

	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/recon"

	quikv1 "shectory/quik_agent/internal/pb"
)

// ---- fakes over the Aligner's narrow interfaces ----

type fakeAlignManager struct {
	placed    []*quikv1.PlaceOrder
	cancelled [][2]string // {orderNum, sec}
	placeErr  error
	cancelErr error
}

func (f *fakeAlignManager) PlaceOrderErr(p *quikv1.PlaceOrder) error {
	f.placed = append(f.placed, p)
	return f.placeErr
}

func (f *fakeAlignManager) CancelOrphan(orderNum, sec string) error {
	f.cancelled = append(f.cancelled, [2]string{orderNum, sec})
	return f.cancelErr
}

type fakeFixSender struct {
	sent []*quikv1.FixRobotState
	err  error
}

func (f *fakeFixSender) SendFixState(fix *quikv1.FixRobotState) error {
	f.sent = append(f.sent, fix)
	return f.err
}

func alignFixture() (*Aligner, *fakeAlignManager, *fakeFixSender) {
	mgr := &fakeAlignManager{}
	fix := &fakeFixSender{}
	a := &Aligner{
		Manager: mgr,
		Runner:  fix,
		Provider: fakeProvider{
			ticks:  []quikdde.Tick{{Code: "RIU6", Last: 89173, ReceivedUnixMs: 999_000}},
			params: []quikdde.ParamRow{{Code: "RIU6", PriceStep: 10}},
		},
		NowMs: func() int64 { return 1_000_000 },
	}
	return a, mgr, fix
}

func TestAligner_CancelOrder(t *testing.T) {
	a, mgr, _ := alignFixture()
	plan := recon.Plan{ID: "abc123", Steps: []recon.Step{
		{Kind: "cancel_order", Symbol: "RIU6", OrderNum: "555", Detail: "orphan"},
	}}
	res := a.Execute(plan)
	if len(mgr.cancelled) != 1 || mgr.cancelled[0] != [2]string{"555", "RIU6"} {
		t.Fatalf("want CancelOrphan(555, RIU6), got %v", mgr.cancelled)
	}
	if len(res) != 1 || !res[0].OK || res[0].Error != "" {
		t.Fatalf("want one OK result, got %+v", res)
	}
}

// close_position is a HARD REFUSAL: recon no longer generates this step kind
// (an "excess account position" is contextual — it can include the operator's
// own manual trading, not just robot activity — so it is reported for context
// only). The Aligner must place NO order for it under any Qty/sign/zero, and
// must refuse even with a live tick+price-step available (unlike the old
// placing behavior, the refusal does not depend on provider data at all).
func TestAligner_ClosePosition_AlwaysRefusesNoOrderPlaced(t *testing.T) {
	for name, qty := range map[string]int64{"positive": 2, "negative": -1, "zero": 0} {
		a, mgr, _ := alignFixture() // fixture provider DOES have a valid RIU6 tick+step
		res := a.Execute(recon.Plan{ID: "cp-" + name, Steps: []recon.Step{
			{Kind: "close_position", Symbol: "RIU6", Qty: qty, Detail: "stale/legacy step"},
		}})
		if len(mgr.placed) != 0 {
			t.Errorf("%s: close_position must place NO order, got %+v", name, mgr.placed)
		}
		if len(res) != 1 || res[0].OK || res[0].Error == "" {
			t.Errorf("%s: close_position must be refused with a non-empty error, got %+v", name, res)
		}
	}
}

// The refusal holds even with NO provider tick/price-step data at all — the
// old placing implementation needed both to compute a quantized price; the
// refusal needs neither, confirming it never reaches that logic.
func TestAligner_ClosePosition_RefusesWithNoProviderData(t *testing.T) {
	a, mgr, _ := alignFixture()
	a.Provider = fakeProvider{} // no ticks, no params
	res := a.Execute(recon.Plan{ID: "cp-noprov", Steps: []recon.Step{
		{Kind: "close_position", Symbol: "RIU6", Qty: 1},
	}})
	if len(mgr.placed) != 0 {
		t.Fatalf("must not place without provider data either, placed %+v", mgr.placed)
	}
	if len(res) != 1 || res[0].OK {
		t.Fatalf("want a refused result, got %+v", res)
	}
}

// A multi-symbol plan with two close_position steps: the FIRST is refused,
// which stops the sequence (Execute's documented behavior) — the second is
// reported skipped, never executed. Nothing is ever placed either way.
func TestAligner_TwoClosePositions_BothNeverPlaceOrders(t *testing.T) {
	a, mgr, _ := alignFixture()
	a.Provider = fakeProvider{
		ticks: []quikdde.Tick{
			{Code: "RIU6", Last: 89173},
			{Code: "GZU6", Last: 14520},
		},
		params: []quikdde.ParamRow{
			{Code: "RIU6", PriceStep: 10},
			{Code: "GZU6", PriceStep: 1},
		},
	}
	res := a.Execute(recon.Plan{ID: "p9", Steps: []recon.Step{
		{Kind: "close_position", Symbol: "GZU6", Qty: 1},
		{Kind: "close_position", Symbol: "RIU6", Qty: -1},
	}})
	if len(mgr.placed) != 0 {
		t.Fatalf("want zero placements, got %d: %+v", len(mgr.placed), mgr.placed)
	}
	if len(res) != 2 || res[0].OK || res[0].Error == "" {
		t.Fatalf("want the first step refused with an error, got %+v", res)
	}
	if res[1].OK || res[1].Error == "" {
		t.Fatalf("want the second step reported skipped after the first failure, got %+v", res[1])
	}
}

func TestAligner_FixState(t *testing.T) {
	a, _, fix := alignFixture()
	step := recon.Step{
		Kind: "fix_state", Symbol: "RIU6", OrderNum: "777", RobotID: "r1",
		SetPos: 2, SetAvg: 89000, Detail: "phantom working order — clear and reset",
	}
	res := a.Execute(recon.Plan{ID: "p5", Steps: []recon.Step{step}})
	if len(fix.sent) != 1 {
		t.Fatalf("want one SendFixState, got %d", len(fix.sent))
	}
	fx := fix.sent[0]
	if fx.GetRobotId() != "r1" || fx.GetSetPosition() != 2 || fx.GetSetAvgPrice() != 89000 {
		t.Errorf("fix payload mismatch: %+v", fx)
	}
	if !fx.GetClearWorking() {
		t.Errorf("fix_state must clear the phantom working belief")
	}
	if fx.GetNote() == "" {
		t.Errorf("fix note (journal text) must not be empty")
	}
	if len(res) != 1 || !res[0].OK || res[0].RobotID != "r1" {
		t.Fatalf("want one OK result carrying the robot id, got %+v", res)
	}
}

// A step error STOPS the sequence: the failing step reports the error, every
// remaining step is reported skipped, and no later action executes.
func TestAligner_ErrorStopsSequence(t *testing.T) {
	a, mgr, fix := alignFixture()
	mgr.cancelErr = errors.New("bridge down")
	res := a.Execute(recon.Plan{ID: "p6", Steps: []recon.Step{
		{Kind: "cancel_order", Symbol: "RIU6", OrderNum: "1"},
		{Kind: "close_position", Symbol: "RIU6", Qty: 1},
		{Kind: "fix_state", Symbol: "RIU6", RobotID: "r1"},
	}})
	if len(res) != 3 {
		t.Fatalf("every step needs a result, got %d", len(res))
	}
	if res[0].OK || res[0].Error != "bridge down" {
		t.Errorf("failing step must carry its error, got %+v", res[0])
	}
	for i, r := range res[1:] {
		if r.OK || r.Error == "" {
			t.Errorf("step %d after a failure must be reported skipped, got %+v", i+1, r)
		}
	}
	if len(mgr.placed) != 0 || len(fix.sent) != 0 {
		t.Errorf("no action may execute after a failed step (placed=%d fixes=%d)",
			len(mgr.placed), len(fix.sent))
	}
}

// A Manager rejection becomes the StepResult error — the Aligner never
// pre-checks or bypasses the Manager's gates. (close_position can no longer
// exercise this path at all — it refuses unconditionally without ever
// reaching the Manager — so this now covers cancel_order, the one step kind
// that still calls into trade.Manager.)
func TestAligner_ManagerRejectionBecomesStepError(t *testing.T) {
	a, mgr, _ := alignFixture()
	mgr.cancelErr = errors.New("cancel rejected: bridge down")
	res := a.Execute(recon.Plan{ID: "p7", Steps: []recon.Step{
		{Kind: "cancel_order", Symbol: "RIU6", OrderNum: "1"},
	}})
	if len(res) != 1 || res[0].OK || res[0].Error != "cancel rejected: bridge down" {
		t.Fatalf("rejection must surface as the step error, got %+v", res)
	}
}

func TestAligner_UnknownKindFails(t *testing.T) {
	a, _, _ := alignFixture()
	res := a.Execute(recon.Plan{ID: "p8", Steps: []recon.Step{{Kind: "adopt_fill"}}})
	if len(res) != 1 || res[0].OK || res[0].Error == "" {
		t.Fatalf("unknown kind must fail loudly, got %+v", res)
	}
}
