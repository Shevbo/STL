package watchdog

import "testing"

// The zombie we removed (the agent stand spammed "DDE hung (dde server not
// alive) — read-only restart attempt #N" every cycle and inflated the reconnect
// counter): quikdde.Alive() returns false whenever the DDE server thread is not
// running, and DDE is RETIRED so it is never running. stale() then reports hung
// on EVERY check. main.go now starts the watchdog only when
// quikdde.LegacyEnabled(); this test locks the logic that made that necessary.
func TestStaleReportsHungWhenServerNotAlive(t *testing.T) {
	w := New(Config{}, Deps{Alive: func() bool { return false }})
	hung, reason := w.stale()
	if !hung || reason != "dde server not alive" {
		t.Fatalf("dead DDE server must read as hung; got hung=%v reason=%q", hung, reason)
	}
}

// With legacy DDE OFF the health path feeds a vacuously-true Alive
// (quikdde.Alive() || !quikdde.LegacyEnabled()). Under that wiring a fresh feed
// must NOT be reported hung — the "server not alive" branch was the sole source
// of the false positives. (Config{} -> applyDefaults gives StaleAfter=60s, so a
// 1s-fresh feed is well within tolerance.)
func TestStaleNotHungWithVacuousAliveAndFreshFeed(t *testing.T) {
	w := New(Config{}, Deps{
		Alive:       func() bool { return true },   // DDE off -> vacuously alive
		FreshnessMs: func() int64 { return 1_000 }, // 1s: fresh
		HaveTicked:  func() bool { return true },
	})
	if hung, reason := w.stale(); hung {
		t.Fatalf("fresh feed with vacuous Alive must not be hung; reason=%q", reason)
	}
}
