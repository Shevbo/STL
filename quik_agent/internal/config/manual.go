package config

import "sync"

// ManualOffsets is the concurrency-safe accessor over Config.ReconManualOffset,
// backed by agent_config.json. Deps.ManualGet/ManualSet (internal/status) call
// Get/Set from the status HTTP server's own request goroutine, so Set must
// serialize concurrent POST /api/manual-offset requests: the mutex here
// guarantees two overlapping writes cannot interleave and corrupt the file.
type ManualOffsets struct {
	mu   sync.Mutex
	path string
	cfg  Config // snapshot taken at construction; only ReconManualOffset is mutated here
}

// NewManualOffsets wraps cfg's manual-offset map, persisting future edits back
// to path (normally the same agent_config.json the agent loaded at startup).
func NewManualOffsets(path string, cfg Config) *ManualOffsets {
	m := &ManualOffsets{path: path, cfg: cfg}
	if m.cfg.ReconManualOffset == nil {
		m.cfg.ReconManualOffset = map[string]int64{}
	}
	return m
}

// Get returns a defensive copy of the current map (Deps.ManualGet).
func (m *ManualOffsets) Get() map[string]int64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]int64, len(m.cfg.ReconManualOffset))
	for k, v := range m.cfg.ReconManualOffset {
		out[k] = v
	}
	return out
}

// Set replaces the map wholesale (never a merge — matches the status page's
// full-map POST body) and persists the full config to agent_config.json
// before returning, so a confirmed edit survives a restart immediately
// (Deps.ManualSet).
func (m *ManualOffsets) Set(offsets map[string]int64) error {
	cp := make(map[string]int64, len(offsets))
	for k, v := range offsets {
		cp[k] = v
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.cfg.ReconManualOffset = cp
	return Save(m.path, m.cfg)
}
