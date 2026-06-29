package quikdde

import (
	"context"
	"fmt"
	"sync"
)

// Supervisor owns the lifecycle of a single DDE server so the watchdog can restart
// it (read-only recovery) without the rest of the agent caring about platform
// build tags. It wraps StartDDE / the returned stop func, both of which are defined
// per-platform (real on Windows, no-op stub elsewhere).
//
// READ-ONLY: this only stops and re-starts the inbound DDE channel. It never sends
// an order or any market transaction.
type Supervisor struct {
	dataRoot string

	mu   sync.Mutex
	stop func()
}

// NewSupervisor starts DDE for dataRoot and returns a supervisor that can restart
// it later. It returns the supervisor even on a start error so the watchdog can
// retry; err reports the initial start outcome.
func NewSupervisor(dataRoot string) (*Supervisor, error) {
	s := &Supervisor{dataRoot: dataRoot, stop: func() {}}
	stop, err := StartDDE(dataRoot)
	if err != nil {
		return s, err
	}
	s.stop = stop
	return s, nil
}

// Restart stops the current DDE server and starts a fresh one. It is safe for
// concurrent callers (serialised by the supervisor mutex). ctx is honoured only as
// a cancellation check before the (synchronous) restart; StartDDE itself is fast.
func (s *Supervisor) Restart(ctx context.Context) error {
	if ctx != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.stop != nil {
		s.stop()
		s.stop = func() {}
	}
	stop, err := StartDDE(s.dataRoot)
	if err != nil {
		return fmt.Errorf("quikdde restart: %w", err)
	}
	s.stop = stop
	return nil
}

// Stop tears down the current DDE server. Safe to call multiple times.
func (s *Supervisor) Stop() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stop != nil {
		s.stop()
		s.stop = func() {}
	}
}
