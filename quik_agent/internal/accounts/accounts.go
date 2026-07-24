// Package accounts is the snapshot store for QUIK account state (positions, working
// orders, recent trades) and agent<->QUIK clock health (round-trip time, clock drift,
// tape exchange lag). It is fed from the QLua acc_pos/acc_ord/acc_trd/pong events
// decoded by trade.Bridge's AccEvent sink; the status page reads Snapshot().
//
// Every mutation and read is mutex-guarded so it is safe to call from the bridge's
// reader goroutine (writes) and the HTTP status handler (reads) concurrently.
package accounts

import (
	"math"
	"slices"
	"strconv"
	"strings"
	"sync"
)

// Position mirrors one row of the QUIK account position table.
type Position struct {
	Sec string
	Net int64
	Avg float64
	// VarMargin is futures_client_holding.varmargin — the per-instrument ВМ.
	// 0 on an old Lua build that publishes only [sec, net, avg]; HasVarMargin
	// tells the two apart so the UI can print "—" instead of a fake zero.
	VarMargin    float64
	HasVarMargin bool
}

// Order mirrors one row of the QUIK working-orders table.
type Order struct {
	Num, Sec string
	Active   bool
	Price    float64
	Balance  int64
	Qty      int64
	// Tag is the owner tag (brokerref, stamped from the order COMMENT — see
	// trade.ownerTag) decoded from the row's optional trailing 7th element.
	// Empty when absent (old Lua build) or unknown.
	Tag string
	// Side ("B"/"S") and TsMs (order registration epoch ms) come from optional
	// trailing row elements 8-9 (Lua cc3+); zero-valued on an old Lua build.
	Side string
	TsMs int64
}

// Trade mirrors one row of the QUIK trades table (a fill).
type Trade struct {
	Num, OrderNum, Sec string
	Price              float64
	Qty                int64
	TsMs               int64
	// Tag is the owner tag (brokerref) decoded from the row's optional trailing
	// 7th element. Empty when absent (old Lua build) or unknown.
	Tag string
	// Side ("B"/"S") and ExchTsMs (exchange trade time, epoch ms) come from
	// optional trailing row elements 8-9 (Lua cc3+); zero-valued on an old build.
	// TsMs above stays the Lua RECEIPT stamp (recon depends on it).
	Side     string
	ExchTsMs int64
}

// Money mirrors the futures_limits money row (limit_type 0, "денежные средства") —
// the REAL account state for strict day-close accounting. Equity() is QUIK's
// "средства": limit + variation margin since the last clearing + accrued income.
type Money struct {
	Limit       float64 // cbplimit: money limit set at the last clearing
	VarMargin   float64 // varmargin: ВМ accumulated since the last clearing
	AccruedInt  float64 // accruedint: накопленный доход (earlier clearings today)
	TsComission float64 // ts_comission: exchange fees for the session
	Planned     float64 // cbplplanned: free money (planned limit)
	// Used is cbplused ("Тек. чист. поз." — money currently bound by positions).
	// 0 on an old Lua build; HasUsed tells "no data" from a real zero.
	Used    float64
	HasUsed bool
}

// Equity is the QUIK "средства" figure: Limit + VarMargin + AccruedInt.
func (m Money) Equity() float64 { return m.Limit + m.VarMargin + m.AccruedInt }

// TransReply mirrors one OnTransReply (the QUIK транзакции table has no QLua
// getItem access; this ring of replies is the programmatic equivalent for
// transactions sent from this terminal).
type TransReply struct {
	TsMs     int64
	TransID  int64
	Status   int32
	OrderNum string
	Text     string
}

// tradesRing caps how many recent trades Snapshot() exposes. QUIK re-sends the full
// trades table from scratch on every session rollover; without a cap that would grow
// the in-memory slice without bound over an agent's uptime.
const tradesRing = 500

// mskOffsetMs is the fixed MSK UTC offset. MSK has observed no DST since 2014, so a
// fixed +3h is correct year-round (unlike, say, US/Europe offsets).
const mskOffsetMs = 3 * 3600 * 1000

const dayMs = 24 * 3600 * 1000

