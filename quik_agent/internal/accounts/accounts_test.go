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

// TestOrderTradeFromRowTag: the trailing 7th row element (brokerref, stamped by the
// Lua from the order COMMENT) decodes into Order.Tag/Trade.Tag. It is OPTIONAL — a row
// from an old Lua build with only 6 elements still decodes fine, just with Tag == "".
func TestOrderTradeFromRowTag(t *testing.T) {
	o, ok := OrderFromRow([]any{"555", "RIU6", 1.0, 89000.0, 1.0, 1.0, "agent-fvg-RIU6-v2"})
	if !ok || o.Tag != "agent-fvg-RIU6-v2" {
		t.Fatalf("order tag: %+v ok=%v", o, ok)
	}
	// Old Lua (no 7th element) => empty tag, still decodes.
	o2, ok2 := OrderFromRow([]any{"555", "RIU6", 1.0, 89000.0, 1.0, 1.0})
	if !ok2 || o2.Tag != "" {
		t.Fatalf("legacy order: %+v ok=%v", o2, ok2)
	}
	tr, ok3 := TradeFromRow([]any{"t1", "555", "RIU6", 89050.0, 1.0, 1.75e12, "recon"})
	if !ok3 || tr.Tag != "recon" {
		t.Fatalf("trade tag: %+v ok=%v", tr, ok3)
	}
}

// TestOrderTradeFromRowSideTs: cc3+ trailing elements 8-9 (side "B"/"S", ts_ms)
// decode into Side/TsMs (orders) and Side/ExchTsMs (trades). A cc2 row (7
// elements) still decodes with the new fields zero-valued.
func TestOrderTradeFromRowSideTs(t *testing.T) {
	o, ok := OrderFromRow([]any{"555", "RIU6", 1.0, 89000.0, 1.0, 1.0, "tag", "S", 1751700000000.0})
	if !ok || o.Side != "S" || o.TsMs != 1751700000000 {
		t.Fatalf("order side/ts: %+v ok=%v", o, ok)
	}
	tr, ok2 := TradeFromRow([]any{"t1", "555", "RIU6", 89050.0, 1.0, 1.75e12, "recon", "B", 1751700000123.0})
	if !ok2 || tr.Side != "B" || tr.ExchTsMs != 1751700000123 {
		t.Fatalf("trade side/ts: %+v ok=%v", tr, ok2)
	}
	o2, ok3 := OrderFromRow([]any{"555", "RIU6", 1.0, 89000.0, 1.0, 1.0, "tag"})
	if !ok3 || o2.Side != "" || o2.TsMs != 0 {
		t.Fatalf("cc2 order row: %+v ok=%v", o2, ok3)
	}
}

func TestStoreTransRepliesRing(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	for i := 0; i < transRing+5; i++ {
		s.AddTransReply(int64(i), 0, "", fmt.Sprintf("m%d", i))
	}
	snap := s.Snapshot()
	if len(snap.TransReplies) != transRing {
		t.Fatalf("ring len = %d, want %d", len(snap.TransReplies), transRing)
	}
	if snap.TransReplies[0].TransID != 5 || snap.TransReplies[transRing-1].TransID != int64(transRing+4) {
		t.Fatalf("ring window = [%d..%d]", snap.TransReplies[0].TransID, snap.TransReplies[transRing-1].TransID)
	}
	if snap.TransReplies[0].TsMs != 1000 {
		t.Fatalf("TsMs = %d, want store clock 1000", snap.TransReplies[0].TsMs)
	}
}

