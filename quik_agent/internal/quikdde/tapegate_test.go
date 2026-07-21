package quikdde

import (
	"reflect"
	"testing"
)

// Row layout: [price, qty, side, receipt_ms, exch_ms?]. The gate judges ONLY the
// 5th element (exchange time) — receipt stamps are always "now" during a replay,
// which is exactly why they cannot be trusted (2026-07-21 incident).

const now = int64(1_784_700_000_000)

func TestFilterStaleTapeRows_DropsReplayedKeepsLive(t *testing.T) {
	live := []float64{84000, 1, 2, float64(now), float64(now - 2_000)}
	replayed := []float64{83260, 1, 1, float64(now), float64(now - 8*3600*1000)} // 8h old
	got := FilterStaleTapeRows([][]float64{replayed, live, replayed}, now, TapeGateMaxAgeMs)
	want := [][]float64{live}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("want only the live row, got %v", got)
	}
}

func TestFilterStaleTapeRows_LegacyRowsWithoutExchTsPass(t *testing.T) {
	// Older Lua emits 4-element rows: age unknowable -> pass (layer 1's job).
	legacy := []float64{84000, 1, 2, float64(now)}
	zero := []float64{84010, 1, 1, float64(now), 0} // QUIK gave no datetime
	got := FilterStaleTapeRows([][]float64{legacy, zero}, now, TapeGateMaxAgeMs)
	if len(got) != 2 {
		t.Fatalf("legacy/zero-ts rows must pass, got %v", got)
	}
}

func TestFilterStaleTapeRows_BoundaryAndDisabled(t *testing.T) {
	edge := []float64{84000, 1, 2, float64(now), float64(now - TapeGateMaxAgeMs)} // exactly at cap
	if got := FilterStaleTapeRows([][]float64{edge}, now, TapeGateMaxAgeMs); len(got) != 1 {
		t.Fatalf("age == maxAge must pass (only strictly older drops), got %v", got)
	}
	stale := []float64{84000, 1, 2, float64(now), float64(now - TapeGateMaxAgeMs - 1)}
	if got := FilterStaleTapeRows([][]float64{stale}, now, TapeGateMaxAgeMs); len(got) != 0 {
		t.Fatalf("age > maxAge must drop, got %v", got)
	}
	if got := FilterStaleTapeRows([][]float64{stale}, now, 0); len(got) != 1 {
		t.Fatalf("maxAge<=0 disables the gate, got %v", got)
	}
}

func TestFilterStaleTapeRows_NoDropReturnsInputSlice(t *testing.T) {
	rows := [][]float64{{84000, 1, 2, float64(now), float64(now - 1000)}}
	if got := FilterStaleTapeRows(rows, now, TapeGateMaxAgeMs); &got[0] != &rows[0] {
		t.Fatalf("hot path must return the input slice unchanged (zero alloc)")
	}
}