// Snapshot is the point-in-time read of the store, computed against the injected
// clock at the moment Snapshot() is called (ages are NOT frozen at write time).
type Snapshot struct {
	Positions []Position
	Orders    []Order
	Trades    []Trade
	// TransReplies is the OnTransReply ring, arrival order (oldest first).
	TransReplies []TransReply
	// QuikFolder is the QUIK working folder reported by the Lua pong ("" until
	// a cc3+ pong arrives); the status page tails info.log/news.log there.
	QuikFolder string

	// Money is the futures_limits money row; nil until the first acc_money
	// frame arrives (old Lua build publishes none). MoneyAgeMs is -1 then.
	Money      *Money
	MoneyAgeMs int64

	// PosAgeMs/OrdAgeMs are -1 when the corresponding table has NEVER been
	// published (SetPositions/SetOrders not yet called) — never an
	// epoch-sized number from subtracting against a zero-value timestamp.
	// Otherwise ms since the last Set call, computed against s.now() every
	// read.
	PosAgeMs int64
	OrdAgeMs int64
	// PosAtMs/OrdAtMs are the ABSOLUTE receipt timestamps (UnixMilli) of the
	// last SetPositions/SetOrders call, 0 if never called. Unlike the ages
	// above, these do NOT change between reads unless the underlying table
	// actually changed — recon.Plan.ID hashes these, not the ages, so a
	// confirm computed minutes after a poll still matches (see recon/plan.go).
	PosAtMs int64
	OrdAtMs int64

	RTTMs        int64 // agent->Lua->agent round trip of the last pong
	ClockDriftMs int64 // local MSK time-of-day minus QUIK server time-of-day
	PongAgeMs    int64 // ms since the last SetPong call
	// ExchangeLagMs is -1 when no trade has ever been observed (Lua's
	// last_trade_ts_ms is 0), else the freshest pong-derived sample: pong
	// receipt time minus that trade's exchange timestamp.
	ExchangeLagMs int64
}

// Store is the mutex-guarded account/clock-health snapshot. now is injected so tests
// control time deterministically; production wiring passes a wall-clock UTC-ms func.
type Store struct {
	mu  sync.Mutex
	now func() int64

	positions []Position
	posAtMs   int64 // content-version stamp (recon plan ID): advances only on a content change
	posRecvMs int64 // receipt stamp (freshness/STALE age): advances on every publish

	orders    []Order
	ordAtMs   int64 // content-version stamp (recon plan ID)
	ordRecvMs int64 // receipt stamp (freshness/STALE age)

	trades     []Trade
	seenTrades map[string]struct{} // every accepted Trade.Num (survives ring eviction)

	transReplies []TransReply
	quikFolder   string

	money       *Money
	moneyRecvMs int64

	rttMs        int64
	clockDriftMs int64
	pongAtMs     int64

	exchangeLagMs  int64
	lastTapeRecvMs int64
	haveTapeLag    bool
}

// New builds an empty Store. now must return the current time as UTC milliseconds
// (production wiring uses time.Now().UnixMilli; tests inject a fake clock).
func New(now func() int64) *Store {
	return &Store{
		now:        now,
		seenTrades: make(map[string]struct{}),
		rttMs:      -1, // "no data" until the first pong with a valid send-time
	}
}

// SetPositions replaces the position table (QUIK sends the full table each poll).
// The receipt stamp (posRecvMs) advances every call so the freshness age stays low
// under the Lua keepalive; the content stamp (posAtMs, which the recon plan ID hashes)
// advances ONLY when the rows actually change, so a keepalive re-emit of an unchanged
// table cannot rotate the plan ID and 409 the operator's align confirm.
func (s *Store) SetPositions(rows []Position) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	if s.posRecvMs == 0 || !slices.Equal(s.positions, rows) {
		s.posAtMs = now
	}
	s.posRecvMs = now
	s.positions = append([]Position(nil), rows...) // defensive copy: caller may reuse rows
}

// SetOrders replaces the working-orders table (QUIK sends the full table each poll).
// See SetPositions for the receipt-vs-content stamp split.
func (s *Store) SetOrders(rows []Order) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	if s.ordRecvMs == 0 || !slices.Equal(s.orders, rows) {
		s.ordAtMs = now
	}
	s.ordRecvMs = now
	s.orders = append([]Order(nil), rows...) // defensive copy: caller may reuse rows
}

// seenTradesCap bounds the persistent dedupe set. When exceeded, the set is reset to
// the nums currently in the ring — a rare event (at most ~once a day on a very busy
// account) that trades a brief dedupe-window narrowing for bounded memory.
const seenTradesCap = 50_000

