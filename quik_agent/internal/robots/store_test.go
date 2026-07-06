package robots

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

func TestStorePutGetPersistReload(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	spec := &quikv1.RobotSpec{RobotId: "live-fvg-RIU6", StrategyId: "fvg",
		Symbol: "RIU6", Schedule: "09:00-23:55", MaxPositionContracts: 1,
		ParamsJson: `{"symbol":"RIU6","qty":1}`}
	if err := s.Put(spec); err != nil {
		t.Fatal(err)
	}
	if got := s.Get("live-fvg-RIU6"); got == nil || got.StrategyId != "fvg" {
		t.Fatalf("get after put = %+v", got)
	}
	if err := s.SetPaused("live-fvg-RIU6", true); err != nil {
		t.Fatal(err)
	}

	// RELOAD from disk — specs and paused flags must survive (zero-touch resume).
	s2, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got := s2.Get("live-fvg-RIU6"); got == nil || got.Symbol != "RIU6" {
		t.Fatalf("reload lost spec: %+v", got)
	}
	if !s2.Paused("live-fvg-RIU6") {
		t.Fatal("reload lost paused flag")
	}
	if n := len(s2.All()); n != 1 {
		t.Fatalf("All() = %d, want 1", n)
	}

	if err := s2.Delete("live-fvg-RIU6"); err != nil {
		t.Fatal(err)
	}
	if s2.Get("live-fvg-RIU6") != nil {
		t.Fatal("delete did not remove spec")
	}
	// file must exist and be valid JSON after every mutation
	if _, err := os.Stat(filepath.Join(dir, "robots.json")); err != nil {
		t.Fatal("robots.json missing after mutations")
	}
}

func TestStoreDeployedAndParamsTimestamps(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	fakeNow := int64(1000)
	s.nowMs = func() int64 { return fakeNow }

	spec := &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg", Symbol: "RIU6"}
	if err := s.Put(spec); err != nil {
		t.Fatal(err)
	}
	deployed, params := s.Times("r1")
	if deployed != 1000 {
		t.Fatalf("deployed = %d, want 1000", deployed)
	}
	if params != 0 {
		t.Fatalf("params = %d, want 0 (not touched yet)", params)
	}

	// re-Put must preserve the original DeployedAtMs even as time moves on
	fakeNow = 2000
	if err := s.Put(spec); err != nil {
		t.Fatal(err)
	}
	if deployed, _ = s.Times("r1"); deployed != 1000 {
		t.Fatalf("re-put changed deployed = %d, want 1000 preserved", deployed)
	}

	// TouchParams sets/updates the second stamp only
	fakeNow = 3000
	if err := s.TouchParams("r1"); err != nil {
		t.Fatal(err)
	}
	deployed, params = s.Times("r1")
	if deployed != 1000 || params != 3000 {
		t.Fatalf("after touch: deployed=%d params=%d, want 1000/3000", deployed, params)
	}

	// RELOAD from disk — both stamps must survive
	s2, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	deployed, params = s2.Times("r1")
	if deployed != 1000 || params != 3000 {
		t.Fatalf("reload lost timestamps: deployed=%d params=%d", deployed, params)
	}
}

func TestUpdateParamsAppliesOnlyPresentFieldsAndPersists(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	fakeNow := int64(5000)
	s.nowMs = func() int64 { return fakeNow }

	spec := &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg", Symbol: "RIU6",
		Schedule: "09:00-23:55", MaxPositionContracts: 1, ParamsJson: `{"qty":1}`}
	if err := s.Put(spec); err != nil {
		t.Fatal(err)
	}

	newParams := `{"qty":2}`
	got, err := s.UpdateParams("r1", &newParams, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got.GetParamsJson() != newParams {
		t.Fatalf("params_json = %q, want %q", got.GetParamsJson(), newParams)
	}
	// nil pointers must leave schedule/max_position untouched
	if got.GetSchedule() != "09:00-23:55" {
		t.Fatalf("schedule changed unexpectedly: %q", got.GetSchedule())
	}
	if got.GetMaxPositionContracts() != 1 {
		t.Fatalf("max_position changed unexpectedly: %d", got.GetMaxPositionContracts())
	}
	if _, paramsMs := s.Times("r1"); paramsMs != fakeNow {
		t.Fatalf("params_updated_at_ms = %d, want %d", paramsMs, fakeNow)
	}

	// the ORIGINAL spec object (still held by the caller who built it above) must
	// not be mutated in place — Store swaps in a clone, never edits a shared pointer
	// another goroutine (e.g. a concurrent status-page read) may be holding.
	if spec.GetParamsJson() != `{"qty":1}` {
		t.Fatalf("original spec mutated in place: %q", spec.GetParamsJson())
	}

	// RELOAD from disk — the edit and its timestamp must survive.
	s2, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	reloaded := s2.Get("r1")
	if reloaded.GetParamsJson() != newParams {
		t.Fatalf("reload lost params_json: %q", reloaded.GetParamsJson())
	}
	if _, paramsMs := s2.Times("r1"); paramsMs != fakeNow {
		t.Fatalf("reload lost params timestamp: %d", paramsMs)
	}

	// a second call with different present fields (schedule + max_position, params
	// left nil) must change only those and leave params_json alone.
	newSchedule := "10:00-18:45"
	var newMax int64 = 3
	got2, err := s.UpdateParams("r1", nil, &newSchedule, &newMax)
	if err != nil {
		t.Fatal(err)
	}
	if got2.GetParamsJson() != newParams {
		t.Fatalf("params_json changed on a nil-params update: %q", got2.GetParamsJson())
	}
	if got2.GetSchedule() != newSchedule || got2.GetMaxPositionContracts() != newMax {
		t.Fatalf("schedule/max_position = %q/%d, want %q/%d",
			got2.GetSchedule(), got2.GetMaxPositionContracts(), newSchedule, newMax)
	}
}

func TestUpdateParamsUnknownIDReturnsErrNotFound(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpdateParams("ghost", nil, nil, nil); !errors.Is(err, ErrNotFound) {
		t.Fatalf("err = %v, want wrapping ErrNotFound", err)
	}
}

func TestSetPaperFlipsAndPersistsWithoutMutatingCallersPointer(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	spec := &quikv1.RobotSpec{RobotId: "r1", Symbol: "RIU6", Paper: true}
	if err := s.Put(spec); err != nil {
		t.Fatal(err)
	}

	got, err := s.SetPaper("r1", false)
	if err != nil {
		t.Fatal(err)
	}
	if got.GetPaper() {
		t.Fatal("SetPaper(r1, false) left Paper=true on the returned spec")
	}
	if !spec.GetPaper() {
		t.Fatal("original spec mutated in place: Paper flipped on the caller's own pointer")
	}

	s2, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if s2.Get("r1").GetPaper() {
		t.Fatal("reload lost the paper=false flip")
	}
}

func TestSetPaperUnknownIDReturnsErrNotFound(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.SetPaper("ghost", true); !errors.Is(err, ErrNotFound) {
		t.Fatalf("err = %v, want wrapping ErrNotFound", err)
	}
}

func TestStoreCorruptFileDoesNotBlockStartup(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "robots.json"), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewStore(dir); err == nil {
		t.Log("corrupt file tolerated by returning error — acceptable, caller logs it")
	}
}
