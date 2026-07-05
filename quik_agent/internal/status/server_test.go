package status

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/recon"

	quikv1 "shectory/quik_agent/internal/pb"
)

func TestServer_GetStatus(t *testing.T) {
	ts := httptest.NewServer(newMux(baseDeps()))
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/api/status")
	if err != nil {
		t.Fatalf("GET /api/status: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "application/json" {
		t.Errorf("content-type = %q, want application/json", ct)
	}
}

func TestServer_LogsTailsFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.log")
	content := strings.Repeat("x", 70_000) + "TAIL_MARKER_END"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write temp log: %v", err)
	}

	d := baseDeps()
	d.LogPaths = map[string]string{"agent": path}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/logs/agent")
	if err != nil {
		t.Fatalf("GET /logs/agent: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	if !strings.HasSuffix(buf.String(), "TAIL_MARKER_END") {
		t.Errorf("tail response does not end with the marker (len=%d)", buf.Len())
	}
	if buf.Len() > logTailBytes {
		t.Errorf("tail response is %d bytes, want <= %d", buf.Len(), logTailBytes)
	}
}

func TestServer_LogsUnknownName(t *testing.T) {
	ts := httptest.NewServer(newMux(baseDeps()))
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/logs/nope")
	if err != nil {
		t.Fatalf("GET /logs/nope: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
}

func TestServer_StrategyDoc(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "strategies_doc.json")
	body := `{"fvg": {"description": "Fair Value Gap"}}`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write temp doc: %v", err)
	}

	d := baseDeps()
	d.DocsPath = path
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/strategy/fvg")
	if err != nil {
		t.Fatalf("GET /strategy/fvg: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var doc struct {
		Description string `json:"description"`
	}
	json.NewDecoder(resp.Body).Decode(&doc)
	if doc.Description != "Fair Value Gap" {
		t.Errorf("description = %q, want Fair Value Gap", doc.Description)
	}

	resp2, err := http.Get(ts.URL + "/strategy/unknown")
	if err != nil {
		t.Fatalf("GET /strategy/unknown: %v", err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusNotFound {
		t.Errorf("unknown strategy status = %d, want 404", resp2.StatusCode)
	}
}

// mismatchDeps returns a Deps whose recon.Evaluate produces a MISMATCH with a
// non-empty Plan: one real robot claims Position=5 on RIU6 but QUIK's account
// snapshot shows no position at all for that symbol.
func mismatchDeps() Deps {
	d := baseDeps()
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", Paper: false}},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", Position: 5},
	}}
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{PosAgeMs: 10, OrdAgeMs: 10}}
	return d
}

func getFreshPlanID(t *testing.T, ts *httptest.Server) string {
	t.Helper()
	resp, err := http.Get(ts.URL + "/api/status")
	if err != nil {
		t.Fatalf("GET /api/status: %v", err)
	}
	defer resp.Body.Close()
	var out struct {
		Recon struct {
			Plan *struct {
				ID string `json:"id"`
			} `json:"plan"`
		} `json:"recon"`
	}
	json.NewDecoder(resp.Body).Decode(&out)
	if out.Recon.Plan == nil {
		t.Fatalf("expected a non-nil recon.plan in a MISMATCH fixture")
	}
	return out.Recon.Plan.ID
}

func TestServer_AlignStalePlanReturns409WithFreshPlan(t *testing.T) {
	d := mismatchDeps()
	// AlignExec is wired (the nil gate now runs FIRST, so the 409 path must be
	// reached past it) but must never actually run on a stale plan_id.
	d.AlignExec = func(plan recon.Plan) []StepResult {
		t.Errorf("AlignExec must not run on a stale plan_id")
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)

	body, _ := json.Marshal(alignRequest{PlanID: "stale-plan-id-does-not-exist"})
	resp, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("POST /api/align: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("status = %d, want 409", resp.StatusCode)
	}
	var out alignMismatchResponse
	json.NewDecoder(resp.Body).Decode(&out)
	if out.Recon.Plan == nil || out.Recon.Plan.ID != fresh {
		t.Errorf("409 body must carry the fresh plan %q, got %+v", fresh, out.Recon.Plan)
	}
}

func TestServer_AlignNotWiredReturns503(t *testing.T) {
	d := mismatchDeps()
	d.AlignExec = nil
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)
	body, _ := json.Marshal(alignRequest{PlanID: fresh})
	resp, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("POST /api/align: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", resp.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	if !strings.Contains(buf.String(), "align not wired") {
		t.Errorf("body = %q, want it to contain 'align not wired'", buf.String())
	}
}

func TestServer_AlignExecutesMatchingPlan(t *testing.T) {
	d := mismatchDeps()
	var gotPlan recon.Plan
	d.AlignExec = func(plan recon.Plan) []StepResult {
		gotPlan = plan
		return []StepResult{{Kind: "close_position", Symbol: "RIU6", OK: true}}
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)
	body, _ := json.Marshal(alignRequest{PlanID: fresh})
	resp, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("POST /api/align: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if gotPlan.ID != fresh {
		t.Errorf("AlignExec called with plan.ID=%q, want %q", gotPlan.ID, fresh)
	}
	var out alignOKResponse
	json.NewDecoder(resp.Body).Decode(&out)
	if len(out.Results) != 1 || !out.Results[0].OK {
		t.Errorf("unexpected results: %+v", out.Results)
	}
}

func TestServer_ManualOffsetCallsManualSet(t *testing.T) {
	d := baseDeps()
	var got map[string]int64
	d.ManualSet = func(m map[string]int64) error {
		got = m
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/manual-offset", "application/json",
		bytes.NewReader([]byte(`{"RIU6":2}`)))
	if err != nil {
		t.Fatalf("POST /api/manual-offset: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if got == nil || got["RIU6"] != 2 {
		t.Errorf("ManualSet called with %v, want map[RIU6:2]", got)
	}
}

func TestServer_ManualOffsetNotWiredReturns503(t *testing.T) {
	d := baseDeps()
	d.ManualSet = nil
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/manual-offset", "application/json",
		bytes.NewReader([]byte(`{"RIU6":2}`)))
	if err != nil {
		t.Fatalf("POST /api/manual-offset: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", resp.StatusCode)
	}
}

func TestServer_RootServesEmbeddedPage(t *testing.T) {
	ts := httptest.NewServer(newMux(baseDeps()))
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatalf("GET /: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	if !strings.Contains(buf.String(), "<html") {
		t.Errorf("root response does not look like the embedded HTML page")
	}
}