// AddTrades merges newly seen trades into the ring, deduping by Trade.Num. This is
// required because a QUIK session rollover resets the Lua trades cursor and RE-SENDS
// the full trades table from scratch; without dedupe every rollover would duplicate
// every historical trade still inside the ring window.
//
// The dedupe set is PERSISTENT (independent of the ring): a num that was accepted and
// later evicted from the 500-ring stays suppressed. Otherwise a full-table resend
// would re-append already-evicted old trades at the tail and the front-trim would
// then evict genuinely newer trades by slice position. The ring therefore keeps pure
// arrival order; evicted-and-resent nums never re-enter.
func (s *Store) AddTrades(rows []Trade) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, tr := range rows {
		if _, seen := s.seenTrades[tr.Num]; seen {
			continue
		}
		s.trades = append(s.trades, tr)
		s.seenTrades[tr.Num] = struct{}{}
	}
	if len(s.trades) > tradesRing {
		drop := len(s.trades) - tradesRing
		s.trades = append([]Trade(nil), s.trades[drop:]...)
	}
	if len(s.seenTrades) > seenTradesCap {
		s.seenTrades = make(map[string]struct{}, tradesRing)
		for _, tr := range s.trades {
			s.seenTrades[tr.Num] = struct{}{}
		}
	}
}

// transRing caps the OnTransReply ring (same rationale as tradesRing).
const transRing = 200

// AddTransReply appends one OnTransReply to the ring, stamped with the store clock.
func (s *Store) AddTransReply(transID int64, status int32, orderNum, text string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.transReplies = append(s.transReplies, TransReply{
		TsMs: s.now(), TransID: transID, Status: status, OrderNum: orderNum, Text: text,
	})
	if len(s.transReplies) > transRing {
		drop := len(s.transReplies) - transRing
		s.transReplies = append([]TransReply(nil), s.transReplies[drop:]...)
	}
}

// SetMoney records the latest futures_limits money row.
func (s *Store) SetMoney(m Money) {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := m
	s.money = &cp
	s.moneyRecvMs = s.now()
}

// SetQuikFolder records the QUIK working folder from a Lua pong (ignored when empty).
func (s *Store) SetQuikFolder(dir string) {
	if dir == "" {
		return
	}
	s.mu.Lock()
	s.quikFolder = dir
	s.mu.Unlock()
}

// SetPong records a QUIK clock-sync round trip. t0Ms is the agent-stamped send time
// echoed back by Lua; serverTime is QUIK's own clock as "HH:MM:SS" (MSK, no DST).
// lastTradeTsMs is the exchange timestamp (epoch ms) of the freshest all-trade Lua has
// seen, 0 if none yet — when > 0 it feeds the exchange-lag measurement (pong receipt
// time minus that trade's exchange time); 0 leaves ExchangeLagMs at its current value
// (still -1/"no data" on a fresh store — see Snapshot).
func (s *Store) SetPong(t0Ms, _luaTsMs int64, serverTime string, lastTradeTsMs int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	nowMs := s.now()
	if t0Ms > 0 {
		s.rttMs = nowMs - t0Ms
	} else {
		s.rttMs = -1 // no valid send-time echoed; report "no data", not an epoch-sized RTT
	}
	s.pongAtMs = nowMs
	if drift, ok := clockDriftMs(nowMs, serverTime); ok {
		s.clockDriftMs = drift
	}
	if lastTradeTsMs > 0 {
		s.setTapeLagLocked(lastTradeTsMs, nowMs)
	}
}

// SetTapeLag feeds one (exchange timestamp, agent receive time) sample. Only the
// freshest pair (by recvMs) is kept, so an out-of-order/stale sample cannot clobber a
// more recent measurement. Exported for direct unit testing; production callers go
// through SetPong (see setTapeLagLocked), which is fed from the QLua pong's
// last_trade_ts_ms rather than the tape feed (tape rows carry the agent's OWN receipt
// stamp, not the exchange's trade time — see shectory_trade.lua OnAllTrade).
func (s *Store) SetTapeLag(exchTsMs, recvMs int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.setTapeLagLocked(exchTsMs, recvMs)
}

// setTapeLagLocked is SetTapeLag's body, callable while s.mu is already held (from
// SetPong).
func (s *Store) setTapeLagLocked(exchTsMs, recvMs int64) {
	if s.haveTapeLag && recvMs < s.lastTapeRecvMs {
		return
	}
	s.lastTapeRecvMs = recvMs
	s.exchangeLagMs = recvMs - exchTsMs
	s.haveTapeLag = true
}

