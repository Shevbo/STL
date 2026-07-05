package accounts

import (
	"fmt"
	"testing"
)

func TestPositionFromRow(t *testing.T) {
	p, ok := PositionFromRow([]any{"RIU6", 2.0, 89100.0})
	if !ok {
		t.Fatal("valid row rejected")
	}
	if p != (Position{Sec: "RIU6", Net: 2, Avg: 89100.0}) {
		t.Fatalf("got %+v", p)
	}
	if _, ok := PositionFromRow([]any{"RIU6", 2.0}); ok {
		t.Fatal("short row must be rejected")
	}
	if _, ok := PositionFromRow([]any{"RIU6", nil, 89100.0}); ok {
		t.Fatal("non-numeric net must be rejected")
	}
}

func TestOrderFromRow(t *testing.T) {
	o, ok := OrderFromRow([]any{"123", "RIU6", 1.0, 89000.0, 1.0, 1.0})
	if !ok {
		t.Fatal("valid row rejected")
	}
	want := Order{Num: "123", Sec: "RIU6", Active: true, Price: 89000, Balance: 1, Qty: 1}
	if o != want {
		t.Fatalf("got %+v, want %+v", o, want)
	}
	inactive, ok := OrderFromRow([]any{"124", "RIU6", 0.0, 89000.0, 0.0, 0.0})
	if !ok || inactive.Active {
		t.Fatalf("active=0 must decode to Active=false, got %+v", inactive)
	}
	if _, ok := OrderFromRow([]any{"123", "RIU6", 1.0, 89000.0, 1.0}); ok {
		t.Fatal("short row must be rejected")
	}
	if _, ok := OrderFromRow([]any{"123", "RIU6", 1.0, "bad", 1.0, 1.0}); ok {
		t.Fatal("non-numeric price must be rejected")
	}
}

func TestTradeFromRow(t *testing.T) {
	tr, ok := TradeFromRow([]any{"t1", "123", "RIU6", 89050.0, 1.0, 1751700000000.0})
	if !ok {
		t.Fatal("valid row rejected")
	}
	want := Trade{Num: "t1", OrderNum: "123", Sec: "RIU6", Price: 89050, Qty: 1, TsMs: 1751700000000}
	if tr != want {
		t.Fatalf("got %+v, want %+v", tr, want)
	}
	if _, ok := TradeFromRow([]any{"t1", "123", "RIU6", 89050.0, 1.0}); ok {
		t.Fatal("short row must be rejected")
	}
}

func TestStorePositionsOrdersSnapshot(t *testing.T) {
	s := New(func() int64 { return 0 })
	s.SetPositions([]Position{{Sec: "RIU6", Net: 2, Avg: 89100}})
	s.SetOrders([]Order{{Num: "1", Sec: "RIU6", Active: true, Price: 89000, Balance: 1, Qty: 1}})
	snap := s.Snapshot()
	if len(snap.Positions) != 1 || snap.Positions[0].Sec != "RIU6" {
		t.Fatalf("positions = %+v", snap.Positions)
	}
	if len(snap.Orders) != 1 || snap.Orders[0].Num != "1" {
		t.Fatalf("orders = %+v", snap.Orders)
	}
}

func TestStoreAges(t *testing.T) {
	now := int64(1_000_000)
	s := New(func() int64 { return now })
	s.SetPositions([]Position{{Sec: "RIU6", Net: 1, Avg: 100}})
	now += 250
	s.SetOrders([]Order{{Num: "1", Sec: "RIU6", Active: true, Price: 100, Balance: 1, Qty: 1}})
	now += 500
	snap := s.Snapshot()
	if snap.PosAgeMs != 750 {
		t.Fatalf("PosAgeMs = %d, want 750", snap.PosAgeMs)
	}
	if snap.OrdAgeMs != 500 {
		t.Fatalf("OrdAgeMs = %d, want 500", snap.OrdAgeMs)
	}
}

