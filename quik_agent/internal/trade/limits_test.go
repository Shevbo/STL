package trade

import (
	"testing"
	"time"
)

func baseLimits() Limits {
	return Limits{
		TradingEnabled:       true,
		MaxContractsPerOrder: 2,
		MaxWorkingContracts:  2,
		PriceCollarFrac:      0.002,
		InstrumentWhitelist:  []string{"RIU6"},
		DailyOrderCap:        50,
	}
}

func TestApplyPushedLimits(t *testing.T) {
	g := NewGuard(baseLimits()) // whitelist [RIU6], caps 2/2, collar 0.002, daily 50

	// Before: GZU6 is rejected (not in the agent's whitelist).
	if g.Limits().whitelisted("GZU6") {
		t.Fatal("GZU6 unexpectedly whitelisted before push")
	}

	// STL pushes a wider whitelist + same caps. The whitelist is REPLACED.
	g.ApplyPushed([]string{"RIU6", "GZU6", "SiU6"}, 2, 2, 0.002, 50)
	if !g.Limits().whitelisted("GZU6") {
		t.Fatal("GZU6 should be whitelisted after push")
	}
	if g.LastPushMs() == 0 {
		t.Fatal("LastPushMs should be set after a push")
	}

	// Caps are ceiling-only: a LOOSER push is ignored (config stays the hard cap).
	g.ApplyPushed(nil, 99, 99, 0.5, 9999)
	lim := g.Limits()
	if lim.MaxContractsPerOrder != 2 || lim.MaxWorkingContracts != 2 ||
		lim.PriceCollarFrac != 0.002 || lim.DailyOrderCap != 50 {
		t.Fatalf("looser caps must be ignored, got %+v", lim)
	}

	// A TIGHTER push is adopted.
	g.ApplyPushed(nil, 1, 1, 0.001, 10)
	lim = g.Limits()
	if lim.MaxContractsPerOrder != 1 || lim.MaxWorkingContracts != 1 ||
		lim.PriceCollarFrac != 0.001 || lim.DailyOrderCap != 10 {
		t.Fatalf("tighter caps must be adopted, got %+v", lim)
	}

	// An empty whitelist push is IGNORED (fail-safe), the master flag is untouched.
	before := g.Limits().InstrumentWhitelist
	g.ApplyPushed(nil, 0, 0, 0, 0)
	if len(g.Limits().InstrumentWhitelist) != len(before) {
		t.Fatal("empty whitelist push must not change the whitelist")
	}
	if !g.Limits().TradingEnabled {
		t.Fatal("ApplyPushed must never change the master flag")
	}
}

func TestCheckPlace(t *testing.T) {
	cases := []struct {
		name    string
		mutate  func(l *Limits)
		check   PlaceCheck
		wantOK  bool
		wantRsn RejectReason
	}{
		{
			name:   "ok",
			check:  PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 1, CurrentWorking: 0},
			wantOK: true,
		},
		{
			name:    "master flag off rejects everything",
			mutate:  func(l *Limits) { l.TradingEnabled = false },
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 1},
			wantOK:  false,
			wantRsn: ReasonTradingDisabled,
		},
		{
			name:    "not whitelisted",
			check:   PlaceCheck{Code: "SiU6", Price: 100000, Quantity: 1},
			wantOK:  false,
			wantRsn: ReasonNotWhitelisted,
		},
		{
			name:    "whitelist case-insensitive ok",
			check:   PlaceCheck{Code: "riu6", Price: 100000, Quantity: 1},
			wantOK:  true,
		},
		{
			name:    "zero quantity",
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 0},
			wantOK:  false,
			wantRsn: ReasonQtyNonPositive,
		},
		{
			name:    "quantity over per-order cap",
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 3},
			wantOK:  false,
			wantRsn: ReasonQtyPerOrder,
		},
		{
			name:    "would exceed working cap",
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 2, CurrentWorking: 1},
			wantOK:  false,
			wantRsn: ReasonWorkingCap,
		},
		{
			name:    "working cap exact boundary ok",
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 1, CurrentWorking: 1},
			wantOK:  true,
		},
		{
			name:    "non-positive price",
			check:   PlaceCheck{Code: "RIU6", Price: 0, Quantity: 1},
			wantOK:  false,
			wantRsn: ReasonPriceNonPositive,
		},
		{
			name:    "daily cap zero rejects",
			mutate:  func(l *Limits) { l.DailyOrderCap = 0 },
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 1},
			wantOK:  false,
			wantRsn: ReasonDailyCap,
		},
		{
			name:    "empty whitelist fails closed",
			mutate:  func(l *Limits) { l.InstrumentWhitelist = nil },
			check:   PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 1},
			wantOK:  false,
			wantRsn: ReasonNotWhitelisted,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			l := baseLimits()
			if tc.mutate != nil {
				tc.mutate(&l)
			}
			g := NewGuard(l)
			ok, rsn := g.CheckPlace(tc.check)
			if ok != tc.wantOK {
				t.Fatalf("ok = %v, want %v (rsn=%q)", ok, tc.wantOK, rsn)
			}
			if !ok && rsn != tc.wantRsn {
				t.Fatalf("reason = %q, want %q", rsn, tc.wantRsn)
			}
		})
	}
}

