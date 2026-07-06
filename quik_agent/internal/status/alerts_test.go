package status

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

func TestReconAlerter_SustainedMismatchEmitsAfterDebounce(t *testing.T) {
	var a ReconAlerter
	if got := a.Step("MISMATCH", false, 0); got != nil {
		t.Fatalf("first MISMATCH observation only arms, got %+v", got)
	}
	if got := a.Step("MISMATCH", false, 9_999); got != nil {
		t.Fatalf("still inside the 10s debounce, got %+v", got)
	}
	got := a.Step("MISMATCH", false, 10_000)
	if len(got) != 1 || got[0].Code != "RECON_MISMATCH" {
		t.Fatalf("want RECON_MISMATCH at >=10s, got %+v", got)
	}
	if got[0].Severity != quikv1.AlertSeverity_ALERT_SEVERITY_WARN {
		t.Errorf("paper-only mismatch is WARN, got %v", got[0].Severity)
	}
	if got[0].Message == "" {
		t.Errorf("alert must carry a human message")
	}
	// while the mismatch persists, no repeat emission
	if got := a.Step("MISMATCH", false, 60_000); got != nil {
		t.Errorf("no repeat while still MISMATCH, got %+v", got)
	}
}

func TestReconAlerter_RealInvolvedIsCritical(t *testing.T) {
	var a ReconAlerter
	a.Step("MISMATCH", true, 0)
	got := a.Step("MISMATCH", true, 10_000)
	if len(got) != 1 || got[0].Severity != quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL {
		t.Fatalf("real-money involvement must be CRITICAL, got %+v", got)
	}
}

func TestReconAlerter_FlapWithin10sNeverAlerts(t *testing.T) {
	var a ReconAlerter
	a.Step("MISMATCH", true, 0)
	if got := a.Step("OK", true, 5_000); got != nil {
		t.Fatalf("flap back to OK before emission: no alert, got %+v", got)
	}
	// re-arm restarts the debounce from the NEW first observation
	a.Step("MISMATCH", true, 6_000)
	if got := a.Step("MISMATCH", true, 15_999); got != nil {
		t.Fatalf("9.999s since re-arm is inside the debounce, got %+v", got)
	}
	if got := a.Step("MISMATCH", true, 16_000); len(got) != 1 {
		t.Fatalf("10s since re-arm must emit, got %+v", got)
	}
}

func TestReconAlerter_RecoveredAfterEmittedAlert(t *testing.T) {
	var a ReconAlerter
	a.Step("MISMATCH", false, 0)
	a.Step("MISMATCH", false, 10_000)
	got := a.Step("OK", false, 20_000)
	if len(got) != 1 || got[0].Code != "RECON_RECOVERED" {
		t.Fatalf("want RECON_RECOVERED after an emitted alert, got %+v", got)
	}
	if got[0].Severity != quikv1.AlertSeverity_ALERT_SEVERITY_INFO {
		t.Errorf("recovery is INFO, got %v", got[0].Severity)
	}
	// recovery emits once; steady OK stays silent
	if got := a.Step("OK", false, 30_000); got != nil {
		t.Errorf("steady OK after recovery must be silent, got %+v", got)
	}
}

func TestReconAlerter_OKWithoutEmissionStaysSilent(t *testing.T) {
	var a ReconAlerter
	if got := a.Step("OK", false, 0); got != nil {
		t.Fatalf("OK from cold start must be silent, got %+v", got)
	}
}

func TestReconAlerter_StaleDisarmsButNeverRecovers(t *testing.T) {
	var a ReconAlerter
	// STALE resets the debounce arm (data too old to judge)...
	a.Step("MISMATCH", false, 0)
	a.Step("STALE", false, 5_000)
	a.Step("MISMATCH", false, 6_000)
	if got := a.Step("MISMATCH", false, 15_000); got != nil {
		t.Fatalf("arm restarted at 6000; 9s later is inside the debounce, got %+v", got)
	}
	// ...and after an emitted alert, STALE is NOT a recovery — only a real OK is.
	var b ReconAlerter
	b.Step("MISMATCH", false, 0)
	b.Step("MISMATCH", false, 10_000)
	if got := b.Step("STALE", false, 20_000); got != nil {
		t.Fatalf("STALE must not emit RECON_RECOVERED, got %+v", got)
	}
	if got := b.Step("OK", false, 30_000); len(got) != 1 || got[0].Code != "RECON_RECOVERED" {
		t.Fatalf("the real OK after STALE still recovers, got %+v", got)
	}
}
