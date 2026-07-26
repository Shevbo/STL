package runner

import (
	"testing"

	"shectory/quik_agent/internal/accounts"
	quikv1 "shectory/quik_agent/internal/pb"
)

// Fixed instant: 2026-07-13 20:00 MSK = 17:00 UTC.
const nowMs = int64(1783962000000)

func status(paper bool, hbMs int64, fills []*quikv1.RobotFill, working []*quikv1.RobotWorkingOrder) *quikv1.RobotStatus {
	return &quikv1.RobotStatus{Paper: paper, HeartbeatUnixMs: hbMs,
		RecentFills: fills, WorkingOrders: working}
}

func fill(orderNum string, qty int64, ts int64) *quikv1.RobotFill {
	return &quikv1.RobotFill{OrderId: orderNum, Qty: qty, Status: "filled", TsUnixMs: ts}
}

func trade(tag, orderNum, side string, qty int64, price float64, ts int64) accounts.Trade {
	return accounts.Trade{Num: orderNum + "-t", OrderNum: orderNum, Sec: "RIU6",
		Price: price, Qty: qty, TsMs: ts, Tag: tag, Side: side}
}

// The 2026-07-13 incident shape: fills lost between QUIK and the journal ->
// synthesize exactly the shortfall.
func TestMissingFillsRestoresLostTrade(t *testing.T) {
	st := map[string]*quikv1.RobotStatus{
		"r1": status(false, nowMs-5000, []*quikv1.RobotFill{fill("100", 1, nowMs-3600_000)}, nil),
	}
	trades := []accounts.Trade{
		trade("r1", "100", "B", 1, 87000, nowMs-3600_000), // known -> skip
		trade("r1", "200", "S", 1, 88000, nowMs-600_000),  // LOST -> restore
	}
	ups := MissingFills(st, trades, nowMs)
	if len(ups) != 1 {
		t.Fatalf("want 1 synthetic update, got %d", len(ups))
	}
	u := ups[0]
	if u.GetOrderId() != "200" || u.GetFilled() != 1 || u.GetSide() != quikv1.Side_SIDE_SELL {
		t.Fatalf("bad update: %+v", u)
	}
	if u.GetClientId() != "rr:r1:qsync:200:1" {
		t.Fatalf("client_id must be stable/idempotent, got %q", u.GetClientId())
	}
	if u.GetState() != quikv1.OrderState_ORDER_STATE_FILLED {
		t.Fatalf("state must be FILLED")
	}
}

// Partial recorded before a crash: only the qty DIFF is synthesized, at the
// order's trade VWAP.
func TestMissingFillsQtyDiffAndVwap(t *testing.T) {
	st := map[string]*quikv1.RobotStatus{
		"r1": status(false, nowMs-5000, []*quikv1.RobotFill{fill("300", 2, nowMs-600_000)}, nil),
	}
	trades := []accounts.Trade{
		trade("r1", "300", "B", 2, 87000, nowMs-600_000),
		trade("r1", "300", "B", 3, 87100, nowMs-500_000),
	}
	ups := MissingFills(st, trades, nowMs)
	if len(ups) != 1 || ups[0].GetFilled() != 3 {
		t.Fatalf("want diff qty 3, got %+v", ups)
	}
	wantVwap := (87000.0*2 + 87100.0*3) / 5
	if ups[0].GetPrice() != wantVwap {
		t.Fatalf("want vwap %.2f, got %.2f", wantVwap, ups[0].GetPrice())
	}
	// client_id keys on the QUIK total (5), so a re-detection dedups runner-side
	if ups[0].GetClientId() != "rr:r1:qsync:300:5" {
		t.Fatalf("client_id: %q", ups[0].GetClientId())
	}
}

func TestMissingFillsGuards(t *testing.T) {
	freshTrade := trade("r1", "400", "S", 1, 88000, nowMs-10_000) // younger than age gate
	lost := trade("r1", "500", "S", 1, 88000, nowMs-600_000)

	// paper robot -> never healed
	st := map[string]*quikv1.RobotStatus{"r1": status(true, nowMs-5000, nil, nil)}
	if got := MissingFills(st, []accounts.Trade{lost}, nowMs); len(got) != 0 {
		t.Fatalf("paper robot healed: %+v", got)
	}
	// stale heartbeat -> skip (book view untrusted)
	st = map[string]*quikv1.RobotStatus{"r1": status(false, nowMs-600_000, nil, nil)}
	if got := MissingFills(st, []accounts.Trade{lost}, nowMs); len(got) != 0 {
		t.Fatalf("stale robot healed: %+v", got)
	}
	// fresh trade -> normal event path still has time
	st = map[string]*quikv1.RobotStatus{"r1": status(false, nowMs-5000, nil, nil)}
	if got := MissingFills(st, []accounts.Trade{freshTrade}, nowMs); len(got) != 0 {
		t.Fatalf("fresh trade healed early: %+v", got)
	}
	// working order -> normal event path owns it
	st = map[string]*quikv1.RobotStatus{"r1": status(false, nowMs-5000, nil,
		[]*quikv1.RobotWorkingOrder{{OrderId: "500"}})}
	if got := MissingFills(st, []accounts.Trade{lost}, nowMs); len(got) != 0 {
		t.Fatalf("working order healed: %+v", got)
	}
	// manual (untagged) and recon trades -> never touched
	st = map[string]*quikv1.RobotStatus{"r1": status(false, nowMs-5000, nil, nil)}
	manual := lost
	manual.Tag = ""
	rec := lost
	rec.Tag = "recon"
	if got := MissingFills(st, []accounts.Trade{manual, rec}, nowMs); len(got) != 0 {
		t.Fatalf("manual/recon healed: %+v", got)
	}
	// unknown side (old Lua) -> cannot synthesize safely
	noSide := lost
	noSide.Side = ""
	if got := MissingFills(st, []accounts.Trade{noSide}, nowMs); len(got) != 0 {
		t.Fatalf("side-less trade healed: %+v", got)
	}
}

