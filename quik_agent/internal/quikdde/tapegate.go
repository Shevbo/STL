package quikdde

// Tape replay gate — the Go backstop of the two-layer defense born from the
// 2026-07-21 incident: a restarted QUIK terminal re-fires the WHOLE day's trades
// through OnAllTrade, and because tape rows are receipt-stamped, runner bars got
// hours-old prices under fresh timestamps while quotes stayed live — averaging
// robots piled real longs 1 lot/min into a phantom dip.
//
// Layer 1 (source): shectory_trade.lua drops stale trades in OnAllTrade using the
// per-trade EXCHANGE datetime (CONFIG.TAPE_GATE_SEC) and appends that exchange
// epoch-ms as the row's 5th element.
// Layer 2 (here): the agent re-checks that 5th element, protecting when the
// operator has not yet restarted the Lua (it runs from memory — a shipped file is
// not the running code) or when a sidecar config disables the Lua gate by mistake.
//
// A row WITHOUT a 5th element (older Lua build) or with 0 (QUIK gave no datetime)
// passes: age is unknowable, and killing the live feed is worse than letting a
// replay through one layer — that failure mode is exactly what layer 1 covers.

// TapeGateMaxAgeMs is the default backstop age: generous enough for batching
// (300ms), file-queue latency and modest VDS clock drift; a replay lags minutes
// to hours. Mirrors the Lua default TAPE_GATE_SEC=300.
const TapeGateMaxAgeMs = 300_000

// FilterStaleTapeRows returns rows whose exchange timestamp (5th element,
// epoch-ms) is within maxAgeMs of nowMs. Rows without a positive 5th element
// pass through. maxAgeMs <= 0 disables the gate. The input slice is never
// mutated; when nothing is dropped the input is returned as-is (zero alloc on
// the hot path — every live batch).
func FilterStaleTapeRows(rows [][]float64, nowMs, maxAgeMs int64) [][]float64 {
	if maxAgeMs <= 0 {
		return rows
	}
	drop := func(r []float64) bool {
		if len(r) < 5 || r[4] <= 0 {
			return false
		}
		return nowMs-int64(r[4]) > maxAgeMs
	}
	n := 0
	for _, r := range rows {
		if drop(r) {
			n++
		}
	}
	if n == 0 {
		return rows
	}
	out := make([][]float64, 0, len(rows)-n)
	for _, r := range rows {
		if !drop(r) {
			out = append(out, r)
		}
	}
	return out
}
