package status

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/trade"

	quikv1 "shectory/quik_agent/internal/pb"
)

// ---- fakes over the narrow interfaces declared in status.go ----

type fakeAccounts struct{ snap accounts.Snapshot }

func (f fakeAccounts) Snapshot() accounts.Snapshot { return f.snap }

type fakeRobots struct {
	specs  []*quikv1.RobotSpec
	paused map[string]bool
	times  map[string][2]int64
}

func (f fakeRobots) All() []*quikv1.RobotSpec { return f.specs }
func (f fakeRobots) Paused(id string) bool    { return f.paused[id] }
func (f fakeRobots) Times(id string) (int64, int64) {
	t := f.times[id]
	return t[0], t[1]
}

type fakeRunner struct {
	statuses    map[string]*quikv1.RobotStatus
	reportAgeMs int64
	healthy     bool
}

func (f fakeRunner) LastStatuses() map[string]*quikv1.RobotStatus { return f.statuses }
func (f fakeRunner) LastReportAgeMs() int64                       { return f.reportAgeMs }
func (f fakeRunner) RunnerHealthy() bool                          { return f.healthy }

type fakeManager struct {
	working []trade.WorkingSnapshot
	trans   []trade.PendingTransView
}

func (f fakeManager) SnapshotWorking() []trade.WorkingSnapshot    { return f.working }
func (f fakeManager) PendingTransViews() []trade.PendingTransView { return f.trans }

type fakeProvider struct {
	ticks  []quikdde.Tick
	params []quikdde.ParamRow
}

func (f fakeProvider) Ticks() []quikdde.Tick      { return f.ticks }
func (f fakeProvider) Params() []quikdde.ParamRow { return f.params }

// baseDeps returns a minimal, fully-fake Deps: every source empty, clock
// fixed at 1_000_000ms so PosAgeMs/OrdAgeMs computed by BuildStatus's callers
// (accounts.Store in production) can be controlled directly on the fake
// Snapshot instead.
func baseDeps() Deps {
	return Deps{
		Accounts: fakeAccounts{},
		Robots:   fakeRobots{paused: map[string]bool{}, times: map[string][2]int64{}},
		Runner:   fakeRunner{statuses: map[string]*quikv1.RobotStatus{}},
		Manager:  fakeManager{},
		Provider: fakeProvider{},
		Version:  "test",
		BuildRev: 42,
		NowMs:    func() int64 { return 1_000_000 },
	}
}

func decodeTop(t *testing.T, data []byte) map[string]json.RawMessage {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("BuildStatus output is not valid JSON: %v", err)
	}
	return m
}

func TestBuildStatus_TopLevelKeys(t *testing.T) {
	data, err := BuildStatus(baseDeps())
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	top := decodeTop(t, data)
	for _, key := range []string{"agent", "health", "robots", "recon"} {
		if _, ok := top[key]; !ok {
			t.Errorf("missing top-level key %q in status JSON: %s", key, data)
		}
	}
}

func TestBuildStatus_PaperRobotRendersPaperMode(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", StrategyId: "fvg", Paper: true}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(out.Robots) != 1 {
		t.Fatalf("want 1 robot, got %d", len(out.Robots))
	}
	if out.Robots[0]["mode"] != "paper" {
		t.Errorf("want mode=paper, got %v", out.Robots[0]["mode"])
	}
}

func TestBuildStatus_RealRobotRendersRealMode(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", StrategyId: "fvg", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	json.Unmarshal(data, &out)
	if out.Robots[0]["mode"] != "real" {
		t.Errorf("want mode=real, got %v", out.Robots[0]["mode"])
	}
}

func TestBuildStatus_MissingCoefOmitsPnlRub(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", RealizedPnl: 123},
	}}
	// Provider knows no params for RIU6 at all -> coef unknown.
	d.Provider = fakeProvider{}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	json.Unmarshal(data, &out)
	if _, ok := out.Robots[0]["pnl_rub"]; ok {
		t.Errorf("pnl_rub must be omitted when coef is unknown, got %v", out.Robots[0]["pnl_rub"])
	}
	if out.Robots[0]["pnl_points"] != float64(123) {
		t.Errorf("pnl_points must be realized_pnl verbatim, got %v", out.Robots[0]["pnl_points"])
	}
}

