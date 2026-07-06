package config

import (
	"os"
	"path/filepath"
	"testing"
)

func writeConfig(t *testing.T, dir, body string) string {
	t.Helper()
	path := filepath.Join(dir, "agent_config.json")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestLoadOrInit_NewFieldsDefaultOnMissingKeys(t *testing.T) {
	dir := t.TempDir()
	path := writeConfig(t, dir, `{"stl_grpc_url":"x:1"}`)
	cfg, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit: %v", err)
	}
	if cfg.StatusPort != 8071 {
		t.Errorf("StatusPort = %d, want 8071 (default, key absent)", cfg.StatusPort)
	}
	if cfg.StatusSnapshotMinSec != 5 {
		t.Errorf("StatusSnapshotMinSec = %d, want 5", cfg.StatusSnapshotMinSec)
	}
}

func TestLoadOrInit_ExplicitStatusPortZeroSurvivesReload(t *testing.T) {
	dir := t.TempDir()
	path := writeConfig(t, dir, `{"status_port": 0}`)
	cfg, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit: %v", err)
	}
	if cfg.StatusPort != 0 {
		t.Errorf("StatusPort = %d, want 0 (explicit disable)", cfg.StatusPort)
	}
	// A second load (simulating a restart) must not resurrect the default —
	// this is the whole point of consulting the raw JSON for presence.
	cfg2, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit (reload): %v", err)
	}
	if cfg2.StatusPort != 0 {
		t.Errorf("StatusPort after reload = %d, want still 0", cfg2.StatusPort)
	}
}

func TestLoadOrInit_ExplicitStatusPortNonzeroPreserved(t *testing.T) {
	dir := t.TempDir()
	path := writeConfig(t, dir, `{"status_port": 9100}`)
	cfg, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit: %v", err)
	}
	if cfg.StatusPort != 9100 {
		t.Errorf("StatusPort = %d, want 9100", cfg.StatusPort)
	}
}

func TestSave_AtomicRoundTripAndNoTmpLeftover(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent_config.json")
	want := Config{STLGRPCURL: "x:1", StatusPort: 9100}
	if err := Save(path, want); err != nil {
		t.Fatalf("Save: %v", err)
	}
	got, err := loadFile(path)
	if err != nil {
		t.Fatalf("loadFile after Save: %v", err)
	}
	if got.STLGRPCURL != "x:1" || got.StatusPort != 9100 {
		t.Errorf("round-trip = %+v", got)
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Errorf(".tmp sibling left behind after a successful Save (stat err=%v)", err)
	}
}
