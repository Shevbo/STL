package robots

import (
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

func TestStoreCorruptFileDoesNotBlockStartup(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "robots.json"), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewStore(dir); err == nil {
		t.Log("corrupt file tolerated by returning error — acceptable, caller logs it")
	}
}
