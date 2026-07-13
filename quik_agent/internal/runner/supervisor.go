package runner

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"
)

// Supervisor keeps the bundled robot-runner child process alive: start, wait
// for exit, back off, restart. Zero-touch: the agent is the single entrypoint;
// nobody ever starts the runner by hand.

type SupervisorCfg struct {
	// Start launches the runner and blocks until it exits. Injected for tests;
	// production uses ExecStart below.
	Start      func(ctx context.Context) error
	BackoffMin time.Duration
	BackoffMax time.Duration
	Logf       func(string, ...any)
}

type Supervisor struct{ cfg SupervisorCfg }

func NewSupervisor(cfg SupervisorCfg) *Supervisor {
	if cfg.BackoffMin == 0 {
		cfg.BackoffMin = time.Second
	}
	if cfg.BackoffMax == 0 {
		cfg.BackoffMax = 60 * time.Second
	}
	if cfg.Logf == nil {
		cfg.Logf = func(string, ...any) {}
	}
	return &Supervisor{cfg: cfg}
}

// Run supervises the runner until ctx is done. Backoff resets after a run that
// survived >60s (a healthy runner that later crashed restarts fast again).
func (s *Supervisor) Run(ctx context.Context) {
	backoff := s.cfg.BackoffMin
	for ctx.Err() == nil {
		started := time.Now()
		if err := s.cfg.Start(ctx); err != nil && ctx.Err() == nil {
			s.cfg.Logf("runner exited: %v", err)
		}
		if ctx.Err() != nil {
			return
		}
		if time.Since(started) > time.Minute {
			backoff = s.cfg.BackoffMin // healthy run — reset
		}
		s.cfg.Logf("restarting runner in %s", backoff)
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff *= 2; backoff > s.cfg.BackoffMax {
			backoff = s.cfg.BackoffMax
		}
	}
}

// ExecStart returns a production Start func launching exe with args, piping
// stdout/stderr lines into logf.
func ExecStart(exe string, args []string, logf func(string, ...any)) func(ctx context.Context) error {
	return func(ctx context.Context) error {
		cmd := exec.CommandContext(ctx, exe, args...)
		out, _ := cmd.StdoutPipe()
		errp, _ := cmd.StderrPipe()
		go pipeLines(out, logf)
		go pipeLines(errp, logf)
		if err := cmd.Start(); err != nil {
			return err
		}
		logf("runner started pid=%d", cmd.Process.Pid)
		return cmd.Wait()
	}
}

func pipeLines(r io.Reader, logf func(string, ...any)) {
	if r == nil {
		return
	}
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		logf("runner: %s", sc.Text())
	}
}

// FileTee returns a logf that forwards every line to base AND appends it to the
// file at path, so the agent-local status page has a runner.log to tail
// (GET /logs/runner). The file is truncated once at open, so it holds the
// current agent session's runner output. On an open error it logs once via base
// and returns base unchanged — a disk problem must never silence or block the
// runner supervisor. The returned logf is called from several goroutines (two
// pipeLines readers + the supervisor loop), so the file write is mutex-guarded.
//
// ponytail: truncated once per agent start, then append-open-close per line, no
// rotation. The runner logs line-per-event so the per-line open is negligible and
// avoids a long-held handle (survives external deletion/rotation); the page tails
// only the last 64KiB. Add rotation only if runner.log ever bloats the VDS disk.
func FileTee(path string, base func(string, ...any)) func(string, ...any) {
	// Truncate once at open so the file holds the current agent session's output.
	f, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		base("runner-log: cannot open %s: %v (console only)", path, err)
		return base
	}
	f.Close()
	var mu sync.Mutex
	return func(format string, a ...any) {
		base(format, a...)
		mu.Lock()
		defer mu.Unlock()
		lf, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			return // a disk error must never block the runner supervisor
		}
		fmt.Fprintf(lf, format+"\n", a...)
		lf.Close()
	}
}
