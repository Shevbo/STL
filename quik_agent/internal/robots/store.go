// Package robots persists agent-hosted RobotSpecs locally so deployed robots
// auto-resume after an agent/VDS restart without STL (zero-touch startup).
package robots

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"

	"google.golang.org/protobuf/encoding/protojson"

	quikv1 "shectory/quik_agent/internal/pb"
)

type entry struct {
	Spec   json.RawMessage `json:"spec"` // protojson-encoded RobotSpec
	Paused bool            `json:"paused"`
}

// Store is a mutex-guarded map of RobotSpecs mirrored to robots.json in dir.
// Every mutation rewrites the file atomically (tmp + rename).
type Store struct {
	mu     sync.Mutex
	path   string
	specs  map[string]*quikv1.RobotSpec
	paused map[string]bool
}

func NewStore(dir string) (*Store, error) {
	s := &Store{
		path:   filepath.Join(dir, "robots.json"),
		specs:  map[string]*quikv1.RobotSpec{},
		paused: map[string]bool{},
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
		m[id] = entry{Spec: b, Paused: s.paused[id]}
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
	s.specs[spec.GetRobotId()] = spec
	return s.flushLocked()
}

func (s *Store) Delete(robotID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.specs, robotID)
	delete(s.paused, robotID)
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
