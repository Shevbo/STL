// Package robots persists agent-hosted RobotSpecs locally so deployed robots
// auto-resume after an agent/VDS restart without STL (zero-touch startup).
package robots

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"

	"google.golang.org/protobuf/encoding/protojson"

	quikv1 "shectory/quik_agent/internal/pb"
)

type entry struct {
	Spec              json.RawMessage `json:"spec"` // protojson-encoded RobotSpec
	Paused            bool            `json:"paused"`
	DeployedAtMs      int64           `json:"deployed_at_ms"`
	ParamsUpdatedAtMs int64           `json:"params_updated_at_ms"`
}

// Store is a mutex-guarded map of RobotSpecs mirrored to robots.json in dir.
// Every mutation rewrites the file atomically (tmp + rename).
type Store struct {
	mu     sync.Mutex
	path   string
	specs  map[string]*quikv1.RobotSpec
	paused map[string]bool
	times  map[string][2]int64 // [0]=deployedAtMs [1]=paramsUpdatedAtMs
	nowMs  func() int64
}

func NewStore(dir string) (*Store, error) {
	s := &Store{
		path:   filepath.Join(dir, "robots.json"),
		specs:  map[string]*quikv1.RobotSpec{},
		paused: map[string]bool{},
		times:  map[string][2]int64{},
		nowMs:  func() int64 { return time.Now().UnixMilli() },
	}
	raw, err := os.ReadFile(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return s, nil
		}
		return nil, err
	}
	var m map[string]entry
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	for id, e := range m {
		spec := &quikv1.RobotSpec{}
		if err := protojson.Unmarshal(e.Spec, spec); err != nil {
			continue // skip a corrupt entry, never fail startup on one robot
		}
		s.specs[id] = spec
		s.paused[id] = e.Paused
		// zero value on legacy entries predating these fields — "not set yet"
		s.times[id] = [2]int64{e.DeployedAtMs, e.ParamsUpdatedAtMs}
	}
	return s, nil
}

func (s *Store) flushLocked() error {
	m := map[string]entry{}
	for id, spec := range s.specs {
		b, err := protojson.Marshal(spec)
		if err != nil {
			return err
		}
		t := s.times[id]
		m[id] = entry{Spec: b, Paused: s.paused[id], DeployedAtMs: t[0], ParamsUpdatedAtMs: t[1]}
	}
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.path) // atomic on the same volume
}

func (s *Store) Put(spec *quikv1.RobotSpec) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	id := spec.GetRobotId()
	s.specs[id] = spec
	if t := s.times[id]; t[0] == 0 { // treat missing/0 as "not deployed yet"
		t[0] = s.nowMs()
		s.times[id] = t
	}
	return s.flushLocked()
}

// TouchParams stamps ParamsUpdatedAtMs=now for robotID (called whenever the
// runner/STL edits a deployed robot's params, distinct from the initial deploy).
func (s *Store) TouchParams(robotID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.times[robotID]
	t[1] = s.nowMs()
	s.times[robotID] = t
	return s.flushLocked()
}

// Times returns the persisted deploy/params-edit timestamps for robotID (zero
// value if never set, e.g. legacy entries predating these fields).
func (s *Store) Times(robotID string) (deployedMs, paramsMs int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.times[robotID]
	return t[0], t[1]
}

func (s *Store) Delete(robotID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.specs, robotID)
	delete(s.paused, robotID)
	delete(s.times, robotID)
	return s.flushLocked()
}

func (s *Store) Get(robotID string) *quikv1.RobotSpec {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.specs[robotID]
}

func (s *Store) All() []*quikv1.RobotSpec {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]*quikv1.RobotSpec, 0, len(s.specs))
	for _, sp := range s.specs {
		out = append(out, sp)
	}
	return out
}

func (s *Store) SetPaused(robotID string, paused bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.paused[robotID] = paused
	return s.flushLocked()
}

func (s *Store) Paused(robotID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.paused[robotID]
}
