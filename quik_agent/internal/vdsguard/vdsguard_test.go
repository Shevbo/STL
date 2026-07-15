package vdsguard

import (
	"strings"
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

type harness struct {
	g        *Guard
	now      int64
	pongAge  int64
	rtt      int64
	folder   string
	mem      MemStatus
	memOK    bool
	alerts   []string
	restarts []string
	fail     bool
}

func newHarness(cfg Config) *harness {
	h := &harness{now: 1_784_100_000_000, rtt: 20, folder: `C:\QUIK`}
	h.g = New(cfg, Deps{
		Pong:  func() (int64, int64, string) { return h.pongAge, h.rtt, h.folder },
		Alert: func(_ quikv1.AlertSeverity, code, _ string) { h.alerts = append(h.alerts, code) },
		RestartQuik: func(folder string) error {
			h.restarts = append(h.restarts, folder)
			if h.fail {
				return errAny
			}
			return nil
		},
		Mem: func() (MemStatus, bool) { return h.mem, h.memOK },
		Now: func() int64 { return h.now },
	})
	return h
}

var errAny = &strErr{"boom"}

type strErr struct{ s string }

func (e *strErr) Error() string { return e.s }

func TestHealthyQuikNoActions(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge = 4000
	h.g.tick()
	if len(h.alerts) != 0 || len(h.restarts) != 0 || h.g.Status().QuikState != "OK" {
		t.Fatalf("healthy pong acted: %+v %+v", h.alerts, h.restarts)
	}
}

func TestSlowQuikAlertsThrottled(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge = 30_000 // > SlowMs 15s, < HungMs
	h.g.tick()
	h.g.tick() // within throttle window
	if got := strings.Join(h.alerts, ","); got != "QUIK_SLOW" {
		t.Fatalf("want one throttled QUIK_SLOW, got %q", got)
	}
	if len(h.restarts) != 0 || h.g.Status().QuikState != "SLOW" {
		t.Fatalf("slow must not restart")
	}
}

func TestHungQuikRestartsWithCooldown(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge = 400_000 // > HungMs 300s
	h.g.tick()
	if len(h.restarts) != 1 || h.restarts[0] != `C:\QUIK` {
		t.Fatalf("want 1 restart from the pong folder, got %+v", h.restarts)
	}
	if h.g.Status().QuikState != "HUNG" || h.g.Status().RestartsToday != 1 {
		t.Fatalf("bad view: %+v", h.g.Status())
	}
	h.now += 60_000 // inside cooldown 900s
	h.g.tick()
	if len(h.restarts) != 1 {
		t.Fatalf("cooldown violated: %+v", h.restarts)
	}
	h.now += 900_000
	h.g.tick()
	if len(h.restarts) != 2 {
		t.Fatalf("want a second restart after cooldown, got %+v", h.restarts)
	}
}

func TestHungWithoutFolderNeverKillsBlind(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge, h.folder = 400_000, ""
	h.g.tick()
	if len(h.restarts) != 0 {
		t.Fatalf("restarted without a known QUIK folder")
	}
	if got := strings.Join(h.alerts, ","); got != "QUIK_HUNG" {
		t.Fatalf("want manual-restart alert, got %q", got)
	}
}

func TestDisabledGuardIsInert(t *testing.T) {
	h := newHarness(Config{Disabled: true})
	h.pongAge = 999_000
	h.g.tick()
	if len(h.alerts)+len(h.restarts) != 0 || h.g.Status().QuikState != "DISABLED" {
		t.Fatalf("disabled guard acted: %+v %+v", h.alerts, h.restarts)
	}
}

func TestNoPongEverAlertsButNeverRestarts(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge, h.rtt = -1, -1
	h.g.tick()
	if len(h.restarts) != 0 {
		t.Fatalf("restarted a QUIK that never proved alive this session")
	}
	if got := strings.Join(h.alerts, ","); got != "QUIK_NO_PONG" {
		t.Fatalf("want QUIK_NO_PONG, got %q", got)
	}
}

func TestLowMemoryAlertThrottled(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge = 4000
	h.memOK = true
	h.mem = MemStatus{LoadPct: 95, TotalMB: 4096, AvailMB: 120, CommitPct: 97}
	h.g.tick()
	h.g.tick()
	if got := strings.Join(h.alerts, ","); got != "VDS_LOW_MEMORY" {
		t.Fatalf("want one throttled VDS_LOW_MEMORY, got %q", got)
	}
	if !h.g.Status().LowMemory || h.g.Status().Mem == nil {
		t.Fatalf("view must expose low-memory state: %+v", h.g.Status())
	}
}

func TestRestartFailureAlerts(t *testing.T) {
	h := newHarness(Config{})
	h.pongAge, h.fail = 400_000, true
	h.g.tick()
	if got := strings.Join(h.alerts, ","); got != "QUIK_HUNG_RESTART,QUIK_RESTART_FAILED" {
		t.Fatalf("want restart+failure alerts, got %q", got)
	}
}

func TestAutostartBat(t *testing.T) {
	got := AutostartBat(`C:\QuikFinam`, `C:\distr\dist`, "quik-agent_amd64.exe")
	for _, want := range []string{
		`start "" "C:\QuikFinam\info.exe"`,
		"timeout /t 25 /nobreak >nul",
		`cd /d "C:\distr\dist"`,
		`start "" "C:\distr\dist\quik-agent_amd64.exe"`,
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("bat missing %q:\n%s", want, got)
		}
	}
	// unknown QUIK folder: agent-only launcher, no blind info.exe start
	if got := AutostartBat("", `C:\d`, "a.exe"); strings.Contains(got, "info.exe") {
		t.Fatalf("bat must not start an unknown QUIK: %s", got)
	}
}
