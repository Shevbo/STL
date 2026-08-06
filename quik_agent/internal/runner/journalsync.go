package runner

import (
	"context"
	"fmt"
	"strings"
	"time"

	"shectory/quik_agent/internal/accounts"
	quikv1 "shectory/quik_agent/internal/pb"
)

// Journal auto-heal: QUIK is the source of truth for REAL fills. A robot-tagged
// trade in today's QUIK trades table that the robot's believed book does not
// account for is a LOST fill (runner died between fill and persist — seen live
// 2026-07-13, book frozen at 10:15 while 12 real fills piled up; event-path
// outage; wiped state). Recon only FLAGS the divergence; this loop repairs it:
// the missing quantity is synthesized as a normal OrderUpdate and fanned through
// the regular runner event path, so the runner applies it via on_order_event,
// persists it, and the next status report converges recon back to OK.
//
// Idempotency is structural: the synthetic client_id is a pure function of
// (robot, order_num, total QUIK qty for that order) and the runner dedups fill
// application per client_id. Re-detecting the same shortfall re-sends the same
// client_id -> zero fresh qty applied. A later extra trade on the same order
// raises the QUIK total -> new client_id -> only the increment applies.

const (
	syncInterval       = 60 * time.Second
	syncMinTradeAgeMs  = 90_000 // let the normal event path deliver first
	syncMaxHeartbeatMs = 90_000 // heal only robots whose book view is FRESH
	syncMaxPerCycle    = 30     // runaway guard: heal gradually, never storm
	syncTailCap        = 200    // runner's persisted fills tail size (runtime.py)
)

