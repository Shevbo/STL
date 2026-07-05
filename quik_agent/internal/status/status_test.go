package status

import (
	"encoding/json"
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

func TestRobotIDFromClientID(t *testing.T) {
	cases := []struct {
		clientID string
		wantID   string
		wantOK   bool
	}{
		{"rr:live-fvg-RIU6:1", "live-fvg-RIU6", true},
		{"rr:r1:2", "r1", true},
		{"human-42", "", false},
		{"rr:onlyid", "onlyid", true},
	}
	for _, c := range cases {
		id, ok := robotIDFromClientID(c.clientID)
		if id != c.wantID || ok != c.wantOK {
			t.Errorf("robotIDFromClientID(%q) = (%q, %v), want (%q, %v)", c.clientID, id, ok, c.wantID, c.wantOK)
		}
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

// TestBuildStatus_ManualOffsetsInRecon: the recon block must carry the
// CURRENT ManualGet() map so the page can render the offset editor.
func TestBuildStatus_ManualOffsetsInRecon(t *testing.T) {
	d := baseDeps()
	d.ManualGet = func() map[string]int64 { return map[string]int64{"RIU6": 2, "GZU6": -1} }

	data, err := BuildStatus(d)
	if err != nil {
		t.Fatalf("BuildStatus: %v", err)
	}
	var out struct {
		Recon struct {
			ManualOffsets map[string]int64 `json:"manual_offsets"`
		} `json:"recon"`
	}
	json.Unmarshal(data, &out)
	if out.Recon.ManualOffsets["RIU6"] != 2 || out.Recon.ManualOffsets["GZU6"] != -1 {
		t.Errorf("recon.manual_offsets = %v, want map[GZU6:-1 RIU6:2]", out.Recon.ManualOffsets)
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

// TestBuildStatus_WorkingOrderOwnershipSplit: SnapshotWorking entries with an
// "rr:<robotID>:n" ClientID must land in THAT robot's OrderNums (so an
// absent-from-QUIK one surfaces as MISSING:<robotID>), while any other
// ClientID goes to HumanOrders (so its active QUIK order resolves to owner
// "human", never ORPHAN).
func TestBuildStatus_WorkingOrderOwnershipSplit(t *testing.T) {
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
		{ClientID: "rr:r1:1", OrderNum: "111", Code: "RIU6"}, // robot-owned, NOT active in QUIK
		{ClientID: "human-1", OrderNum: "222", Code: "RIU6"}, // human-owned, active in QUIK
	}}
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		PosAgeMs: 10, OrdAgeMs: 10,
		Orders: []accounts.Order{{Num: "222", Sec: "RIU6", Active: true, Qty: 1, Balance: 1}},
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
	if got := byNum["222"]; got.Owner != "human" || !got.OK {
		t.Errorf(`order 222 = %+v, want owner "human" OK=true (not ORPHAN)`, got)
	}
	if got := byNum["111"]; got.Owner != "MISSING:r1" || got.OK {
		t.Errorf(`order 111 = %+v, want owner "MISSING:r1" OK=false`, got)
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
		Trades: []accounts.Trade{{Num: "t1", OrderNum: "10", Sec: "RIU6", Price: 100, Qty: 1}},
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