// A 200-capped tail that already cut into today undercounts the journal ->
// healing would double-apply; the robot must be skipped.
func TestMissingFillsTailCutGuard(t *testing.T) {
	fills := make([]*quikv1.RobotFill, syncTailCap)
	for i := range fills {
		fills[i] = fill("x", 1, nowMs-1000) // oldest entry inside today
	}
	st := map[string]*quikv1.RobotStatus{"r1": status(false, nowMs-5000, fills, nil)}
	lost := trade("r1", "600", "S", 1, 88000, nowMs-600_000)
	if got := MissingFills(st, []accounts.Trade{lost}, nowMs); len(got) != 0 {
		t.Fatalf("tail-cut robot healed: %+v", got)
	}
}

// The journal stamp must be the EXCHANGE trade time when the Lua build supplies
// it: a restart re-stamps receipt times to NOW, which would journal a restored
// fill hours off its true chart spot.
func TestMissingFillsPrefersExchangeTime(t *testing.T) {
	st := map[string]*quikv1.RobotStatus{"r1": status(false, nowMs-5000, nil, nil)}
	tr := trade("r1", "700", "S", 1, 88000, nowMs-600_000) // receipt: 10 min ago
	tr.ExchTsMs = nowMs - 7200_000                          // exchange: 2h ago
	ups := MissingFills(st, []accounts.Trade{tr}, nowMs)
	if len(ups) != 1 || ups[0].GetTsUnixMs() != nowMs-7200_000 {
		t.Fatalf("want exchange ts, got %+v", ups)
	}
}

// The 2026-07-24 l90z0 incident: QUIK truncates brokerref to 20 chars, so a
// cuid-named robot's (24 chars) tagged trades matched no view key and the heal
// was DEAD for exactly the most active robots — a lost 13:30 fill sat unhealed
// for 4 hours. The heal must match by the 20-char truncation and emit the
// synthetic update under the FULL robot id (the rr:<id> fan-out key).
func TestMissingFillsMatchesTruncatedTag(t *testing.T) {
	const rid = "l90z0afzceesll5izjjg0g8w" // 24 chars, live shape
	st := map[string]*quikv1.RobotStatus{
		rid: status(false, nowMs-5000, []*quikv1.RobotFill{fill("100", 1, nowMs-3600_000)}, nil),
	}
	trades := []accounts.Trade{
		trade(rid[:20], "100", "B", 1, 87000, nowMs-3600_000), // known -> skip
		trade(rid[:20], "200", "S", 1, 87700, nowMs-600_000),  // LOST -> restore
	}
	ups := MissingFills(st, trades, nowMs)
	if len(ups) != 1 {
		t.Fatalf("truncated-tag trade must heal; got %d updates", len(ups))
	}
	if got := ups[0].GetClientId(); got != "rr:"+rid+":qsync:200:1" {
		t.Fatalf("synthetic client_id must carry the FULL robot id, got %q", got)
	}
}

// The 2026-07-24->26 double-debit: the 13:30 sell was recorded by hand
// (record-fill-agent, order_id "manual-<ts>") at 17:41, then the heal
// synthesized the SAME trade again at 23:08 — its dedup keys on the QUIK
// order num, which the manual record does not carry. A manual record of the
// same sec+side at ~the order's VWAP must credit the shortfall.
func TestMissingFillsManualRecordCredits(t *testing.T) {
	manualRec := &quikv1.RobotFill{OrderId: "manual-1784889005000", Symbol: "RIU6",
		Side: quikv1.Side_SIDE_SELL, Qty: 1, Price: 87700, Status: "filled",
		TsUnixMs: nowMs - 3600_000}
	st := map[string]*quikv1.RobotStatus{
		"r1": status(false, nowMs-5000, []*quikv1.RobotFill{manualRec}, nil),
	}
	// The same real trade, robot-tagged in QUIK's table -> must NOT re-synthesize.
	same := []accounts.Trade{trade("r1", "800", "S", 1, 87700, nowMs-600_000)}
	if got := MissingFills(st, same, nowMs); len(got) != 0 {
		t.Fatalf("manually recorded trade healed again (double debit): %+v", got)
	}
	// A DIFFERENT lost trade (far price) must still heal — and the one credit
	// must not be consumed twice across orders.
	far := []accounts.Trade{
		trade("r1", "800", "S", 1, 87700, nowMs-600_000), // covered by the record
		trade("r1", "801", "S", 1, 89600, nowMs-500_000), // genuinely lost
	}
	got := MissingFills(st, far, nowMs)
	if len(got) != 1 || got[0].GetOrderId() != "801" {
		t.Fatalf("distinct lost trade must still heal exactly once: %+v", got)
	}
}

// Two robots sharing the same 20-char prefix cannot be attributed safely —
// heal NEITHER rather than guess.
func TestMissingFillsAmbiguousPrefixHealsNeither(t *testing.T) {
	a, b := "prefix-prefix-prefix-AAAA", "prefix-prefix-prefix-BBBB"
	st := map[string]*quikv1.RobotStatus{
		a: status(false, nowMs-5000, nil, nil),
		b: status(false, nowMs-5000, nil, nil),
	}
	trades := []accounts.Trade{trade(a[:20], "300", "B", 1, 87000, nowMs-600_000)}
	if ups := MissingFills(st, trades, nowMs); len(ups) != 0 {
		t.Fatalf("ambiguous prefix must heal neither, got %d", len(ups))
	}
}
