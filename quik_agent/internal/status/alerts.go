package status

// ReconAlerter debounces recon-state transitions into alert specs. It is pure
// state-machine logic: the caller (main.go's recon evaluation loop, Task 9)
// feeds it every observation with its own clock and forwards the returned
// specs to the link's EmitAlert — nothing here touches the network.

import (
	"fmt"

	quikv1 "shectory/quik_agent/internal/pb"
)

// AlertSpec is one alert the caller should emit (matches Emitter.EmitAlert's
// arguments).
type AlertSpec struct {
	Severity quikv1.AlertSeverity
	Code     string
	Message  string
}

// reconAlertDebounceMs: a MISMATCH must be sustained this long before it
// alerts. Recon compares snapshots that refresh on independent cadences
// (account poll vs. runner status), so a sub-10s flap is expected noise, not
// an incident.
const reconAlertDebounceMs = 10_000

// ReconAlerter tracks one recon stream's alert state. Zero value is ready.
// Exported (Task 8 originally kept it package-private) so main.go's recon
// alert loop (Task 9) can hold one across ticks: var a status.ReconAlerter.
type ReconAlerter struct {
	armed     bool  // a MISMATCH run is in progress (explicit flag: nowMs may legitimately be 0)
	armedAtMs int64 // first ms a MISMATCH was observed in the current run
	emitted   bool  // a RECON_MISMATCH went out and no RECON_RECOVERED yet
}

// Step consumes one recon observation ("OK" | "MISMATCH" | "STALE") and
// returns the alerts to emit now (usually none):
//   - the first MISMATCH observation only ARMS the debounce; an observation
//     still MISMATCH >=10s later emits RECON_MISMATCH once — CRITICAL when a
//     real-money robot is involved, WARN otherwise;
//   - OK after an emitted alert emits RECON_RECOVERED (INFO) once;
//   - OK inside the debounce (a flap) silently disarms;
//   - STALE disarms the debounce (data too old to judge) but is never a
//     recovery — only a real OK clears an emitted alert.
func (a *ReconAlerter) Step(state string, realInvolved bool, nowMs int64) []AlertSpec {
	switch state {
	case "MISMATCH":
		if !a.armed {
			a.armed = true
			a.armedAtMs = nowMs
			return nil
		}
		if !a.emitted && nowMs-a.armedAtMs >= reconAlertDebounceMs {
			a.emitted = true
			sev := quikv1.AlertSeverity_ALERT_SEVERITY_WARN
			if realInvolved {
				sev = quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL
			}
			return []AlertSpec{{
				Severity: sev,
				Code:     "RECON_MISMATCH",
				Message: fmt.Sprintf(
					"Рекон: расхождение книг роботов с фактом QUIK держится >=%dс — открой статус-страницу агента и проверь align-план",
					reconAlertDebounceMs/1000),
			}}
		}
		return nil
	case "OK":
		a.armed = false
		if a.emitted {
			a.emitted = false
			return []AlertSpec{{
				Severity: quikv1.AlertSeverity_ALERT_SEVERITY_INFO,
				Code:     "RECON_RECOVERED",
				Message:  "Рекон: расхождение устранено, книги роботов снова сходятся с QUIK",
			}}
		}
		return nil
	default: // "STALE" (or unknown): disarm, never recover.
		a.armed = false
		return nil
	}
}
