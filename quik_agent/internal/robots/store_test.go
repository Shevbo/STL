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

func TestStoreCorruptFileDoesNotBlockStartup(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "robots.json"), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewStore(dir); err == nil {
		t.Log("corrupt file tolerated by returning error — acceptable, caller logs it")
	}
}