// Snapshot returns a point-in-time copy of the store. Ages are computed against the
// injected clock at call time, not frozen at the last write.
func (s *Store) Snapshot() Snapshot {
	s.mu.Lock()
	defer s.mu.Unlock()
	nowMs := s.now()

	posAge := int64(-1)
	if s.posRecvMs != 0 {
		posAge = nowMs - s.posRecvMs
	}
	ordAge := int64(-1)
	if s.ordRecvMs != 0 {
		ordAge = nowMs - s.ordRecvMs
	}
	exchLag := int64(-1)
	if s.haveTapeLag {
		exchLag = s.exchangeLagMs
	}
	var money *Money
	moneyAge := int64(-1)
	if s.money != nil {
		cp := *s.money
		money = &cp
		moneyAge = nowMs - s.moneyRecvMs
	}

	return Snapshot{
		Positions:    append([]Position(nil), s.positions...),
		Orders:       append([]Order(nil), s.orders...),
		Trades:       append([]Trade(nil), s.trades...),
		TransReplies: append([]TransReply(nil), s.transReplies...),
		QuikFolder:   s.quikFolder,

		Money:      money,
		MoneyAgeMs: moneyAge,

		PosAgeMs: posAge,
		OrdAgeMs: ordAge,
		PosAtMs:  s.posAtMs,
		OrdAtMs:  s.ordAtMs,

		RTTMs:         s.rttMs,
		ClockDriftMs:  s.clockDriftMs,
		PongAgeMs:     nowMs - s.pongAtMs,
		ExchangeLagMs: exchLag,
	}
}

// clockDriftMs computes local-MSK-time-of-day minus the QUIK server's time-of-day
// (serverTime, "HH:MM:SS", already MSK). nowMs is UTC ms; MSK time-of-day is derived
// by adding the fixed +3h offset and reducing mod 24h (MSK observes no DST). Because
// both values are pure times-of-day, a naive subtraction is wrong across a midnight
// boundary (e.g. 00:00:05 vs 23:59:55 is actually a 10s drift, not ~24h); we pick
// whichever of {d, d+24h, d-24h} has the smallest absolute value.
func clockDriftMs(nowMs int64, serverTime string) (int64, bool) {
	serverMs, ok := parseHHMMSS(serverTime)
	if !ok {
		return 0, false
	}
	localMs := ((nowMs+mskOffsetMs)%dayMs + dayMs) % dayMs
	d := localMs - serverMs
	best := d
	for _, cand := range [...]int64{d + dayMs, d - dayMs} {
		if absInt64(cand) < absInt64(best) {
			best = cand
		}
	}
	return best, true
}

func absInt64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}

// parseHHMMSS parses a QUIK "HH:MM:SS" server-time string into milliseconds since
// local midnight.
func parseHHMMSS(s string) (int64, bool) {
	parts := strings.Split(s, ":")
	if len(parts) != 3 {
		return 0, false
	}
	h, err1 := strconv.Atoi(parts[0])
	m, err2 := strconv.Atoi(parts[1])
	sec, err3 := strconv.Atoi(parts[2])
	if err1 != nil || err2 != nil || err3 != nil {
		return 0, false
	}
	return int64(h)*3600_000 + int64(m)*60_000 + int64(sec)*1000, true
}

// ---- row converters ----
// QLua rows arrive as [][]any decoded from JSON, where every number (int or float)
// decodes to float64. These converters are type-tolerant: they accept float64 or a
// numeric string for numeric fields, and reject (ok=false) a row that is short or
// carries an unconvertible value, so one malformed row cannot corrupt the whole batch.

// PositionFromRow converts one acc_pos row: [sec, net, avg], plus an OPTIONAL
// trailing 4th element (varmargin) — absent on an old Lua build, in which case
// VarMargin stays 0 and HasVarMargin false.
func PositionFromRow(row []any) (Position, bool) {
	if len(row) < 3 {
		return Position{}, false
	}
	sec, ok := asString(row[0])
	if !ok {
		return Position{}, false
	}
	net, ok := asInt(row[1])
	if !ok {
		return Position{}, false
	}
	avg, ok := asFloat(row[2])
	if !ok {
		return Position{}, false
	}
	p := Position{Sec: sec, Net: net, Avg: avg}
	if len(row) >= 4 {
		// A malformed 4th element must not drop the whole row: the position
		// itself is still valid, only the ВМ is unknown.
		if vm, ok := asFloat(row[3]); ok {
			p.VarMargin, p.HasVarMargin = vm, true
		}
	}
	return p, true
}