func TestBuildStatus_KnownCoefYieldsPnlRub(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", RealizedPnl: 100},
	}}
	d.Provider = fakeProvider{params: []quikdde.ParamRow{{Code: "RIU6", PriceStep: 10, StepCost: 20}}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	json.Unmarshal(data, &out)
	got, ok := out.Robots[0]["pnl_rub"]
	if !ok {
		t.Fatalf("pnl_rub must be present when coef is known")
	}
	if got != float64(200) { // 100 points * (20/10)
		t.Errorf("pnl_rub = %v, want 200", got)
	}
}

func TestBuildStatus_ReconStaleGating(t *testing.T) {
	d := baseDeps()
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{PosAgeMs: 60_000, OrdAgeMs: 1_000}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Recon struct {
			State string `json:"state"`
		} `json:"recon"`
	}
	json.Unmarshal(data, &out)
	if out.Recon.State != "STALE" {
		t.Errorf("want recon.state=STALE when PosAgeMs exceeds the threshold, got %q", out.Recon.State)
	}
}

func TestBuildStatus_ReconOKWhenFresh(t *testing.T) {
	d := baseDeps()
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{PosAgeMs: 100, OrdAgeMs: 100}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Recon struct {
			State string `json:"state"`
		} `json:"recon"`
	}
	json.Unmarshal(data, &out)
	if out.Recon.State != "OK" {
		t.Errorf("want recon.state=OK with no robots/positions/orders, got %q", out.Recon.State)
	}
}

// TestBuildStatus_ZeroStepCostOmitsPnlRub: a params row can carry PriceStep>0
// with StepCost==0 (DDE-sheet fallback parse path) — rubles must be OMITTED,
// not fabricated as 0.
func TestBuildStatus_ZeroStepCostOmitsPnlRub(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", RealizedPnl: 123},
	}}
	d.Provider = fakeProvider{params: []quikdde.ParamRow{{Code: "RIU6", PriceStep: 10, StepCost: 0}}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	json.Unmarshal(data, &out)
	if v, ok := out.Robots[0]["pnl_rub"]; ok {
		t.Errorf("pnl_rub must be omitted when StepCost is 0, got %v", v)
	}
}

// TestBuildStatus_ManualOffsetGoneRobotChecksAndManualPresent: manual_offset is
// retired in favor of the tag model — the recon block must carry manual.orders/
// manual.account_net and robot_checks, and manual_offsets must be entirely
// ABSENT from the JSON (not just empty), so a stale client can't mistake its
// disappearance for an empty map.
func TestBuildStatus_ManualOffsetGoneRobotChecksAndManualPresent(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		PosAgeMs: 10, OrdAgeMs: 10,
		Orders: []accounts.Order{{Num: "1", Sec: "RIU6", Active: true, Tag: ""}}, // untagged -> manual
	}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(data, &top); err != nil {
		t.Fatalf("unmarshal top: %v", err)
	}
	var recon map[string]json.RawMessage
	if err := json.Unmarshal(top["recon"], &recon); err != nil {
		t.Fatalf("unmarshal recon: %v", err)
	}
	if _, present := recon["manual_offsets"]; present {
		t.Errorf("recon.manual_offsets must be GONE, got %s", recon["manual_offsets"])
	}
	if _, present := recon["manual"]; !present {
		t.Error("recon.manual must be present")
	}
	if _, present := recon["robot_checks"]; !present {
		t.Error("recon.robot_checks must be present")
	}

	var out struct {
		Manual struct {
			Orders []struct {
				OrderNum string `json:"order_num"`
			} `json:"orders"`
		} `json:"manual"`
		RobotChecks []struct {
			ID string `json:"id"`
		} `json:"robot_checks"`
	}
	if err := json.Unmarshal(top["recon"], &out); err != nil {
		t.Fatalf("unmarshal recon fields: %v", err)
	}
	if len(out.Manual.Orders) != 1 || out.Manual.Orders[0].OrderNum != "1" {
		t.Errorf("recon.manual.orders = %+v, want the untagged order 1", out.Manual.Orders)
	}
	if len(out.RobotChecks) != 1 || out.RobotChecks[0].ID != "r1" {
		t.Errorf("recon.robot_checks = %+v, want robot r1", out.RobotChecks)
	}
}