func TestStoreAddTradesDedupeOnResend(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	batch := []Trade{
		{Num: "t1", OrderNum: "1", Sec: "RIU6", Price: 100, Qty: 1, TsMs: 1},
		{Num: "t2", OrderNum: "1", Sec: "RIU6", Price: 101, Qty: 1, TsMs: 2},
	}
	s.AddTrades(batch)
	// QUIK session rollover: Lua cursor resets and re-sends the FULL trades table.
	s.AddTrades(batch)
	snap := s.Snapshot()
	if len(snap.Trades) != 2 {
		t.Fatalf("got %d trades after resend, want 2 (deduped by Num)", len(snap.Trades))
	}
}

func TestStoreAddTradesCapsAt500(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	var batch []Trade
	for i := 0; i < 600; i++ {
		batch = append(batch, Trade{Num: fmt.Sprintf("t%d", i), OrderNum: "1", Sec: "RIU6", Price: 100, Qty: 1, TsMs: int64(i)})
	}
	s.AddTrades(batch)
	snap := s.Snapshot()
	if len(snap.Trades) != 500 {
		t.Fatalf("got %d trades, want 500 (ring cap)", len(snap.Trades))
	}
	if snap.Trades[0].Num != "t100" {
		t.Fatalf("ring should drop oldest 100, first = %s", snap.Trades[0].Num)
	}
	if snap.Trades[499].Num != "t599" {
		t.Fatalf("ring should keep newest, last = %s", snap.Trades[499].Num)
	}
}

func TestStoreRTTAndDrift(t *testing.T) {
	now := int64(1000_000)
	s := New(func() int64 { return now })
	s.SetPong(now-150, 0, "03:00:00") // t0 150ms ago
	snap := s.Snapshot()
	if snap.RTTMs != 150 {
		t.Fatalf("rtt %d", snap.RTTMs)
	}
}

func TestStoreDriftMidnightWrap(t *testing.T) {
	// now (UTC ms) chosen so that local MSK time-of-day = 00:00:30 (30s past MSK
	// midnight): (now + 3h) mod 24h == 30_000ms.
	now := int64(75_630_000)
	s := New(func() int64 { return now })
	// Server reports 23:59:00 MSK (60s before midnight). Physically the true drift
	// is +90s (local is 90s ahead), not the naive ~24h same-day subtraction.
	s.SetPong(now, 0, "23:59:00")
	snap := s.Snapshot()
	if snap.ClockDriftMs != 90_000 {
		t.Fatalf("ClockDriftMs = %d, want 90000 (midnight-wrap candidate)", snap.ClockDriftMs)
	}
}

func TestStoreDriftSameDay(t *testing.T) {
	// now (UTC ms) chosen so local MSK time-of-day = 12:00:05.
	now := int64(12*3600_000 + 5_000 - 3*3600_000)
	s := New(func() int64 { return now })
	s.SetPong(now, 0, "12:00:00")
	snap := s.Snapshot()
	if snap.ClockDriftMs != 5_000 {
		t.Fatalf("ClockDriftMs = %d, want 5000", snap.ClockDriftMs)
	}
}

func TestStorePongAge(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	s.SetPong(now-10, 0, "00:00:00")
	now += 300
	snap := s.Snapshot()
	if snap.PongAgeMs != 300 {
		t.Fatalf("PongAgeMs = %d, want 300", snap.PongAgeMs)
	}
}

func TestStoreTapeLag(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	s.SetTapeLag(900, 950) // exch ts 900, agent recv 950 -> lag 50ms
	snap := s.Snapshot()
	if snap.ExchangeLagMs != 50 {
		t.Fatalf("ExchangeLagMs = %d, want 50", snap.ExchangeLagMs)
	}
	// A stale (older) sample must NOT override the freshest pair.
	s.SetTapeLag(500, 940)
	snap = s.Snapshot()
	if snap.ExchangeLagMs != 50 {
		t.Fatalf("stale tape sample overrode freshest pair: ExchangeLagMs = %d, want 50", snap.ExchangeLagMs)
	}
	// A genuinely fresher pair must update.
	s.SetTapeLag(1000, 1200)
	snap = s.Snapshot()
	if snap.ExchangeLagMs != 200 {
		t.Fatalf("ExchangeLagMs = %d, want 200", snap.ExchangeLagMs)
	}
}