// OrderFromRow converts one acc_ord row: [order_num, sec, active(0|1), price, balance,
// qty], plus an OPTIONAL trailing 7th element (the owner tag/brokerref) — absent on an
// old Lua build, in which case Tag stays "".
func OrderFromRow(row []any) (Order, bool) {
	if len(row) < 6 {
		return Order{}, false
	}
	num, ok := asString(row[0])
	if !ok {
		return Order{}, false
	}
	sec, ok := asString(row[1])
	if !ok {
		return Order{}, false
	}
	active, ok := asBool01(row[2])
	if !ok {
		return Order{}, false
	}
	price, ok := asFloat(row[3])
	if !ok {
		return Order{}, false
	}
	balance, ok := asInt(row[4])
	if !ok {
		return Order{}, false
	}
	qty, ok := asInt(row[5])
	if !ok {
		return Order{}, false
	}
	out := Order{Num: num, Sec: sec, Active: active, Price: price, Balance: balance, Qty: qty}
	if len(row) >= 7 {
		if s, ok := row[6].(string); ok {
			out.Tag = s
		}
	}
	if len(row) >= 9 { // Lua cc3+: side, registration ts_ms
		if s, ok := row[7].(string); ok {
			out.Side = s
		}
		if ts, ok := asInt(row[8]); ok {
			out.TsMs = ts
		}
	}
	return out, true
}

// TradeFromRow converts one acc_trd row: [trade_num, order_num, sec, price, qty,
// ts_ms], plus an OPTIONAL trailing 7th element (the owner tag/brokerref) — absent on
// an old Lua build, in which case Tag stays "".
func TradeFromRow(row []any) (Trade, bool) {
	if len(row) < 6 {
		return Trade{}, false
	}
	num, ok := asString(row[0])
	if !ok {
		return Trade{}, false
	}
	orderNum, ok := asString(row[1])
	if !ok {
		return Trade{}, false
	}
	sec, ok := asString(row[2])
	if !ok {
		return Trade{}, false
	}
	price, ok := asFloat(row[3])
	if !ok {
		return Trade{}, false
	}
	qty, ok := asInt(row[4])
	if !ok {
		return Trade{}, false
	}
	tsMs, ok := asInt(row[5])
	if !ok {
		return Trade{}, false
	}
	out := Trade{Num: num, OrderNum: orderNum, Sec: sec, Price: price, Qty: qty, TsMs: tsMs}
	if len(row) >= 7 {
		if s, ok := row[6].(string); ok {
			out.Tag = s
		}
	}
	if len(row) >= 9 { // Lua cc3+: side, exchange trade ts_ms
		if s, ok := row[7].(string); ok {
			out.Side = s
		}
		if ts, ok := asInt(row[8]); ok {
			out.ExchTsMs = ts
		}
	}
	return out, true
}

// MoneyFromRow converts one acc_money row: [cbplimit, varmargin, accruedint,
// ts_comission, cbplplanned], plus an OPTIONAL trailing 6th element (cbplused,
// "Тек. чист. поз.") — absent on an old Lua build, HasUsed false then.
func MoneyFromRow(row []any) (Money, bool) {
	if len(row) < 5 {
		return Money{}, false
	}
	vals := make([]float64, 5)
	for i := 0; i < 5; i++ {
		f, ok := asFloat(row[i])
		if !ok {
			return Money{}, false
		}
		vals[i] = f
	}
	m := Money{Limit: vals[0], VarMargin: vals[1], AccruedInt: vals[2],
		TsComission: vals[3], Planned: vals[4]}
	if len(row) >= 6 {
		if u, ok := asFloat(row[5]); ok {
			m.Used, m.HasUsed = u, true
		}
	}
	return m, true
}

func asString(v any) (string, bool) {
	switch x := v.(type) {
	case string:
		return x, true
	case float64:
		if x == math.Trunc(x) {
			return strconv.FormatInt(int64(x), 10), true
		}
		return strconv.FormatFloat(x, 'f', -1, 64), true
	default:
		return "", false
	}
}

func asFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case string:
		f, err := strconv.ParseFloat(x, 64)
		if err != nil {
			return 0, false
		}
		return f, true
	default:
		return 0, false
	}
}

func asInt(v any) (int64, bool) {
	f, ok := asFloat(v)
	if !ok {
		return 0, false
	}
	return int64(f), true
}

func asBool01(v any) (bool, bool) {
	f, ok := asFloat(v)
	if !ok {
		return false, false
	}
	return f != 0, true
}
