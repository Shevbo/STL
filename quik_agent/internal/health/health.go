// Package health computes channel health (ChannelState UP/DEGRADED/DOWN) for the
// dde, quik, and link channels from configurable thresholds, builds the
// Diagnostics snapshot, and detects state transitions to raise Alert frames with
// the right AlertSeverity and machine codes (DDE_DOWN / QUIK_DOWN / LINK_DOWN and
// their *_RECOVERED counterparts).
//
// This package is PURE LOGIC: it touches no DDE, no network, no clock except via
// the Inputs it is handed. internal/link feeds it real values (DDE Alive, last
// tick age, reconnect counter, uptime) and sends the Diagnostics / Alert frames it
// returns. That keeps the threshold maths unit-testable without QUIK or gRPC.
//
// Phase 1 is READ-ONLY: nothing here can place, move, or cancel an order. It only
// classifies signals and emits diagnostics/alerts.
package health

import (
	quikv1 "shectory/quik_agent/internal/pb"
)

// Thresholds parameterises the state machine. All durations are milliseconds.
// Zero values are replaced by Defaults() so a partially-filled struct still works.
type Thresholds struct {
	// StaleTickMs: DDE moves UP -> DEGRADED once the freshest tick is older than
	// this and the DDE server is still up.
	StaleTickMs int64
	// DDEDownMs: DDE moves to DOWN once the freshest tick is older than this (or
	// the DDE server reports not alive).
	DDEDownMs int64
}

// Defaults returns the built-in thresholds (mirrors internal/config defaults).
func Defaults() Thresholds {
	return Thresholds{
		StaleTickMs: 30_000,
		DDEDownMs:   60_000,
	}
}

// normalize fills any non-positive field from Defaults so the maths is well-defined.
func (t Thresholds) normalize() Thresholds {
	d := Defaults()
	if t.StaleTickMs <= 0 {
		t.StaleTickMs = d.StaleTickMs
	}
	if t.DDEDownMs <= 0 {
		t.DDEDownMs = d.DDEDownMs
	}
	// Guard ordering: DOWN threshold must be >= DEGRADED threshold.
	if t.DDEDownMs < t.StaleTickMs {
		t.DDEDownMs = t.StaleTickMs
	}
	return t
}

// Inputs is the raw, already-sampled health signal the link hands to Evaluate.
// No method here reads a clock or a socket; the caller samples and passes values.
type Inputs struct {
	// DDEServerAlive is quikdde.Alive(): the local DDE server thread is up.
	DDEServerAlive bool
	// HaveTicked is true once at least one DDE mutation has been seen. Before the
	// first tick, a zero LastTickAgeMs must NOT read as "fresh".
	HaveTicked bool
	// LastTickAgeMs is the staleness of the freshest DDE tick (Provider.FreshnessMs).
	LastTickAgeMs int64
	// QuikAlive is the agent's view of whether QUIK itself looks reachable.
	QuikAlive bool
	// LinkConnected is true while the gRPC session is established. Diagnostics are
	// only sent over a live link, so from the agent's own view this is normally
	// true; the local notify fallback covers the link-down case.
	LinkConnected bool
	// ReconnectsSinceStart is the session reconnect counter.
	ReconnectsSinceStart uint32
	// UptimeSec is process uptime in seconds.
	UptimeSec int64
}

// Snapshot is the classified health at one instant.
type Snapshot struct {
	DDE  quikv1.ChannelState
	Quik quikv1.ChannelState
	Link quikv1.ChannelState
}

// classifyDDE maps DDE liveness + tick staleness to a ChannelState.
func classifyDDE(in Inputs, t Thresholds) quikv1.ChannelState {
	if !in.DDEServerAlive {
		return quikv1.ChannelState_CHANNEL_STATE_DOWN
	}
	if !in.HaveTicked {
		// Server up but no data yet: degraded, not a hard outage.
		return quikv1.ChannelState_CHANNEL_STATE_DEGRADED
	}
	switch {
	case in.LastTickAgeMs >= t.DDEDownMs:
		return quikv1.ChannelState_CHANNEL_STATE_DOWN
	case in.LastTickAgeMs >= t.StaleTickMs:
		return quikv1.ChannelState_CHANNEL_STATE_DEGRADED
	default:
		return quikv1.ChannelState_CHANNEL_STATE_UP
	}
}

func classifyQuik(in Inputs) quikv1.ChannelState {
	if in.QuikAlive {
		return quikv1.ChannelState_CHANNEL_STATE_UP
	}
	return quikv1.ChannelState_CHANNEL_STATE_DOWN
}

func classifyLink(in Inputs) quikv1.ChannelState {
	if in.LinkConnected {
		return quikv1.ChannelState_CHANNEL_STATE_UP
	}
	return quikv1.ChannelState_CHANNEL_STATE_DOWN
}

// Evaluate classifies the three channels from inputs and thresholds.
func Evaluate(in Inputs, t Thresholds) Snapshot {
	t = t.normalize()
	return Snapshot{
		DDE:  classifyDDE(in, t),
		Quik: classifyQuik(in),
		Link: classifyLink(in),
	}
}