// TestBuildStatus_ZeroStepPlanSerializesEmptySteps: a pure-Trans mismatch
// yields a non-nil Plan with ZERO steps (Task 6 convention); the JSON layer
// must serialize it as "steps":[] — never null, never omitted — so the page
// can distinguish "plan with nothing to execute" (no align button) from "no
// plan".
func TestBuildStatus_ZeroStepPlanSerializesEmptySteps(t *testing.T) {
	d := baseDeps()
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{PosAgeMs: 10, OrdAgeMs: 10}}
	d.Manager = fakeManager{trans: []trade.PendingTransView{
		{TransID: 7, Status: "REJECTED", Text: "Торговля QUIK отключена", OK: false},
	}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Recon struct {
			State string `json:"state"`
			Plan  *struct {
				ID    string            `json:"id"`
				Steps []json.RawMessage `json:"steps"`
			} `json:"plan"`
		} `json:"recon"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if out.Recon.State != "MISMATCH" {
		t.Fatalf("want MISMATCH on a not-OK trans, got %q", out.Recon.State)
	}
	if out.Recon.Plan == nil {
		t.Fatalf("want a non-nil plan on MISMATCH")
	}
	if out.Recon.Plan.Steps == nil {
		t.Errorf(`plan.steps must serialize as [] (present, empty) — got null/omitted: %s`, data)
	}
	if len(out.Recon.Plan.Steps) != 0 {
		t.Errorf("want zero steps on a pure-Trans mismatch, got %d", len(out.Recon.Plan.Steps))
	}
}

// TestBuildStatus_WorkingOrderTagAttribution: under the tag model a robot's
// believed-working order absent from QUIK surfaces as MISSING:<robotID>, while an
// UNTAGGED QUIK order is the operator's manual trading — it goes to the manual block
// (never reconciled), NOT to the robot OrderChecks.
func TestBuildStatus_WorkingOrderTagAttribution(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", Position: 0},
	}}
	d.Manager = fakeManager{working: []trade.WorkingSnapshot{
		{ClientID: "rr:r1:1", OrderNum: "111", Code: "RIU6"}, // robot-owned, NOT active in QUIK -> MISSING
		{ClientID: "human-1", OrderNum: "222", Code: "RIU6"}, // human path; its QUIK order is untagged -> MANUAL
	}}
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		PosAgeMs: 10, OrdAgeMs: 10,
		Orders: []accounts.Order{{Num: "222", Sec: "RIU6", Active: true, Qty: 1, Balance: 1, Tag: ""}},
	}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Recon struct {
			Orders []struct {
				OrderNum string `json:"order_num"`
				Owner    string `json:"owner"`
				OK       bool   `json:"ok"`
			} `json:"orders"`
			Manual struct {
				Orders []struct {
					OrderNum string `json:"order_num"`
				} `json:"orders"`
			} `json:"manual"`
		} `json:"recon"`
	}
	json.Unmarshal(data, &out)

	byNum := map[string]struct {
		Owner string
		OK    bool
	}{}
	for _, o := range out.Recon.Orders {
		byNum[o.OrderNum] = struct {
			Owner string
			OK    bool
		}{o.Owner, o.OK}
	}
	if _, present := byNum["222"]; present {
		t.Errorf(`untagged order 222 must NOT be a robot OrderCheck (it is manual), got %+v`, byNum["222"])
	}
	if got := byNum["111"]; got.Owner != "MISSING:r1" || got.OK {
		t.Errorf(`order 111 = %+v, want owner "MISSING:r1" OK=false`, got)
	}
	var manualHas222 bool
	for _, m := range out.Recon.Manual.Orders {
		if m.OrderNum == "222" {
			manualHas222 = true
		}
	}
	if !manualHas222 {
		t.Errorf("untagged order 222 must appear in recon.manual.orders, got %+v", out.Recon.Manual.Orders)
	}
}

// TestBuildStatus_FillKeysGating: a PAPER robot's RecentFills must contribute
// no trade checks at all, and a real robot's fills contribute only the ones
// with status "filled" (a rejected fill has no QUIK trade to match).
func TestBuildStatus_FillKeysGating(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs: []*quikv1.RobotSpec{
			{RobotId: "paper1", Symbol: "GZU6", Paper: true},
			{RobotId: "real1", Symbol: "RIU6", Paper: false},
		},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"paper1": {RobotId: "paper1", RecentFills: []*quikv1.RobotFill{
			{OrderId: "90", Qty: 1, Price: 500, Status: "filled"}, // paper: must NOT count
		}},
		"real1": {RobotId: "real1", RecentFills: []*quikv1.RobotFill{
			{OrderId: "10", Qty: 1, Price: 100, Status: "filled"},   // counts
			{OrderId: "11", Qty: 1, Price: 101, Status: "rejected"}, // must NOT count
		}},
	}}
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		PosAgeMs: 10, OrdAgeMs: 10,
		// Tagged for real1 so it matches under the tag model.
		Trades: []accounts.Trade{{Num: "t1", OrderNum: "10", Sec: "RIU6", Price: 100, Qty: 1, Tag: "real1"}},
	}}

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Recon struct {
			Trades []struct {
				TradeID  string `json:"trade_id"`
				OrderNum string `json:"order_num"`
				Matched  bool   `json:"matched"`
			} `json:"trades"`
		} `json:"recon"`
	}
	json.Unmarshal(data, &out)

	if len(out.Recon.Trades) != 1 {
		t.Fatalf("want exactly 1 trade check (real robot's filled fill only), got %d: %+v",
			len(out.Recon.Trades), out.Recon.Trades)
	}
	tc := out.Recon.Trades[0]
	if tc.OrderNum != "10" || !tc.Matched || tc.TradeID != "t1" {
		t.Errorf("trade check = %+v, want order 10 matched against t1", tc)
	}
}

// TestBuildStatus_HasStatusFalseWithNoRunnerReport: a robot the runner has
// never reported on (freshly reconnected runner, first ReportStatus not yet
// received) must render has_status=false — this is the JSON half of the
// money-safety fix in main.go's ModeSet, which refuses a paper/real flip
// while has_status/LastStatuses is absent instead of treating a missing
// status as an implicit zero position. The mode panel in page.html gates its
// "flat" precondition on has_status too, for the same reason.
func TestBuildStatus_HasStatusFalseWithNoRunnerReport(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	// d.Runner (baseDeps' fakeRunner) has an empty statuses map: r1 has no entry.
	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	json.Unmarshal(data, &out)
	if len(out.Robots) != 1 {
		t.Fatalf("want 1 robot, got %d", len(out.Robots))
	}
	if v, ok := out.Robots[0]["has_status"]; !ok || v != false {
		t.Errorf("want has_status=false when the robot has no LastStatuses entry, got %v (present=%v)", v, ok)
	}
}

// TestBuildStatus_HasStatusTrueWhenRunnerReported: once the runner has sent
// at least one ReportStatus for the robot, has_status must be true — even
// when the reported position itself is zero (has_status and position==0 are
// independent facts: ModeSet/the page must be able to tell "reported flat"
// apart from "never reported").
func TestBuildStatus_HasStatusTrueWhenRunnerReported(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", Position: 0},
	}}
	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Robots []map[string]any `json:"robots"`
	}
	json.Unmarshal(data, &out)
	if v, ok := out.Robots[0]["has_status"]; !ok || v != true {
		t.Errorf("want has_status=true once the runner has reported, got %v (present=%v)", v, ok)
	}
}

// ---- EvaluateRecon (Task 9's main.go alert-loop helper) ----

func TestEvaluateRecon_OKStateNeverRealInvolved(t *testing.T) {
	state, real := EvaluateRecon(baseDeps())
	if state != "OK" || real {
		t.Fatalf("empty everything must be OK/false, got %s/%v", state, real)
	}
}

func TestEvaluateRecon_MismatchWithRealRobotIsRealInvolved(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", Position: 3},
	}}
	// A QUIK order tagged for r1 that the robot does not know -> ROBOT_ORPHAN -> a
	// cancel_order step naming r1's symbol, so realInvolved must be true.
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		PosAgeMs: 10, OrdAgeMs: 10,
		Orders: []accounts.Order{{Num: "999", Sec: "RIU6", Active: true, Tag: "r1"}},
	}}

	state, real := EvaluateRecon(d)
	if state != "MISMATCH" {
		t.Fatalf("want MISMATCH, got %s", state)
	}
	if !real {
		t.Errorf("a real (non-paper) robot's ROBOT_ORPHAN must be realInvolved")
	}
}

func TestEvaluateRecon_MismatchPaperOnlyIsNotRealInvolved(t *testing.T) {
	d := baseDeps()
	// A pure Trans mismatch: no robot, no position/order finding involved at all.
	d.Manager = fakeManager{trans: []trade.PendingTransView{
		{TransID: 1, Status: "REJECTED", OK: false},
	}}

	state, real := EvaluateRecon(d)
	if state != "MISMATCH" {
		t.Fatalf("want MISMATCH, got %s", state)
	}
	if real {
		t.Errorf("a trans-only mismatch naming no real robot/symbol must not be realInvolved")
	}
}

// TestEvaluateRecon_TradeOnlyMismatchIsRealInvolved: a real robot's fill with
// no matching tagged QUIK trade flips State to MISMATCH via evalTrades'
// tradesBad, but generates ZERO plan steps (only evalOrders feeds
// Plan.Steps) — the RobotChecks-based pass must still catch it as
// realInvolved, or a real robot's trade divergence would under-alert as WARN
// instead of CRITICAL.
func TestEvaluateRecon_TradeOnlyMismatchIsRealInvolved(t *testing.T) {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", RecentFills: []*quikv1.RobotFill{
			{OrderId: "10", Qty: 1, Price: 100, Status: "filled"},
		}},
	}}
	// No QUIK trade at all (Acc.Orders/Trades both empty) -> no order finding,
	// so ordSteps is empty and rep.Plan.Steps is []; the fill above matches no
	// tagged trade -> tradesBad["r1"]=true -> RobotCheck{ID:"r1", TradesOK:false}.
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{PosAgeMs: 10, OrdAgeMs: 10}}

	state, real := EvaluateRecon(d)
	if state != "MISMATCH" {
		t.Fatalf("want MISMATCH, got %s", state)
	}
	if !real {
		t.Errorf("a real robot's trade-only mismatch (zero plan steps) must still be realInvolved")
	}
}

func TestEvaluateRecon_StaleNeverRealInvolved(t *testing.T) {
	d := baseDeps()
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{PosAgeMs: 999_999}}
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", Position: 3},
	}}

	state, real := EvaluateRecon(d)
	if state != "STALE" {
		t.Fatalf("want STALE, got %s", state)
	}
	if real {
		t.Errorf("STALE must report realInvolved=false regardless of robot mode")
	}
}

// ---- QUIK terminal tables section ----

func TestBuildStatus_QuikSection(t *testing.T) {
	d := baseDeps()
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		Orders: []accounts.Order{{Num: "1", Sec: "RIU6", Active: true, Price: 89000,
			Balance: 1, Qty: 1, Tag: "r1", Side: "S", TsMs: 5}},
		Trades: []accounts.Trade{{Num: "t1", OrderNum: "1", Sec: "RIU6", Price: 89050,
			Qty: 1, TsMs: 7, Tag: "r1", Side: "B", ExchTsMs: 9}},
		TransReplies: []accounts.TransReply{{TsMs: 3, TransID: 42, Status: 3, OrderNum: "1", Text: "ok"}},
	}}
	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Quik struct {
			Orders   []map[string]any `json:"orders"`
			Trades   []map[string]any `json:"trades"`
			Trans    []map[string]any `json:"trans"`
			Messages []string         `json:"messages"`
			News     []string         `json:"news"`
		} `json:"quik"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	q := out.Quik
	if len(q.Orders) != 1 || q.Orders[0]["side"] != "sell" || q.Orders[0]["tag"] != "r1" {
		t.Errorf("orders = %+v", q.Orders)
	}
	// exchange ts (9) must win over the Lua receipt stamp (7)
	if len(q.Trades) != 1 || q.Trades[0]["ts_ms"] != float64(9) || q.Trades[0]["side"] != "buy" {
		t.Errorf("trades = %+v", q.Trades)
	}
	if len(q.Trans) != 1 || q.Trans[0]["trans_id"] != float64(42) {
		t.Errorf("trans = %+v", q.Trans)
	}
	// no QuikFolder -> messages/news are EMPTY ARRAYS, never null
	if q.Messages == nil || q.News == nil {
		t.Errorf("messages/news must be [] without a folder: %v / %v", q.Messages, q.News)
	}
}

// TestTailLogLines_CP1251NewestFirst: QUIK logs are Windows-1251; the tail
// must decode them, drop blank lines, and return newest-first with the cap.
func TestTailLogLines_CP1251NewestFirst(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "info.log")
	content := []byte("line1\r\n")
	content = append(content, []byte{0xCF, 0xF0, 0xE8, 0xE2, 0xE5, 0xF2}...) // "Привет" cp1251
	content = append(content, []byte("\r\n\r\nline3\r\n")...)
	if err := os.WriteFile(path, content, 0o644); err != nil {
		t.Fatal(err)
	}
	lines := tailLogLines(path, 10)
	want := []string{"line3", "Привет", "line1"}
	if len(lines) != 3 || lines[0] != want[0] || lines[1] != want[1] || lines[2] != want[2] {
		t.Fatalf("lines = %q, want %q", lines, want)
	}
	if capped := tailLogLines(path, 2); len(capped) != 2 || capped[0] != "line3" {
		t.Fatalf("capped = %q", capped)
	}
	if missing := tailLogLines(filepath.Join(dir, "absent.log"), 10); missing != nil {
		t.Fatalf("missing file must return nil, got %q", missing)
	}
}
