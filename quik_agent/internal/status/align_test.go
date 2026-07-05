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

// SIGN CONVENTION: Step.Qty POSITIVE = account LONG in excess => SELL |Qty|.
// Price = provider last quantized to the step, FILL-FAVORING for the side:
// SELL rounds down (89173/10 -> 89170).
func TestAligner_ClosePosition_PositiveQtySellsQuantizedDown(t *testing.T) {
	a, mgr, _ := alignFixture()
	plan := recon.Plan{ID: "abc123", Steps: []recon.Step{
		{Kind: "close_position", Symbol: "RIU6", Qty: 2, Detail: "SELL 2 to align"},
	}}
	res := a.Execute(plan)
	if len(mgr.placed) != 1 {
		t.Fatalf("want one placement, got %d", len(mgr.placed))
	}
	p := mgr.placed[0]
	if p.GetSide() != quikv1.Side_SIDE_SELL {
		t.Errorf("Qty>0 must SELL, got side %v", p.GetSide())
	}
	if p.GetQuantity() != 2 {
		t.Errorf("want qty 2, got %d", p.GetQuantity())
	}
	if p.GetPrice() != 89170 {
		t.Errorf("want price quantized 89173->89170, got %v", p.GetPrice())
	}
	if p.GetCode() != "RIU6" {
		t.Errorf("want code RIU6, got %q", p.GetCode())
	}
	if p.GetClientId() != "recon:abc123" {
		t.Errorf(`want client_id "recon:abc123", got %q`, p.GetClientId())
	}
	if len(res) != 1 || !res[0].OK {
		t.Fatalf("want one OK result, got %+v", res)
	}
}

// NEGATIVE Qty = account is SHORT of the robots' claim => BUY back |Qty|.
// BUY quantizes UP (fill-favoring): 89173/10 -> 89180.
func TestAligner_ClosePosition_NegativeQtyBuysQuantizedUp(t *testing.T) {
	a, mgr, _ := alignFixture()
	plan := recon.Plan{ID: "p2", Steps: []recon.Step{
		{Kind: "close_position", Symbol: "RIU6", Qty: -1, Detail: "BUY 1 to align"},
	}}
	a.Execute(plan)
	if len(mgr.placed) != 1 {
		t.Fatalf("want one placement, got %d", len(mgr.placed))
	}
	p := mgr.placed[0]
	if p.GetSide() != quikv1.Side_SIDE_BUY {
		t.Errorf("Qty<0 must BUY, got side %v", p.GetSide())
	}
	if p.GetQuantity() != 1 {
		t.Errorf("want qty 1 (abs), got %d", p.GetQuantity())
	}
	if p.GetPrice() != 89180 {
		t.Errorf("want BUY price quantized 89173->89180, got %v", p.GetPrice())
	}
}

// A last price already on the grid stays put for both sides.
func TestAligner_ClosePosition_OnGridPriceUnchanged(t *testing.T) {
	a, mgr, _ := alignFixture()
	a.Provider = fakeProvider{
		ticks:  []quikdde.Tick{{Code: "RIU6", Last: 89170}},
		params: []quikdde.ParamRow{{Code: "RIU6", PriceStep: 10}},
	}
	a.Execute(recon.Plan{ID: "p3", Steps: []recon.Step{
		{Kind: "close_position", Symbol: "RIU6", Qty: -1},
	}})
	if len(mgr.placed) != 1 || mgr.placed[0].GetPrice() != 89170 {
		t.Fatalf("on-grid price must be unchanged, got %+v", mgr.placed)
	}
}

func TestAligner_ClosePosition_NoPriceOrStepFails(t *testing.T) {
	for name, prov := range map[string]fakeProvider{
		"no tick": {params: []quikdde.ParamRow{{Code: "RIU6", PriceStep: 10}}},
		"no step": {ticks: []quikdde.Tick{{Code: "RIU6", Last: 89173}}},
	} {
		a, mgr, _ := alignFixture()
		a.Provider = prov
		res := a.Execute(recon.Plan{ID: "p4", Steps: []recon.Step{
			{Kind: "close_position", Symbol: "RIU6", Qty: 1},
		}})
		if len(mgr.placed) != 0 {
			t.Errorf("%s: must not place without a valid price, placed %+v", name, mgr.placed)
		}
		if len(res) != 1 || res[0].OK || res[0].Error == "" {
			t.Errorf("%s: want one failed result with error, got %+v", name, res)
		}
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

// A Manager rejection (Guard limits / master flag off) becomes the StepResult
// error — the Aligner never pre-checks or bypasses the Manager's gates.
func TestAligner_ManagerRejectionBecomesStepError(t *testing.T) {
	a, mgr, _ := alignFixture()
	mgr.placeErr = errors.New("trading disabled by master flag")
	res := a.Execute(recon.Plan{ID: "p7", Steps: []recon.Step{
		{Kind: "close_position", Symbol: "RIU6", Qty: 1},
	}})
	if len(res) != 1 || res[0].OK || res[0].Error != "trading disabled by master flag" {
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
