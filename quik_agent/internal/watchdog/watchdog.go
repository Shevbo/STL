// Package watchdog is the supervisor goroutine that gives the agent its
// "иммунитет к зависаниям" (immunity to hangs). It watches DDE liveness and last-
// tick staleness; when ticks go stale beyond a configurable threshold (or the DDE
// server thread has died) it attempts a READ-ONLY DDE restart / reconnect, counts
// the reconnect, and backs off so a wedged QUIK does not get hammered.
//
// HARD CONSTRAINT (Guard 3): this is read-only recovery ONLY. The watchdog can
// stop and re-StartDDE the inbound DDE channel and nothing else. It never places,
// moves, or cancels an order, and it issues no market transaction.
//
// Dependencies are injected as function values (Deps) so the supervisor logic is
// unit-testable without a real DDE server or clock.
package watchdog

import (
	"context"
	"fmt"
	"sync/atomic"
	"time"
)

// Deps are the injected hooks the watchdog drives. In production these are wired
// to quikdde: Alive -> quikdde.Alive, FreshnessMs -> Provider.FreshnessMs,
// RestartDDE -> stop the current DDE server and quikdde.StartDDE again.
type Deps struct {
	// Alive reports whether the DDE server thread is up (quikdde.Alive).
	Alive func() bool
	// FreshnessMs returns the staleness of the freshest tick in ms; 0 means no
	// data seen yet (Provider.FreshnessMs).
	FreshnessMs func() int64
	// HaveTicked reports whether at least one tick has ever arrived. When false a
	// zero FreshnessMs must not read as "fresh".
	HaveTicked func() bool
	// RestartDDE performs the read-only DDE restart. It must stop the existing DDE
	// server and start a new one, returning an error if the restart failed. It must
	// NOT place any order.
	RestartDDE func(ctx context.Context) error
	// OnReconnectAttempt is called (best-effort) each time a restart is attempted,
	// after the reconnect counter is bumped. Optional.
	OnReconnectAttempt func(count uint32, reason string)
	// Logf logs a line. Optional; defaults to a no-op.
	Logf func(format string, args ...any)
}

// Config holds the watchdog thresholds and cadence.
type Config struct {
	// CheckInterval is how often the watchdog samples DDE health.
	CheckInterval time.Duration
	// StaleAfter is the tick-staleness that triggers a restart attempt. Matches
	// config.DDEDownMs (the DOWN threshold), in time.Duration form.
	StaleAfter time.Duration
	// MinBackoff / MaxBackoff bound the restart back-off.
	MinBackoff time.Duration
	MaxBackoff time.Duration
}

func (c *Config) applyDefaults() {
	if c.CheckInterval <= 0 {
		c.CheckInterval = 10 * time.Second
	}
	if c.StaleAfter <= 0 {
		c.StaleAfter = 60 * time.Second
	}
	if c.MinBackoff <= 0 {
		c.MinBackoff = 5 * time.Second
	}
	if c.MaxBackoff <= 0 {
		c.MaxBackoff = 2 * time.Minute
	}
}

// Watchdog supervises DDE liveness. Construct with New, run with Run.
type Watchdog struct {
	cfg        Config
	deps       Deps
	reconnects atomic.Uint32
}

// New builds a Watchdog. Missing Deps callbacks default to safe no-ops where it is
// meaningful; RestartDDE / Alive / FreshnessMs are required for real recovery.
func New(cfg Config, deps Deps) *Watchdog {
	cfg.applyDefaults()
	if deps.Logf == nil {
		deps.Logf = func(string, ...any) {}
	}
	if deps.HaveTicked == nil {
		// If not supplied, treat any non-zero freshness as "have ticked".
		fm := deps.FreshnessMs
		deps.HaveTicked = func() bool { return fm != nil && fm() > 0 }
	}
	return &Watchdog{cfg: cfg, deps: deps}
}

// Reconnects returns the number of DDE restart attempts made since start. The link
// surfaces this in Diagnostics.reconnects_since_start.
func (w *Watchdog) Reconnects() uint32 { return w.reconnects.Load() }

// stale reports whether the DDE channel is currently considered hung: either the
// server thread is not alive, or ticks have gone stale past StaleAfter.
func (w *Watchdog) stale() (bool, string) {
	if w.deps.Alive != nil && !w.deps.Alive() {
		return true, "dde server not alive"
	}
	if w.deps.FreshnessMs == nil {
		return false, ""
	}
	// No data yet is not (by itself) a hang at startup; the link's health package
	// reports DEGRADED for that. The watchdog only acts on a server that WAS
	// ticking and went stale, or a dead server thread (handled above).
	if w.deps.HaveTicked != nil && !w.deps.HaveTicked() {
		return false, ""
	}
	age := time.Duration(w.deps.FreshnessMs()) * time.Millisecond
	if age >= w.cfg.StaleAfter {
		return true, fmt.Sprintf("ticks stale %s >= %s", age, w.cfg.StaleAfter)
	}
	return false, ""
}

// Run supervises DDE until ctx is cancelled. On detected staleness it attempts a
// read-only restart, increments the reconnect counter, and backs off (exponential,
// reset once healthy again). It never returns an error; ctx cancellation ends it.
func (w *Watchdog) Run(ctx context.Context) {
	tick := time.NewTicker(w.cfg.CheckInterval)
	defer tick.Stop()

	backoff := w.cfg.MinBackoff
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}

		hung, reason := w.stale()
		if !hung {
			backoff = w.cfg.MinBackoff // healthy: reset back-off
			continue
		}

		n := w.reconnects.Add(1)
		w.deps.Logf("watchdog: DDE hung (%s) — read-only restart attempt #%d", reason, n)
		if w.deps.OnReconnectAttempt != nil {
			w.deps.OnReconnectAttempt(n, reason)
		}

		if w.deps.RestartDDE != nil {
			if err := w.deps.RestartDDE(ctx); err != nil {
				w.deps.Logf("watchdog: DDE restart failed: %v", err)
			} else {
				w.deps.Logf("watchdog: DDE restart issued")
			}
		}

		// Back off before re-checking so a wedged QUIK is not hammered.
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff < w.cfg.MaxBackoff {
			backoff *= 2
			if backoff > w.cfg.MaxBackoff {
				backoff = w.cfg.MaxBackoff
			}
		}
	}
}
