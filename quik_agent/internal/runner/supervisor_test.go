package runner

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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

func TestFileTeeForwardsAndWritesTruncatedFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "runner.log")
	// Stale content from a prior session must be truncated at open.
	if err := os.WriteFile(path, []byte("STALE-PREVIOUS-SESSION\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	var forwarded []string
	logf := FileTee(path, func(f string, a ...any) {
		forwarded = append(forwarded, fmt.Sprintf(f, a...))
	})
	logf("runner: %s pid=%d", "started", 42)
	logf("runner-sup: restarting in %s", "10ms")

	// base still receives every line (console output preserved)
	if len(forwarded) != 2 || forwarded[0] != "runner: started pid=42" {
		t.Fatalf("base not forwarded verbatim: %v", forwarded)
	}
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	got := string(b)
	if strings.Contains(got, "STALE") {
		t.Fatalf("file not truncated at open: %q", got)
	}
	if !strings.Contains(got, "runner: started pid=42\n") ||
		!strings.Contains(got, "runner-sup: restarting in 10ms\n") {
		t.Fatalf("file missing teed lines: %q", got)
	}
}

func TestFileTeeFallsBackToBaseOnOpenError(t *testing.T) {
	// An unwritable path must not lose console logging: FileTee returns base.
	var got int
	base := func(string, ...any) { got++ }
	logf := FileTee(filepath.Join(t.TempDir(), "no-such-dir", "runner.log"), base)
	logf("hello %d", 1)
	if got == 0 {
		t.Fatal("base logf must still fire when the log file cannot be opened")
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
