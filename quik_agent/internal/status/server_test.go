package status

import (
	"bytes"
	"encoding/json"
	"errors"
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

func TestServer_Pause(t *testing.T) {
	var gotID string
	var gotPaused bool
	d := baseDeps()
	d.Pause = func(id string, paused bool) error { gotID = id; gotPaused = paused; return nil }
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/pause", "application/json", strings.NewReader(`{"paused":true}`))
	if err != nil {
		t.Fatalf("POST pause: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if gotID != "r1" || !gotPaused {
		t.Errorf("Pause got (%q,%v), want (r1,true)", gotID, gotPaused)
	}

	// missing 'paused' -> 400 (never a silent default)
	resp2, _ := http.Post(ts.URL+"/api/robot/r1/pause", "application/json", strings.NewReader(`{}`))
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusBadRequest {
		t.Errorf("missing paused: status = %d, want 400", resp2.StatusCode)
	}

	// nil Pause dep -> 503
	ts2 := httptest.NewServer(newMux(baseDeps()))
	defer ts2.Close()
	resp3, _ := http.Post(ts2.URL+"/api/robot/r1/pause", "application/json", strings.NewReader(`{"paused":true}`))
	resp3.Body.Close()
	if resp3.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("nil Pause: status = %d, want 503", resp3.StatusCode)
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
// non-empty Plan: a QUIK order tagged for robot r1 that the robot does not know about
// (a ROBOT_ORPHAN) yields a single cancel_order step.
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
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		PosAgeMs: 10, OrdAgeMs: 10,
		Orders: []accounts.Order{{Num: "999", Sec: "RIU6", Active: true, Tag: "r1"}},
	}}
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

// clockAccounts models accounts.Store's REAL behavior (ages computed against a live
// clock at Snapshot() time, absolute receipt stamps fixed until new data arrives) — the
// static fakeAccounts used elsewhere in this file returns a frozen age and can't
// exercise the finding-1 regression (plan id computed at page-poll time must still
// match the id recomputed at button-click time, even after the clock moved on).
type clockAccounts struct {
	posAtMs, ordAtMs int64
	positions        []accounts.Position
	orders           []accounts.Order
	nowMs            func() int64
}

func (c clockAccounts) Snapshot() accounts.Snapshot {
	now := c.nowMs()
	return accounts.Snapshot{
		Positions: c.positions,
		Orders:    c.orders,
		PosAtMs:   c.posAtMs,
		OrdAtMs:   c.ordAtMs,
		PosAgeMs:  now - c.posAtMs,
		OrdAgeMs:  now - c.ordAtMs,
	}
}

// TestServer_AlignSucceedsAfterClockAdvanceWithSameTables is the CRITICAL-fix
// regression test at the HTTP layer: it fetches the plan id via GET /api/status, then
// advances the injected clock (simulating time passing before the operator clicks
// "confirm") WITHOUT any new table data, and confirms the align with the EARLIER plan
// id still succeeds. Before the fix, hashing PosAgeMs/OrdAgeMs made this always 409.
func TestServer_AlignSucceedsAfterClockAdvanceWithSameTables(t *testing.T) {
	clock := int64(1_000_000)
	d := mismatchDeps()
	d.Accounts = clockAccounts{
		posAtMs: 999_900, ordAtMs: 999_900,
		orders: []accounts.Order{{Num: "999", Sec: "RIU6", Active: true, Tag: "r1"}}, // ROBOT_ORPHAN -> mismatch
		nowMs:  func() int64 { return clock },
	}
	var gotPlan recon.Plan
	d.AlignExec = func(plan recon.Plan) []StepResult {
		gotPlan = plan
		return []StepResult{{Kind: "close_position", Symbol: "RIU6", OK: true}}
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)

	// Advance the clock (ages recompute) with NO new table data.
	clock += 1500

	body, _ := json.Marshal(alignRequest{PlanID: fresh})
	resp, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("POST /api/align: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		buf := new(bytes.Buffer)
		buf.ReadFrom(resp.Body)
		t.Fatalf("status = %d, want 200 (plan id must survive age drift alone): %s", resp.StatusCode, buf.String())
	}
	if gotPlan.ID != fresh {
		t.Errorf("AlignExec called with plan.ID=%q, want %q", gotPlan.ID, fresh)
	}
}

// TestServer_AlignRefusesReExecutionOfSamePlan: a second POST /api/align naming the
// SAME plan_id that was just executed must be refused (409, "already executed") rather
// than firing the align actions a second time.
func TestServer_AlignRefusesReExecutionOfSamePlan(t *testing.T) {
	d := mismatchDeps()
	execCount := 0
	d.AlignExec = func(plan recon.Plan) []StepResult {
		execCount++
		return []StepResult{{Kind: "close_position", Symbol: "RIU6", OK: true}}
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)
	body, _ := json.Marshal(alignRequest{PlanID: fresh})

	resp1, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("first POST /api/align: %v", err)
	}
	resp1.Body.Close()
	if resp1.StatusCode != http.StatusOK {
		t.Fatalf("first POST status = %d, want 200", resp1.StatusCode)
	}

	resp2, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("second POST /api/align: %v", err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusConflict {
		t.Fatalf("second POST (same plan) status = %d, want 409", resp2.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp2.Body)
	if !strings.Contains(buf.String(), "уже исполнен") {
		t.Errorf("second POST body = %q, want it to mention the plan is already executed", buf.String())
	}
	if execCount != 1 {
		t.Errorf("AlignExec called %d times, want exactly 1 (double-fire must be refused)", execCount)
	}
}

// TestServer_AlignAllFailedDoesNotLatch: an execution where EVERY step failed (the
// routine first-smoke case: disarmed agent rejects the close order) must NOT latch the
// plan id — the operator arms the master flag and retries the SAME plan (unchanged
// tables = unchanged id), and that retry must execute, not 409 "план уже исполнен".
func TestServer_AlignAllFailedDoesNotLatch(t *testing.T) {
	d := mismatchDeps()
	execCount := 0
	d.AlignExec = func(plan recon.Plan) []StepResult {
		execCount++
		if execCount == 1 {
			// First run: all-failed (e.g. "trading disabled by master flag").
			return []StepResult{{Kind: "close_position", Symbol: "RIU6", OK: false,
				Error: "trading disabled by master flag"}}
		}
		return []StepResult{{Kind: "close_position", Symbol: "RIU6", OK: true}}
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)
	body, _ := json.Marshal(alignRequest{PlanID: fresh})

	resp1, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("first POST /api/align: %v", err)
	}
	resp1.Body.Close()
	if resp1.StatusCode != http.StatusOK {
		t.Fatalf("first POST status = %d, want 200 (failed steps still report 200 with per-step errors)", resp1.StatusCode)
	}

	// Immediate retry with the SAME plan id (operator armed the flag): must execute.
	resp2, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("second POST /api/align: %v", err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusOK {
		buf := new(bytes.Buffer)
		buf.ReadFrom(resp2.Body)
		t.Fatalf("retry after all-failed run: status = %d, want 200 (must not latch): %s", resp2.StatusCode, buf.String())
	}
	if execCount != 2 {
		t.Errorf("AlignExec called %d times, want 2 (all-failed run must not latch the plan)", execCount)
	}
}

// TestServer_AlignPartialSuccessLatches: an execution where SOME step succeeded (and
// the rest failed/skipped) DID change reality — re-running the same plan must be
// refused; the remaining steps need a fresh plan computed from the new picture.
func TestServer_AlignPartialSuccessLatches(t *testing.T) {
	d := mismatchDeps()
	execCount := 0
	d.AlignExec = func(plan recon.Plan) []StepResult {
		execCount++
		return []StepResult{
			{Kind: "cancel_order", Symbol: "RIU6", OrderNum: "1", OK: true},
			{Kind: "close_position", Symbol: "RIU6", OK: false, Error: "no current last price"},
		}
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	fresh := getFreshPlanID(t, ts)
	body, _ := json.Marshal(alignRequest{PlanID: fresh})

	resp1, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("first POST /api/align: %v", err)
	}
	resp1.Body.Close()
	if resp1.StatusCode != http.StatusOK {
		t.Fatalf("first POST status = %d, want 200", resp1.StatusCode)
	}

	resp2, err := http.Post(ts.URL+"/api/align", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("second POST /api/align: %v", err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusConflict {
		t.Fatalf("retry after partial success: status = %d, want 409 (must latch)", resp2.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp2.Body)
	if !strings.Contains(buf.String(), "уже исполнен") {
		t.Errorf("retry body = %q, want it to mention the plan is already executed", buf.String())
	}
	if execCount != 1 {
		t.Errorf("AlignExec called %d times, want exactly 1 (partial success must latch)", execCount)
	}
}

// TestServer_ManualOffsetRouteRemoved: no handler is registered for
// POST /api/manual-offset anymore. Go's ServeMux still matches the path via
// the catch-all "GET /" (a subtree pattern), so a mismatched method there
// reports 405 rather than 404 — either way, handleManualOffset never runs.
func TestServer_ManualOffsetRouteRemoved(t *testing.T) {
	ts := httptest.NewServer(newMux(baseDeps()))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/manual-offset", "application/json",
		bytes.NewReader([]byte(`{"RIU6":2}`)))
	if err != nil {
		t.Fatalf("POST /api/manual-offset: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405 (route retired with manual_offset)", resp.StatusCode)
	}
}

// ---- POST /api/robot/{id}/params ----

func TestServer_ParamsRouteCallsParamsSetWithPointerPresence(t *testing.T) {
	var gotID string
	var gotUpd ParamsUpdate
	d := baseDeps()
	d.ParamsSet = func(id string, upd ParamsUpdate) error {
		gotID = id
		gotUpd = upd
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/params", "application/json",
		bytes.NewReader([]byte(`{"max_position":2}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/params: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if gotID != "r1" {
		t.Errorf("id = %q, want r1", gotID)
	}
	if gotUpd.MaxPosition == nil || *gotUpd.MaxPosition != 2 {
		t.Fatalf("MaxPosition = %v, want pointer to 2", gotUpd.MaxPosition)
	}
	if gotUpd.ParamsJSON != nil {
		t.Errorf("ParamsJSON = %v, want nil (absent field must not fabricate a value)", gotUpd.ParamsJSON)
	}
	if gotUpd.Schedule != nil {
		t.Errorf("Schedule = %v, want nil (absent field must not fabricate a value)", gotUpd.Schedule)
	}
}

func TestServer_ParamsRouteAllFieldsPresent(t *testing.T) {
	var gotUpd ParamsUpdate
	d := baseDeps()
	d.ParamsSet = func(id string, upd ParamsUpdate) error {
		gotUpd = upd
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	body := `{"params_json":"{\"k\":1}","schedule":"09:00-18:45","max_position":5}`
	resp, err := http.Post(ts.URL+"/api/robot/r1/params", "application/json", bytes.NewReader([]byte(body)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/params: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if gotUpd.ParamsJSON == nil || *gotUpd.ParamsJSON != `{"k":1}` {
		t.Errorf("ParamsJSON = %v, want pointer to {\"k\":1}", gotUpd.ParamsJSON)
	}
	if gotUpd.Schedule == nil || *gotUpd.Schedule != "09:00-18:45" {
		t.Errorf("Schedule = %v, want pointer to 09:00-18:45", gotUpd.Schedule)
	}
	if gotUpd.MaxPosition == nil || *gotUpd.MaxPosition != 5 {
		t.Errorf("MaxPosition = %v, want pointer to 5", gotUpd.MaxPosition)
	}
}

func TestServer_ParamsRouteBadJSONReturns400(t *testing.T) {
	d := baseDeps()
	d.ParamsSet = func(id string, upd ParamsUpdate) error {
		t.Errorf("ParamsSet must not be called on malformed JSON")
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/params", "application/json",
		bytes.NewReader([]byte(`{not json`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/params: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestServer_ParamsRouteUnknownRobotReturns404(t *testing.T) {
	d := baseDeps()
	d.ParamsSet = func(id string, upd ParamsUpdate) error { return ErrUnknownRobot }
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/ghost/params", "application/json",
		bytes.NewReader([]byte(`{"max_position":1}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/ghost/params: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
}

// TestServer_ParamsRouteOtherErrorReturns400 asserts a non-ErrUnknownRobot
// error (a validation/range failure, per the brief's "400 on bad JSON/range")
// maps to 400 — unlike the mode route, where a non-nil error is a 409
// precondition failure. Params errors are a client-input problem; mode errors
// are a business-state problem.
func TestServer_ParamsRouteOtherErrorReturns400(t *testing.T) {
	d := baseDeps()
	d.ParamsSet = func(id string, upd ParamsUpdate) error {
		return errors.New("max_position must be >= 0")
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/params", "application/json",
		bytes.NewReader([]byte(`{"max_position":-1}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/params: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	if !strings.Contains(buf.String(), "max_position must be >= 0") {
		t.Errorf("body = %q, want it to contain the validation error", buf.String())
	}
}

func TestServer_ParamsRouteNotWiredReturns503(t *testing.T) {
	d := baseDeps() // ParamsSet left nil
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/params", "application/json",
		bytes.NewReader([]byte(`{"max_position":1}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/params: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", resp.StatusCode)
	}
}

// ---- POST /api/robot/{id}/mode ----

func TestServer_ModeRouteCallsModeSet(t *testing.T) {
	var gotID, gotConfirm string
	var gotPaper, gotForce bool
	d := baseDeps()
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error {
		gotID, gotPaper, gotConfirm, gotForce = id, paper, confirmID, force
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":false,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if gotID != "r1" || gotPaper != false || gotConfirm != "r1" || gotForce != false {
		t.Errorf("ModeSet called with (%q, %v, %q, force=%v), want (r1, false, r1, false)", gotID, gotPaper, gotConfirm, gotForce)
	}

	// force=true is forwarded verbatim (the disarm-override the operator uses to
	// send a non-flat real robot to paper).
	gotForce = false
	resp2, _ := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":true,"confirm_id":"r1","force":true}`)))
	resp2.Body.Close()
	if !gotForce {
		t.Errorf("force=true was not forwarded to ModeSet")
	}
}

// TestServer_ModeRoutePreconditionFailureReturns409WithReason: ModeSet's
// precondition check (e.g. the flat gate) rejecting the flip must surface as
// 409 with the exact reason text in the body — that text is the only way the
// operator learns WHY the arming action was refused.
func TestServer_ModeRoutePreconditionFailureReturns409WithReason(t *testing.T) {
	d := baseDeps()
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error {
		return errors.New("робот не в нуле (позиция 5): закрой позицию перед сменой режима")
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":true,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("status = %d, want 409", resp.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	if !strings.Contains(buf.String(), "не в нуле") {
		t.Errorf("body = %q, want it to contain the precondition reason", buf.String())
	}
}

// TestServer_ModeRouteConfirmMismatchReturns409: confirm_id mismatch is the
// ModeSet implementation's own concern (Task 7) — the route just forwards it
// and maps whatever non-nil error comes back to 409, exactly like any other
// precondition failure.
func TestServer_ModeRouteConfirmMismatchReturns409(t *testing.T) {
	d := baseDeps()
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error {
		if confirmID != id {
			return errors.New("подтверждение не совпадает: введите точный ID робота")
		}
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":false,"confirm_id":"wrong"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusConflict {
		t.Errorf("status = %d, want 409", resp.StatusCode)
	}
}

func TestServer_ModeRouteUnknownRobotReturns404(t *testing.T) {
	d := baseDeps()
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error { return ErrUnknownRobot }
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/ghost/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":true,"confirm_id":"ghost"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/ghost/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
}

func TestServer_ModeRouteBadJSONReturns400(t *testing.T) {
	d := baseDeps()
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error {
		t.Errorf("ModeSet must not be called on malformed JSON")
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{not json`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

// TestServer_ModeRouteMissingPaperReturns400: an absent "paper" key must be
// rejected outright, never silently treated as paper=false (which would arm a
// robot for REAL money on a request that never actually said so). Deps.ModeSet
// must not even be invoked.
func TestServer_ModeRouteMissingPaperReturns400(t *testing.T) {
	d := baseDeps()
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error {
		t.Errorf("ModeSet must not be called when 'paper' is absent from the request")
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	if !strings.Contains(buf.String(), "paper") {
		t.Errorf("body = %q, want it to mention the missing 'paper' key", buf.String())
	}
}

func TestServer_ModeRouteNotWiredReturns503(t *testing.T) {
	d := baseDeps() // ModeSet left nil
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":true,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", resp.StatusCode)
	}
}

// TestServer_ModeRouteIsAgentLocalOnly documents and enforces invariant #2:
// POST /api/robot/{id}/mode — the real-money paper/real arming action — must
// be reachable on THIS agent-local status server. The STL-side HTTP app
// (trader/api/quik_robots.py, a separate Python/FastAPI process — Task 8 of
// this plan) deliberately registers NO such route, so STL itself can never
// flip a robot's paper/real flag; this package has no visibility into that
// separate process, so the STL half of the invariant is enforced by Task 8's
// own test suite, not here. This test only asserts the local half: the route
// exists and reaches Deps.ModeSet.
func TestServer_ModeRouteIsAgentLocalOnly(t *testing.T) {
	d := baseDeps()
	called := false
	d.ModeSet = func(id string, paper bool, confirmID string, force bool) error { called = true; return nil }
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/mode", "application/json",
		bytes.NewReader([]byte(`{"paper":true,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST /api/robot/r1/mode: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("mode route not registered on the agent-local server: status = %d, want 200", resp.StatusCode)
	}
	if !called {
		t.Errorf("ModeSet was not invoked by the registered route")
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

// ---- set-position route (operator manual position correction) ----

func TestServer_SetPositionRouteCallsDep(t *testing.T) {
	var gotID, gotConfirm string
	var gotPos int64
	var gotAvg float64
	d := baseDeps()
	d.SetPosition = func(id string, pos int64, avg float64, confirmID string, pnl *PnlFix) error {
		gotID, gotPos, gotAvg, gotConfirm = id, pos, avg, confirmID
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":-1,"avg_price":90070,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST set-position: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if gotID != "r1" || gotPos != -1 || gotAvg != 90070 || gotConfirm != "r1" {
		t.Errorf("SetPosition called with (%q, %d, %v, %q)", gotID, gotPos, gotAvg, gotConfirm)
	}
}

// TestServer_SetPositionMissingFieldsReturn400: an absent "position" key must
// 400 (never a silent 0), and a non-zero position with no/zero avg_price must
// 400 — in both cases WITHOUT calling the dep.
func TestServer_SetPositionMissingFieldsReturn400(t *testing.T) {
	called := false
	d := baseDeps()
	d.SetPosition = func(id string, pos int64, avg float64, confirmID string, pnl *PnlFix) error {
		called = true
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	for _, body := range []string{
		`{"avg_price":90070,"confirm_id":"r1"}`, // no position
		`{"position":-1,"confirm_id":"r1"}`,     // non-zero position, no avg
		`{"position":-1,"avg_price":0,"confirm_id":"r1"}`,
		`not json`,
	} {
		resp, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
			bytes.NewReader([]byte(body)))
		if err != nil {
			t.Fatalf("POST set-position: %v", err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Errorf("body %q: status = %d, want 400", body, resp.StatusCode)
		}
	}
	if called {
		t.Error("SetPosition must not be called on a 400 request")
	}
}

// TestServer_SetPositionZeroNeedsNoAvg: position 0 (flatten the belief) is
// valid without avg_price — a flat book has no meaningful average.
func TestServer_SetPositionZeroNeedsNoAvg(t *testing.T) {
	d := baseDeps()
	d.SetPosition = func(id string, pos int64, avg float64, confirmID string, pnl *PnlFix) error { return nil }
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":0,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST set-position: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
}

func TestServer_SetPositionErrorMapping(t *testing.T) {
	d := baseDeps()
	d.SetPosition = func(id string, pos int64, avg float64, confirmID string, pnl *PnlFix) error {
		if id == "ghost" {
			return ErrUnknownRobot
		}
		return errors.New("робот должен быть на ПАУЗЕ")
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/ghost/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":0,"confirm_id":"ghost"}`)))
	if err != nil {
		t.Fatalf("POST set-position (ghost): %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("unknown robot: status = %d, want 404", resp.StatusCode)
	}

	resp2, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":0,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST set-position (r1): %v", err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusConflict {
		t.Errorf("precondition: status = %d, want 409", resp2.StatusCode)
	}
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp2.Body)
	if !strings.Contains(buf.String(), "ПАУЗЕ") {
		t.Errorf("body = %q, want the reason text", buf.String())
	}
}

func TestServer_SetPositionNotWiredReturns503(t *testing.T) {
	ts := httptest.NewServer(newMux(baseDeps()))
	defer ts.Close()
	resp, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":0,"confirm_id":"r1"}`)))
	if err != nil {
		t.Fatalf("POST set-position: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", resp.StatusCode)
	}
}

// P&L correction rides set-position: both fields -> PnlFix passed; one field
// alone -> 400 without calling the dep (a half-correction would corrupt).
func TestServer_SetPositionPnlFields(t *testing.T) {
	var gotPnl *PnlFix
	called := 0
	d := baseDeps()
	d.SetPosition = func(id string, pos int64, avg float64, confirmID string, pnl *PnlFix) error {
		called++
		gotPnl = pnl
		return nil
	}
	ts := httptest.NewServer(newMux(d))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":7,"avg_price":88964,"confirm_id":"r1",`+
			`"realized_gross_pts":146986.3,"commission_pts":19246.7}`)))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || called != 1 {
		t.Fatalf("status=%d called=%d", resp.StatusCode, called)
	}
	if gotPnl == nil || gotPnl.RealizedGrossPts != 146986.3 || gotPnl.CommissionPts != 19246.7 {
		t.Fatalf("pnl = %+v", gotPnl)
	}

	resp2, err := http.Post(ts.URL+"/api/robot/r1/set-position", "application/json",
		bytes.NewReader([]byte(`{"position":7,"avg_price":88964,"confirm_id":"r1",`+
			`"realized_gross_pts":100}`)))
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusBadRequest || called != 1 {
		t.Fatalf("half pnl: status=%d called=%d, want 400 and no dep call", resp2.StatusCode, called)
	}
}
