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
