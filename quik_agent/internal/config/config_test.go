package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
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
	if cfg.ReconManualOffset == nil || len(cfg.ReconManualOffset) != 0 {
		t.Errorf("ReconManualOffset = %v, want non-nil empty map", cfg.ReconManualOffset)
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

func TestLoadOrInit_ReconManualOffsetExplicitEmptyStaysNonNil(t *testing.T) {
	dir := t.TempDir()
	path := writeConfig(t, dir, `{"recon_manual_offset": {}}`)
	cfg, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit: %v", err)
	}
	if cfg.ReconManualOffset == nil {
		t.Error("explicit {} must decode to a non-nil empty map")
	}
}

func TestLoadOrInit_ReconManualOffsetPreservesValues(t *testing.T) {
	dir := t.TempDir()
	path := writeConfig(t, dir, `{"recon_manual_offset": {"RIU6": 2}}`)
	cfg, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit: %v", err)
	}
	if cfg.ReconManualOffset["RIU6"] != 2 {
		t.Errorf("ReconManualOffset[RIU6] = %d, want 2", cfg.ReconManualOffset["RIU6"])
	}
}

func TestManualOffsets_GetIsDefensiveCopy(t *testing.T) {
	m := NewManualOffsets(filepath.Join(t.TempDir(), "agent_config.json"), Config{})
	got := m.Get()
	got["RIU6"] = 99
	if v := m.Get()["RIU6"]; v != 0 {
		t.Errorf("mutating Get()'s result leaked into the store: RIU6=%d", v)
	}
}

func TestManualOffsets_SetPersistsAndRoundTrips(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent_config.json")
	m := NewManualOffsets(path, Config{STLGRPCURL: "x:1"})
	if err := m.Set(map[string]int64{"RIU6": 3, "GZU6": -1}); err != nil {
		t.Fatalf("Set: %v", err)
	}
	if got := m.Get(); got["RIU6"] != 3 || got["GZU6"] != -1 {
		t.Errorf("Get after Set = %v", got)
	}
	// The full config (including unrelated fields) must have been persisted.
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read persisted config: %v", err)
	}
	var onDisk Config
	if err := json.Unmarshal(b, &onDisk); err != nil {
		t.Fatalf("persisted config is not valid JSON: %v", err)
	}
	if onDisk.STLGRPCURL != "x:1" {
		t.Errorf("persisted config lost an unrelated field: %+v", onDisk)
	}
	if onDisk.ReconManualOffset["RIU6"] != 3 {
		t.Errorf("persisted recon_manual_offset = %v", onDisk.ReconManualOffset)
	}
}

func TestManualOffsets_SetSurvivesOutOfBandHandEdit(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent_config.json")
	// Startup state: agent loaded this config...
	if err := Save(path, Config{STLGRPCURL: "x:1", InstrumentWhitelist: []string{"RIU6"}}); err != nil {
		t.Fatalf("seed Save: %v", err)
	}
	cfg, err := LoadOrInit(path, dir)
	if err != nil {
		t.Fatalf("LoadOrInit: %v", err)
	}
	m := NewManualOffsets(path, cfg)
	// ...then an operator hand-edits ANOTHER field out-of-band...
	if err := os.WriteFile(path,
		[]byte(`{"stl_grpc_url":"x:1","instrument_whitelist":["RIU6","GZU6"]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	// ...and a manual-offset POST lands. The hand-edit must SURVIVE.
	if err := m.Set(map[string]int64{"RIU6": 7}); err != nil {
		t.Fatalf("Set: %v", err)
	}
	onDisk, err := loadFile(path)
	if err != nil {
		t.Fatalf("reload persisted config: %v", err)
	}
	if len(onDisk.InstrumentWhitelist) != 2 || onDisk.InstrumentWhitelist[1] != "GZU6" {
		t.Errorf("out-of-band whitelist edit clobbered by Set: %v", onDisk.InstrumentWhitelist)
	}
	if onDisk.ReconManualOffset["RIU6"] != 7 {
		t.Errorf("persisted recon_manual_offset = %v, want RIU6:7", onDisk.ReconManualOffset)
	}
}

func TestManualOffsets_SetOnCorruptFileErrorsAndWritesNothing(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent_config.json")
	corrupt := `{"stl_grpc_url": TRUNCATED`
	if err := os.WriteFile(path, []byte(corrupt), 0o644); err != nil {
		t.Fatal(err)
	}
	m := NewManualOffsets(path, Config{STLGRPCURL: "x:1"})
	if err := m.Set(map[string]int64{"RIU6": 1}); err == nil {
		t.Fatal("Set over a corrupt config must error, not overwrite it")
	}
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(b) != corrupt {
		t.Errorf("corrupt file was rewritten: %q", b)
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

func TestManualOffsets_ConcurrentSetDoesNotCorruptFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent_config.json")
	m := NewManualOffsets(path, Config{})
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		i := i
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = m.Set(map[string]int64{"RIU6": int64(i)})
		}()
	}
	wg.Wait()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	var onDisk Config
	if err := json.Unmarshal(b, &onDisk); err != nil {
		t.Fatalf("concurrent Set corrupted the file: %v\ncontent: %s", err, b)
	}
}
