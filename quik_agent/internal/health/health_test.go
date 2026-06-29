package health

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

const (
	up   = quikv1.ChannelState_CHANNEL_STATE_UP
	deg  = quikv1.ChannelState_CHANNEL_STATE_DEGRADED
	down = quikv1.ChannelState_CHANNEL_STATE_DOWN
)

// TestClassifyDDE checks threshold -> ChannelState for the DDE channel.
func TestClassifyDDE(t *testing.T) {
	th := Thresholds{StaleTickMs: 30_000, DDEDownMs: 60_000}
	tests := []struct {
		name string
		in   Inputs
		want quikv1.ChannelState
	}{
		{"server down", Inputs{DDEServerAlive: false, HaveTicked: true, LastTickAgeMs: 0}, down},
		{"server up, no tick yet", Inputs{DDEServerAlive: true, HaveTicked: false}, deg},
		{"fresh tick", Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 1_000}, up},
		{"just below stale", Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 29_999}, up},
		{"at stale boundary", Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 30_000}, deg},
		{"between stale and down", Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 45_000}, deg},
		{"at down boundary", Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 60_000}, down},
		{"far past down", Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 120_000}, down},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := classifyDDE(tc.in, th.normalize())
			if got != tc.want {
				t.Fatalf("classifyDDE(%s) = %v, want %v", tc.name, got, tc.want)
			}
		})
	}
}

// TestEvaluateQuikLink checks quik/link classification.
func TestEvaluateQuikLink(t *testing.T) {
	base := Inputs{DDEServerAlive: true, HaveTicked: true, LastTickAgeMs: 1_000}
	tests := []struct {
		name      string
		quikAlive bool
		linkConn  bool
		wantQuik  quikv1.ChannelState
		wantLink  quikv1.ChannelState
	}{
		{"all up", true, true, up, up},
		{"quik down", false, true, down, up},
		{"link down", true, false, up, down},
		{"both down", false, false, down, down},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			in := base
			in.QuikAlive = tc.quikAlive
			in.LinkConnected = tc.linkConn
			s := Evaluate(in, Defaults())
			if s.Quik != tc.wantQuik {
				t.Errorf("quik = %v, want %v", s.Quik, tc.wantQuik)
			}
			if s.Link != tc.wantLink {
				t.Errorf("link = %v, want %v", s.Link, tc.wantLink)
			}
		})
	}
}

// TestThresholdOrderingGuard verifies a misconfigured DOWN < stale gets clamped.
func TestThresholdOrderingGuard(t *testing.T) {
	th := Thresholds{StaleTickMs: 50_000, DDEDownMs: 10_000}.normalize()
	if th.DDEDownMs < th.StaleTickMs {
		t.Fatalf("guard failed: down %d < stale %d", th.DDEDownMs, th.StaleTickMs)
	}
}

// alertByCode finds an emitted alert by code.
func alertByCode(specs []AlertSpec, code string) (AlertSpec, bool) {
	for _, a := range specs {
		if a.Code == code {
			return a, true
		}
	}
	return AlertSpec{}, false
}