// MissingFills computes synthetic OrderUpdates for robot-tagged QUIK trades the
// robots' believed books do not cover. Pure: no clock, no I/O — nowMs injected.
func MissingFills(statuses map[string]*quikv1.RobotStatus, trades []accounts.Trade, nowMs int64) []*quikv1.OrderUpdate {
	floor := mskMidnightMs(nowMs)

	// journalQty[robot][orderNum] = qty the robot's book already accounts for:
	// recorded real fills + still-working orders (their fills are in flight on
	// the normal path and must never be pre-empted).
	// manual = operator record-fill entries (order_id "manual-<ts>", link/robots.go):
	// they book a REAL trade under a non-QUIK order id, so journalQty never sees
	// them — 2026-07-24 the same sell was debited twice (manual record 17:41 +
	// qsync heal 23:08) and the account ran +1 vs the robots. Credited below.
	type manualCredit struct {
		sec   string
		side  quikv1.Side
		price float64
		qty   int64
	}
	type robotView struct {
		journalQty map[string]int64
		working    map[string]bool
		manual     []manualCredit
	}
	views := map[string]*robotView{}
	for id, st := range statuses {
		if st.GetPaper() {
			continue // paper robots place no QUIK orders; nothing to heal
		}
		if hb := st.GetHeartbeatUnixMs(); hb == 0 || nowMs-hb > syncMaxHeartbeatMs {
			continue // stale book view: healing against it could double-apply
		}
		fills := st.GetRecentFills()
		// Tail-cut guard: a 200-capped tail whose OLDEST entry is already inside
		// today may have dropped today's fills -> journalQty would undercount and
		// the heal would double-apply. Skip such a robot (recon still flags it).
		if len(fills) >= syncTailCap && len(fills) > 0 && fills[0].GetTsUnixMs() >= floor {
			continue
		}
		v := &robotView{journalQty: map[string]int64{}, working: map[string]bool{}}
		for _, f := range fills {
			if f.GetStatus() == "filled" && f.GetOrderId() != "" {
				v.journalQty[f.GetOrderId()] += f.GetQty()
				if strings.HasPrefix(f.GetOrderId(), "manual-") {
					v.manual = append(v.manual, manualCredit{sec: f.GetSymbol(),
						side: f.GetSide(), price: f.GetPrice(), qty: f.GetQty()})
				}
			}
		}
		for _, w := range st.GetWorkingOrders() {
			if w.GetOrderId() != "" {
				v.working[w.GetOrderId()] = true
			}
		}
		views[id] = v
	}
	// QUIK truncates the brokerref COMMENT to 20 chars, so a tagged trade carries
	// only the first 20 chars of the robot id. Match by that truncation — the
	// exact-key lookup left the heal DEAD for every cuid-named robot (24 chars):
	// l90z0's lost 13:30 fill on 2026-07-24 sat unhealed for 4 hours while the
	// short-named robots healed fine. Same trap recon fixed on 2026-07-21
	// (recon.quikTag); an ambiguous truncation (two robots sharing a prefix)
	// heals neither — safety over coverage.
	const brokerrefLen = 20
	viewByTag := map[string]*robotView{}
	robotByTag := map[string]string{}
	for id, v := range views {
		tag := id
		if len(tag) > brokerrefLen {
			tag = tag[:brokerrefLen]
		}
		if _, dup := viewByTag[tag]; dup {
			delete(viewByTag, tag) // ambiguous prefix: refuse to heal either
			delete(robotByTag, tag)
			continue
		}
		viewByTag[tag] = v
		robotByTag[tag] = id
	}

	// Aggregate today's settled tagged QUIK trades per (robot, order).
	type orderAgg struct {
		robot, sec, side string
		qty              int64
		vwapNum          float64 // Σ price×qty
		lastTsMs         int64   // journal stamp: EXCHANGE time when known (cc3+ Lua),
		// else the Lua receipt stamp. A restart re-stamps receipts to NOW, which
		// would journal a restored fill hours off its true spot on the chart.
	}
	aggs := map[string]*orderAgg{} // key: robot + "\x00" + orderNum
	var keys []string              // insertion order -> deterministic output
	for _, tr := range trades {
		if tr.Tag == "" || tr.Tag == "recon" || tr.OrderNum == "" || tr.Qty <= 0 {
			continue
		}
		robotID, ok := robotByTag[tr.Tag]
		if !ok {
			continue // not a (healable) robot
		}
		// Floor/age judged by the EXCHANGE time when the Lua ships it, else the
		// QUIK stamp. QUIK dates EVENING-session trades (19:05+) with the NEXT
		// trading day: yesterday-evening trades read "today 19:2x" in TsMs, sit
		// "in the future" all day, and after today's clock passes that time the
		// old guards saw fresh-but-unaccounted trades and REPLAYED yesterday's
		// evening into the book, minute by minute (lxk22, 2026-08-06 19:23-19:33:
		// dozens of phantom fills, position drifted +3 vs QUIK +10, realized
		// corrupted). ExchTsMs carries the TRUE execution time and kills the
		// whole class: yesterday's trade fails the midnight floor.
		effTs := tr.ExchTsMs
		if effTs <= 0 {
			effTs = tr.TsMs
		}
		if effTs < floor || nowMs-effTs < syncMinTradeAgeMs {
			continue
		}
		if tr.Side != "B" && tr.Side != "S" {
			continue // old Lua build without a side column: cannot synthesize safely
		}
		k := robotID + "\x00" + tr.OrderNum
		a := aggs[k]
		if a == nil {
			a = &orderAgg{robot: robotID, sec: tr.Sec, side: tr.Side}
			aggs[k] = a
			keys = append(keys, k)
		}
		a.qty += tr.Qty
		a.vwapNum += tr.Price * float64(tr.Qty)
		ts := tr.ExchTsMs
		if ts <= 0 {
			ts = tr.TsMs
		}
		if ts > a.lastTsMs {
			a.lastTsMs = ts
		}
	}

	var out []*quikv1.OrderUpdate
	for _, k := range keys {
		a := aggs[k]
		v := views[a.robot]
		orderNum := k[len(a.robot)+1:]
		if v.working[orderNum] {
			continue // normal event path still owns this order
		}
		missing := a.qty - v.journalQty[orderNum]
		if missing <= 0 {
			continue
		}
		// Consume operator manual records: same sec+side at ~the order's VWAP
		// (0.2% — the collar's order of magnitude) is the SAME trade already
		// booked by hand; synthesizing it again double-debits the book.
		vwap := a.vwapNum / float64(a.qty)
		sideEnum := quikv1.Side_SIDE_BUY
		if a.side == "S" {
			sideEnum = quikv1.Side_SIDE_SELL
		}
		for i := range v.manual {
			m := &v.manual[i]
			if missing <= 0 {
				break
			}
			if m.qty <= 0 || m.sec != a.sec || m.side != sideEnum {
				continue
			}
			if d := m.price - vwap; d > 0.002*vwap || d < -0.002*vwap {
				continue
			}
			use := m.qty
			if use > missing {
				use = missing
			}
			m.qty -= use
			missing -= use
		}
		if missing <= 0 {
			continue
		}
		side := quikv1.Side_SIDE_BUY
		if a.side == "S" {
			side = quikv1.Side_SIDE_SELL
		}
		out = append(out, &quikv1.OrderUpdate{
			// qty-suffixed: same shortfall -> same id (runner dedups); a later
			// extra trade raises the QUIK total -> new id -> only the increment.
			ClientId: fmt.Sprintf("rr:%s:qsync:%s:%d", a.robot, orderNum, a.qty),
			OrderId:  orderNum,
			Code:     a.sec,
			Side:     side,
			State:    quikv1.OrderState_ORDER_STATE_FILLED,
			Price:    a.vwapNum / float64(a.qty),
			Quantity: missing,
			Filled:   missing,
			Text:     "journal-sync: restored from the QUIK trades table",
			TsUnixMs: a.lastTsMs,
		})
		if len(out) >= syncMaxPerCycle {
			break
		}
	}
	return out
}

// JournalSyncLoop runs MissingFills on a ticker and fans the repairs through the
// runner bridge until ctx is done.
func JournalSyncLoop(ctx context.Context, srv *Server, acc *accounts.Store, logf func(string, ...any)) {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	t := time.NewTicker(syncInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			ups := MissingFills(srv.LastStatuses(), acc.Snapshot().Trades, time.Now().UnixMilli())
			for _, u := range ups {
				logf("restoring lost fill: %s %s qty=%d @ %.0f (order %s)",
					u.GetClientId(), u.GetSide(), u.GetFilled(), u.GetPrice(), u.GetOrderId())
				srv.FanOrderEvent(u)
			}
		}
	}
}

// mskMidnightMs returns 00:00 MSK (UTC+3, no DST since 2014) of the day
// containing nowMs, as epoch-ms. acc_trd holds today's session only; fills and
// trades are compared within it (mirrors internal/status.mskMidnightMs).
func mskMidnightMs(nowMs int64) int64 {
	const day = int64(24 * 60 * 60 * 1000)
	const mskOffset = int64(3 * 60 * 60 * 1000)
	shifted := nowMs + mskOffset
	return (shifted/day)*day - mskOffset
}
