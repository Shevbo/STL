package config

import (
	"fmt"
	"os"
	"sync"
)

// ManualOffsets is the concurrency-safe accessor over Config.ReconManualOffset,
// backed by agent_config.json. Deps.ManualGet/ManualSet (internal/status) call
// Get/Set from the status HTTP server's own request goroutine, so Set must
// serialize concurrent POST /api/manual-offset requests: the mutex here
// guarantees two overlapping writes cannot interleave and corrupt the file.
type ManualOffsets struct {
	mu   sync.Mutex
	path string
	// fallback is the construction-time Config snapshot, used ONLY when the
	// file does not exist at Set time (first persist). When the file exists,
	// Set re-loads it fresh so an out-of-band operator hand-edit (whitelist,
	// ports, ...) made after startup is never clobbered by a stale snapshot.
	fallback Config
	// offsets is the in-memory authority Get serves; kept in sync with the
	// last successful Set (and seeded from the construction snapshot).
	offsets map[string]int64
}

// NewManualOffsets wraps cfg's manual-offset map, persisting future edits back
// to path (normally the same agent_config.json the agent loaded at startup).
func NewManualOffsets(path string, cfg Config) *ManualOffsets {
	m := &ManualOffsets{path: path, fallback: cfg, offsets: map[string]int64{}}
	for k, v := range cfg.ReconManualOffset {
		m.offsets[k] = v
	}
	return m
}

// Get returns a defensive copy of the current map (Deps.ManualGet).
func (m *ManualOffsets) Get() map[string]int64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]int64, len(m.offsets))
	for k, v := range m.offsets {
		out[k] = v
	}
	return out
}

// Set replaces the map wholesale (never a merge — matches the status page's
// full-map POST body) and persists it to agent_config.json before returning,
// so a confirmed edit survives a restart immediately (Deps.ManualSet).
//
// The config is RE-LOADED fresh from disk first (same parse LoadOrInit uses,
// no wizard) and only ReconManualOffset is changed on that freshly-loaded
// struct: an operator hand-edit made to any other field after the agent
// started survives a manual-offset POST. An unreadable or corrupt file is an
// error and NOTHING is written (a broken config must be fixed by a human,
// not silently overwritten with a startup-time snapshot). A missing file is
// the one exception: first persist falls back to the construction snapshot.
func (m *ManualOffsets) Set(offsets map[string]int64) error {
	cp := make(map[string]int64, len(offsets))
	for k, v := range offsets {
		cp[k] = v
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	fresh, err := loadFile(m.path)
	if err != nil {
		if !os.IsNotExist(err) {
			return fmt.Errorf("manual-offset: refusing to overwrite unreadable config: %w", err)
		}
		fresh = m.fallback // first persist: no file on disk yet
	}

	fresh.ReconManualOffset = cp
	if err := Save(m.path, fresh); err != nil {
		return err
	}
	m.offsets = cp
	return nil
}