// Diagnostics builds the proto Diagnostics message from a snapshot + raw inputs.
func Diagnostics(s Snapshot, in Inputs) *quikv1.Diagnostics {
	return &quikv1.Diagnostics{
		Dde:                  s.DDE,
		Quik:                 s.Quik,
		Link:                 s.Link,
		LastTickAgeMs:        in.LastTickAgeMs,
		ReconnectsSinceStart: in.ReconnectsSinceStart,
		UptimeSec:            in.UptimeSec,
	}
}

// AlertSpec describes an Alert the state machine wants raised. SMSDub marks the
// CRITICAL alerts that Phase 2 will also dub to SMS; Phase 1 only sets the flag.
type AlertSpec struct {
	Severity quikv1.AlertSeverity
	Code     string
	Message  string
	SMSDub   bool
}

// Machine codes (machine-readable Alert.code values).
const (
	CodeDDEDown      = "DDE_DOWN"
	CodeDDERecovered = "DDE_RECOVERED"
	CodeDDEDegraded  = "DDE_DEGRADED"

	CodeQuikDown      = "QUIK_DOWN"
	CodeQuikRecovered = "QUIK_RECOVERED"

	CodeLinkDown      = "LINK_DOWN"
	CodeLinkRecovered = "LINK_RECOVERED"
)

// Monitor tracks the previous Snapshot and emits AlertSpecs on transitions only.
// It is NOT safe for concurrent use; the link calls it from one goroutine.
type Monitor struct {
	prev   Snapshot
	primed bool
}

// NewMonitor returns a Monitor with no prior state. The first Step primes the
// baseline and (for any channel that starts DOWN/DEGRADED) raises the entry alert.
func NewMonitor() *Monitor { return &Monitor{} }

// severityFor maps a channel's destination state to an alert severity.
//   - DOWN     -> CRITICAL (SMS-dubbed in Phase 2)
//   - DEGRADED -> WARN
//   - UP (recovery) -> INFO
func severityFor(state quikv1.ChannelState) quikv1.AlertSeverity {
	switch state {
	case quikv1.ChannelState_CHANNEL_STATE_DOWN:
		return quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL
	case quikv1.ChannelState_CHANNEL_STATE_DEGRADED:
		return quikv1.AlertSeverity_ALERT_SEVERITY_WARN
	default:
		return quikv1.AlertSeverity_ALERT_SEVERITY_INFO
	}
}

// channelTransition emits at most one AlertSpec for a single channel changing
// from old to new. down/degraded/recovered codes are channel-specific.
func channelTransition(old, new quikv1.ChannelState, downCode, degradedCode, recoveredCode, label string) (AlertSpec, bool) {
	if old == new {
		return AlertSpec{}, false
	}
	sev := severityFor(new)
	switch new {
	case quikv1.ChannelState_CHANNEL_STATE_DOWN:
		return AlertSpec{
			Severity: sev,
			Code:     downCode,
			Message:  label + " channel DOWN",
			SMSDub:   true,
		}, true
	case quikv1.ChannelState_CHANNEL_STATE_DEGRADED:
		// Degraded recovering from DOWN is still a (partial) recovery; degraded
		// from UP is a warning. Either way report the degraded code at WARN.
		return AlertSpec{
			Severity: sev,
			Code:     degradedCode,
			Message:  label + " channel DEGRADED",
		}, true
	default: // UP
		return AlertSpec{
			Severity: sev,
			Code:     recoveredCode,
			Message:  label + " channel recovered (UP)",
		}, true
	}
}

// Step compares the new snapshot against the previous one and returns the alerts
// to raise. On the very first call it primes the baseline and reports any channel
// that does not start UP (so a cold start in a DOWN state is surfaced).
func (m *Monitor) Step(s Snapshot) []AlertSpec {
	var out []AlertSpec

	if !m.primed {
		base := Snapshot{
			DDE:  quikv1.ChannelState_CHANNEL_STATE_UP,
			Quik: quikv1.ChannelState_CHANNEL_STATE_UP,
			Link: quikv1.ChannelState_CHANNEL_STATE_UP,
		}
		m.prev = base
		m.primed = true
		// Fall through so a non-UP starting channel raises its entry alert.
	}

	if a, ok := channelTransition(m.prev.DDE, s.DDE, CodeDDEDown, CodeDDEDegraded, CodeDDERecovered, "DDE"); ok {
		out = append(out, a)
	}
	if a, ok := channelTransition(m.prev.Quik, s.Quik, CodeQuikDown, CodeQuikDown, CodeQuikRecovered, "QUIK"); ok {
		// QUIK has no distinct degraded code in the contract; reuse QUIK_DOWN code
		// only on real DOWN. For a DEGRADED quik state (not produced today) keep
		// the down code but WARN severity.
		out = append(out, a)
	}
	if a, ok := channelTransition(m.prev.Link, s.Link, CodeLinkDown, CodeLinkDown, CodeLinkRecovered, "Link"); ok {
		out = append(out, a)
	}

	m.prev = s
	return out
}

// Prev returns the last snapshot the Monitor saw (UP baseline before priming).
func (m *Monitor) Prev() Snapshot { return m.prev }