func TestStoreQuikFolder(t *testing.T) {
	s := New(func() int64 { return 0 })
	if s.Snapshot().QuikFolder != "" {
		t.Fatal("fresh store must have empty folder")
	}
	s.SetQuikFolder(`C:\QUIK`)
	s.SetQuikFolder("") // an old-Lua empty pong must NOT erase a known folder
	if got := s.Snapshot().QuikFolder; got != `C:\QUIK` {
		t.Fatalf("folder = %q", got)
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

func TestStoreAddTradesDedupeSurvivesRingEviction(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	// 600 unique trades: t0..t99 get evicted by the 500-ring, t100..t599 remain.
	var all []Trade
	for i := 0; i < 600; i++ {
		all = append(all, Trade{Num: fmt.Sprintf("t%d", i), OrderNum: "1", Sec: "RIU6", Price: 100, Qty: 1, TsMs: int64(i)})
	}
	s.AddTrades(all)

	// Session rollover resend: trades #1-#50 (ALREADY EVICTED) plus one new trade.
	resend := append([]Trade(nil), all[1:51]...)
	resend = append(resend, Trade{Num: "t600", OrderNum: "1", Sec: "RIU6", Price: 102, Qty: 1, TsMs: 600})
	s.AddTrades(resend)

	snap := s.Snapshot()
	if len(snap.Trades) != 500 {
		t.Fatalf("got %d trades, want 500", len(snap.Trades))
	}
	// The genuinely new trade must be at the tail.
	if snap.Trades[499].Num != "t600" {
		t.Fatalf("tail = %s, want t600", snap.Trades[499].Num)
	}
	// Evicted trades must NOT re-enter, and the newest pre-resend trades must NOT be
	// displaced: expect exactly t101..t599 then t600 (t100 dropped for t600's slot).
	present := map[string]bool{}
	for _, tr := range snap.Trades {
		present[tr.Num] = true
	}
	for i := 1; i <= 50; i++ {
		if present[fmt.Sprintf("t%d", i)] {
			t.Fatalf("evicted trade t%d re-entered the ring on resend", i)
		}
	}
	for i := 101; i <= 599; i++ {
		if !present[fmt.Sprintf("t%d", i)] {
			t.Fatalf("newer trade t%d was displaced by the resend", i)
		}
	}
	if snap.Trades[0].Num != "t101" {
		t.Fatalf("front = %s, want t101 (pure arrival order)", snap.Trades[0].Num)
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
	s.SetPong(now-150, 0, "03:00:00", 0) // t0 150ms ago
	snap := s.Snapshot()
	if snap.RTTMs != 150 {
		t.Fatalf("rtt %d", snap.RTTMs)
	}
}

// A keepalive re-publish of an UNCHANGED table must refresh freshness (PosAgeMs low)
// WITHOUT advancing the content stamp PosAtMs — otherwise the recon plan ID rotates and
// every operator align confirm 409s on a static account. A genuine content change DOES
// advance PosAtMs.
func TestStorePublishSplitsRecvFromContentStamp(t *testing.T) {
	now := int64(1_000_000)
	s := New(func() int64 { return now })

	rows := []Position{{Sec: "RIU6", Net: 2, Avg: 89100}}
	s.SetPositions(rows)
	first := s.Snapshot()
	if first.PosAtMs != 1_000_000 || first.PosAgeMs != 0 {
		t.Fatalf("first publish: PosAtMs=%d PosAgeMs=%d, want 1000000/0", first.PosAtMs, first.PosAgeMs)
	}

	// Keepalive: same content, 16s later. Freshness updates, content stamp does not.
	now += 16_000
	s.SetPositions([]Position{{Sec: "RIU6", Net: 2, Avg: 89100}})
	ka := s.Snapshot()
	if ka.PosAtMs != 1_000_000 {
		t.Fatalf("keepalive rotated PosAtMs to %d (must stay 1000000 for stable plan ID)", ka.PosAtMs)
	}
	if ka.PosAgeMs != 0 {
		t.Fatalf("keepalive PosAgeMs=%d, want 0 (freshly received)", ka.PosAgeMs)
	}

	// Real content change: PosAtMs advances (plan ID must invalidate).
	now += 5_000
	s.SetPositions([]Position{{Sec: "RIU6", Net: 1, Avg: 89100}})
	chg := s.Snapshot()
	if chg.PosAtMs != 1_021_000 {
		t.Fatalf("content change: PosAtMs=%d, want 1021000", chg.PosAtMs)
	}
}

// A fresh store has never seen a valid pong: RTT must read the "no data" sentinel
// (-1), not 0, so the page renders "нет данных" rather than a fabricated 0 мс.
func TestStoreRTTFreshIsNoData(t *testing.T) {
	s := New(func() int64 { return 1_783_340_000_000 })
	if snap := s.Snapshot(); snap.RTTMs != -1 {
		t.Fatalf("fresh RTTMs = %d, want -1", snap.RTTMs)
	}
}

// A pong that echoes no valid send-time (t0<=0) must NOT collapse RTT to the
// current epoch (the live bug: rtt_ms tracked the wall clock). It reports -1.
func TestStorePongNoT0YieldsNoData(t *testing.T) {
	now := int64(1_783_340_089_100) // epoch-scale, matching production
	s := New(func() int64 { return now })
	s.SetPong(0, 0, "", 0)
	if snap := s.Snapshot(); snap.RTTMs != -1 {
		t.Fatalf("RTTMs = %d, want -1 (a zero t0 must not yield an epoch-sized RTT)", snap.RTTMs)
	}
}

// RTT is a small delta even at epoch scale: locks the agent-clock semantics so a
// future regression that feeds a zeroed t0 into nowMs-t0 (=> epoch) fails here.
func TestStoreRTTEpochScale(t *testing.T) {
	now := int64(1_783_340_089_100)
	s := New(func() int64 { return now })
	s.SetPong(now-25, 0, "", 0)
	if snap := s.Snapshot(); snap.RTTMs != 25 {
		t.Fatalf("RTTMs = %d, want 25", snap.RTTMs)
	}
}

func TestStoreDriftMidnightWrap(t *testing.T) {
	// now (UTC ms) chosen so that local MSK time-of-day = 00:00:30 (30s past MSK
	// midnight): (now + 3h) mod 24h == 30_000ms.
	now := int64(75_630_000)
	s := New(func() int64 { return now })
	// Server reports 23:59:00 MSK (60s before midnight). Physically the true drift
	// is +90s (local is 90s ahead), not the naive ~24h same-day subtraction.
	s.SetPong(now, 0, "23:59:00", 0)
	snap := s.Snapshot()
	if snap.ClockDriftMs != 90_000 {
		t.Fatalf("ClockDriftMs = %d, want 90000 (midnight-wrap candidate)", snap.ClockDriftMs)
	}
}

func TestStoreDriftSameDay(t *testing.T) {
	// now (UTC ms) chosen so local MSK time-of-day = 12:00:05.
	now := int64(12*3600_000 + 5_000 - 3*3600_000)
	s := New(func() int64 { return now })
	s.SetPong(now, 0, "12:00:00", 0)
	snap := s.Snapshot()
	if snap.ClockDriftMs != 5_000 {
		t.Fatalf("ClockDriftMs = %d, want 5000", snap.ClockDriftMs)
	}
}

func TestStorePongAge(t *testing.T) {
	now := int64(1000)
	s := New(func() int64 { return now })
	s.SetPong(now-10, 0, "00:00:00", 0)
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

// TestStoreExchangeLagFromPong: production feeds ExchangeLagMs from SetPong's
// last_trade_ts_ms (the QLua pong's own exchange-timestamped field), not from the tape
// feed (whose rows carry the agent's receipt stamp, not the exchange's trade time).
func TestStoreExchangeLagFromPong(t *testing.T) {
	now := int64(1_000_000)
	s := New(func() int64 { return now })

	snap := s.Snapshot()
	if snap.ExchangeLagMs != -1 {
		t.Fatalf("ExchangeLagMs before any pong = %d, want -1 (no data)", snap.ExchangeLagMs)
	}

	// Lua has seen no trade yet (last_trade_ts_ms=0): must not fabricate a lag.
	s.SetPong(now, 0, "00:00:00", 0)
	snap = s.Snapshot()
	if snap.ExchangeLagMs != -1 {
		t.Fatalf("ExchangeLagMs with last_trade_ts_ms=0 = %d, want -1 (still no data)", snap.ExchangeLagMs)
	}

	// A pong carrying a real last_trade_ts_ms computes the lag as this pong's
	// receipt time minus that trade's exchange timestamp.
	now = 1_000_500
	s.SetPong(now, 0, "00:00:00", now-300)
	snap = s.Snapshot()
	if snap.ExchangeLagMs != 300 {
		t.Fatalf("ExchangeLagMs = %d, want 300", snap.ExchangeLagMs)
	}
}

// TestStoreAgesNegativeBeforeFirstPublish: PosAgeMs/OrdAgeMs must be -1 (not an
// epoch-sized number) before SetPositions/SetOrders has ever been called, and switch to
// a real age once data arrives.
func TestStoreAgesNegativeBeforeFirstPublish(t *testing.T) {
	now := int64(1_700_000_000_000)
	s := New(func() int64 { return now })

	snap := s.Snapshot()
	if snap.PosAgeMs != -1 || snap.OrdAgeMs != -1 {
		t.Fatalf("ages before any publish = pos=%d ord=%d, want -1/-1", snap.PosAgeMs, snap.OrdAgeMs)
	}
	if snap.PosAtMs != 0 || snap.OrdAtMs != 0 {
		t.Fatalf("stamps before any publish = pos=%d ord=%d, want 0/0", snap.PosAtMs, snap.OrdAtMs)
	}

	s.SetPositions([]Position{{Sec: "RIU6", Net: 1}})
	now += 250
	snap = s.Snapshot()
	if snap.PosAgeMs != 250 {
		t.Fatalf("PosAgeMs after publish = %d, want 250", snap.PosAgeMs)
	}
	if snap.OrdAgeMs != -1 {
		t.Fatalf("OrdAgeMs must still be -1 (orders never published), got %d", snap.OrdAgeMs)
	}
}

func TestMoneyFromRowAndStore(t *testing.T) {
	m, ok := MoneyFromRow([]any{1500000.5, -2300.25, 800.0, 120.5, 1400000.0})
	if !ok {
		t.Fatal("valid acc_money row must convert")
	}
	if got, want := m.Equity(), 1500000.5-2300.25+800.0; got != want {
		t.Fatalf("Equity = %v, want %v", got, want)
	}
	if _, ok := MoneyFromRow([]any{1.0, 2.0}); ok {
		t.Fatal("short row must be rejected")
	}

	now := int64(1000)
	s := New(func() int64 { return now })
	if snap := s.Snapshot(); snap.Money != nil || snap.MoneyAgeMs != -1 {
		t.Fatalf("fresh store must report no money data, got %+v", snap.Money)
	}
	s.SetMoney(m)
	now = 6000
	snap := s.Snapshot()
	if snap.Money == nil || snap.Money.Limit != 1500000.5 || snap.MoneyAgeMs != 5000 {
		t.Fatalf("got money %+v age %d", snap.Money, snap.MoneyAgeMs)
	}
}
