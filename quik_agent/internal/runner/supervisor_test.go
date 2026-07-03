package runner

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

func TestSupervisorRestartsAndBacksOff(t *testing.T) {
	var starts int32
	s := NewSupervisor(SupervisorCfg{
		Start: func(ctx context.Context) error { // test seam instead of exec.Command
			atomic.AddInt32(&starts, 1)
			return nil // child "exited" instantly
		},
		BackoffMin: 10 * time.Millisecond,
		BackoffMax: 40 * time.Millisecond,
		Logf:       func(string, ...any) {},
	})
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	s.Run(ctx)
	n := atomic.LoadInt32(&starts)
	if n < 3 || n > 20 {
		t.Fatalf("starts = %d, want a handful with backoff", n)
	}
}

func TestSupervisorStopsOnCancel(t *testing.T) {
	started := make(chan struct{}, 1)
	s := NewSupervisor(SupervisorCfg{
		Start: func(ctx context.Context) error {
			started <- struct{}{}
			<-ctx.Done() // long-lived child; exits on cancel
			return ctx.Err()
		},
		Logf: func(string, ...any) {},
	})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()
	<-started
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("supervisor did not stop on cancel")
	}
}