func TestDailyCapAndRollover(t *testing.T) {
	l := baseLimits()
	l.DailyOrderCap = 2
	g := NewGuard(l)

	now := time.Date(2026, 6, 29, 10, 0, 0, 0, time.UTC)
	g.nowFn = func() time.Time { return now }

	for i := 0; i < 2; i++ {
		if ok, rsn := g.CommitPlace(); !ok {
			t.Fatalf("commit %d failed: %q", i, rsn)
		}
	}
	if ok, rsn := g.CommitPlace(); ok || rsn != ReasonDailyCap {
		t.Fatalf("3rd commit ok=%v rsn=%q, want daily cap reject", ok, rsn)
	}
	// CheckPlace must also report the cap now.
	if ok, rsn := g.CheckPlace(PlaceCheck{Code: "RIU6", Price: 100000, Quantity: 1}); ok || rsn != ReasonDailyCap {
		t.Fatalf("CheckPlace after cap ok=%v rsn=%q, want daily cap", ok, rsn)
	}
	// Next calendar day resets the counter.
	now = now.Add(24 * time.Hour)
	if ok, _ := g.CommitPlace(); !ok {
		t.Fatalf("commit after day rollover should succeed")
	}
	if got := g.PlacedToday(); got != 1 {
		t.Fatalf("placedToday after rollover = %d, want 1", got)
	}
}

func TestCheckCollar(t *testing.T) {
	cases := []struct {
		name      string
		buy       bool
		reference float64
		price     float64
		frac      float64
		wantOK    bool
	}{
		{"buy within", true, 100000, 100100, 0.002, true},     // +0.1% < 0.2%
		{"buy at bound", true, 100000, 100200, 0.002, true},   // exactly +0.2%
		{"buy beyond", true, 100000, 100201, 0.002, false},    // +0.201% > 0.2%
		{"sell within", false, 100000, 99900, 0.002, true},    // -0.1%
		{"sell beyond", false, 100000, 99700, 0.002, false},   // -0.3%
		{"frac zero disables", true, 100000, 200000, 0, true}, // no collar
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ok, _ := CheckCollar(tc.buy, tc.reference, tc.price, tc.frac)
			if ok != tc.wantOK {
				t.Fatalf("ok = %v, want %v", ok, tc.wantOK)
			}
		})
	}
}

func TestWorstPrice(t *testing.T) {
	if got := WorstPrice(true, 100000, 0.002); got != 100200 {
		t.Fatalf("buy worst = %v, want 100200", got)
	}
	if got := WorstPrice(false, 100000, 0.002); got != 99800 {
		t.Fatalf("sell worst = %v, want 99800", got)
	}
	if got := WorstPrice(true, 100000, 0); got != 100000 {
		t.Fatalf("frac 0 worst = %v, want reference", got)
	}
}