// TestMonitorTransitions checks transition -> Alert severity + code.
func TestMonitorTransitions(t *testing.T) {
	m := NewMonitor()

	// First step at all-UP: priming against an UP baseline => no alerts.
	if got := m.Step(Snapshot{DDE: up, Quik: up, Link: up}); len(got) != 0 {
		t.Fatalf("priming all-UP should emit no alerts, got %v", got)
	}

	// DDE UP -> DOWN => CRITICAL DDE_DOWN, SMS-dubbed.
	got := m.Step(Snapshot{DDE: down, Quik: up, Link: up})
	a, ok := alertByCode(got, CodeDDEDown)
	if !ok {
		t.Fatalf("expected %s, got %v", CodeDDEDown, got)
	}
	if a.Severity != quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL {
		t.Errorf("DDE_DOWN severity = %v, want CRITICAL", a.Severity)
	}
	if !a.SMSDub {
		t.Errorf("DDE_DOWN should be SMS-dubbed")
	}

	// DDE DOWN -> UP => INFO DDE_RECOVERED.
	got = m.Step(Snapshot{DDE: up, Quik: up, Link: up})
	a, ok = alertByCode(got, CodeDDERecovered)
	if !ok {
		t.Fatalf("expected %s, got %v", CodeDDERecovered, got)
	}
	if a.Severity != quikv1.AlertSeverity_ALERT_SEVERITY_INFO {
		t.Errorf("DDE_RECOVERED severity = %v, want INFO", a.Severity)
	}

	// DDE UP -> DEGRADED => WARN DDE_DEGRADED, not SMS-dubbed.
	got = m.Step(Snapshot{DDE: deg, Quik: up, Link: up})
	a, ok = alertByCode(got, CodeDDEDegraded)
	if !ok {
		t.Fatalf("expected %s, got %v", CodeDDEDegraded, got)
	}
	if a.Severity != quikv1.AlertSeverity_ALERT_SEVERITY_WARN {
		t.Errorf("DDE_DEGRADED severity = %v, want WARN", a.Severity)
	}
	if a.SMSDub {
		t.Errorf("DEGRADED must not be SMS-dubbed")
	}

	// No change => no alert.
	if got := m.Step(Snapshot{DDE: deg, Quik: up, Link: up}); len(got) != 0 {
		t.Fatalf("no-change step should emit nothing, got %v", got)
	}
}

// TestMonitorLinkAndQuik checks link/quik down + recovery codes/severities.
func TestMonitorLinkAndQuik(t *testing.T) {
	m := NewMonitor()
	m.Step(Snapshot{DDE: up, Quik: up, Link: up}) // prime

	got := m.Step(Snapshot{DDE: up, Quik: down, Link: down})
	if a, ok := alertByCode(got, CodeQuikDown); !ok || a.Severity != quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL {
		t.Errorf("expected CRITICAL %s, got %v", CodeQuikDown, got)
	}
	if a, ok := alertByCode(got, CodeLinkDown); !ok || a.Severity != quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL {
		t.Errorf("expected CRITICAL %s, got %v", CodeLinkDown, got)
	}

	got = m.Step(Snapshot{DDE: up, Quik: up, Link: up})
	if _, ok := alertByCode(got, CodeQuikRecovered); !ok {
		t.Errorf("expected %s, got %v", CodeQuikRecovered, got)
	}
	if _, ok := alertByCode(got, CodeLinkRecovered); !ok {
		t.Errorf("expected %s, got %v", CodeLinkRecovered, got)
	}
}

// TestColdStartDownSurfaces verifies a channel that starts DOWN is reported on the
// first step (baseline is UP, so DOWN is a transition).
func TestColdStartDownSurfaces(t *testing.T) {
	m := NewMonitor()
	got := m.Step(Snapshot{DDE: down, Quik: up, Link: up})
	if _, ok := alertByCode(got, CodeDDEDown); !ok {
		t.Fatalf("cold start DDE DOWN should surface, got %v", got)
	}
}

// TestDiagnosticsBuild checks the proto Diagnostics carries inputs through.
func TestDiagnosticsBuild(t *testing.T) {
	in := Inputs{
		DDEServerAlive:       true,
		HaveTicked:           true,
		LastTickAgeMs:        1234,
		QuikAlive:            true,
		LinkConnected:        true,
		ReconnectsSinceStart: 7,
		UptimeSec:            99,
	}
	s := Evaluate(in, Defaults())
	d := Diagnostics(s, in)
	if d.GetLastTickAgeMs() != 1234 || d.GetReconnectsSinceStart() != 7 || d.GetUptimeSec() != 99 {
		t.Fatalf("diagnostics did not carry inputs: %+v", d)
	}
	if d.GetDde() != up || d.GetQuik() != up || d.GetLink() != up {
		t.Fatalf("diagnostics states wrong: %+v", d)
	}
}
