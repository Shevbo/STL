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
	"strconv"
	"strings"
	"sync"
)

// Position mirrors one row of the QUIK account position table.
type Position struct {
	Sec string
	Net int64
	Avg float64
}

// Order mirrors one row of the QUIK working-orders table.
type Order struct {
	Num, Sec string
	Active   bool
	Price    float64
	Balance  int64
	Qty      int64
}

// Trade mirrors one row of the QUIK trades table (a fill).
type Trade struct {
	Num, OrderNum, Sec string
	Price              float64
	Qty                int64
	TsMs               int64
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

	PosAgeMs int64 // ms since the last SetPositions call
	OrdAgeMs int64 // ms since the last SetOrders call

	RTTMs         int64 // agent->Lua->agent round trip of the last pong
	ClockDriftMs  int64 // local MSK time-of-day minus QUIK server time-of-day
	PongAgeMs     int64 // ms since the last SetPong call
	ExchangeLagMs int64 // freshest tape sample: agent recv time minus exchange ts
}

// Store is the mutex-guarded account/clock-health snapshot. now is injected so tests
// control time deterministically; production wiring passes a wall-clock UTC-ms func.
type Store struct {
	mu  sync.Mutex
	now func() int64

	positions []Position
	posAtMs   int64

	orders []Order
	ordAtMs int64

	trades   []Trade
	tradeIdx map[string]struct{} // Trade.Num currently present in `trades`

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
		now:      now,
		tradeIdx: make(map[string]struct{}),
	}
}

// SetPositions replaces the position table (QUIK sends the full table each poll).
func (s *Store) SetPositions(rows []Position) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.positions = rows
	s.posAtMs = s.now()
}

// SetOrders replaces the working-orders table (QUIK sends the full table each poll).
func (s *Store) SetOrders(rows []Order) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.orders = rows
	s.ordAtMs = s.now()
}

// AddTrades merges newly seen trades into the ring, deduping by Trade.Num. This is
// required because a QUIK session rollover resets the Lua trades cursor and RE-SENDS
// the full trades table from scratch; without dedupe every rollover would duplicate
// every historical trade still inside the ring window.
func (s *Store) AddTrades(rows []Trade) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, tr := range rows {
		if _, seen := s.tradeIdx[tr.Num]; seen {
			continue
		}
		s.trades = append(s.trades, tr)
		s.tradeIdx[tr.Num] = struct{}{}
	}
	if len(s.trades) > tradesRing {
		drop := len(s.trades) - tradesRing
		for _, tr := range s.trades[:drop] {
			delete(s.tradeIdx, tr.Num)
		}
		s.trades = append([]Trade(nil), s.trades[drop:]...)
	}
}

// SetPong records a QUIK clock-sync round trip. t0Ms is the agent-stamped send time
// echoed back by Lua; serverTime is QUIK's own clock as "HH:MM:SS" (MSK, no DST).
func (s *Store) SetPong(t0Ms, _luaTsMs int64, serverTime string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	nowMs := s.now()
	s.rttMs = nowMs - t0Ms
	s.pongAtMs = nowMs
	if drift, ok := clockDriftMs(nowMs, serverTime); ok {
		s.clockDriftMs = drift
	}
}

// SetTapeLag feeds one (exchange timestamp, agent receive time) sample from the tape
// MD sink (wired in a later task). Only the freshest pair (by recvMs) is kept, so an
// out-of-order/stale sample cannot clobber a more recent measurement.
func (s *Store) SetTapeLag(exchTsMs, recvMs int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
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
	return Snapshot{
		Positions: append([]Position(nil), s.positions...),
		Orders:    append([]Order(nil), s.orders...),
		Trades:    append([]Trade(nil), s.trades...),

		PosAgeMs: nowMs - s.posAtMs,
		OrdAgeMs: nowMs - s.ordAtMs,

		RTTMs:         s.rttMs,
		ClockDriftMs:  s.clockDriftMs,
		PongAgeMs:     nowMs - s.pongAtMs,
		ExchangeLagMs: s.exchangeLagMs,
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

// PositionFromRow converts one acc_pos row: [sec, net, avg].
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
	return Position{Sec: sec, Net: net, Avg: avg}, true
}

// OrderFromRow converts one acc_ord row: [order_num, sec, active(0|1), price, balance, qty].
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
	return Order{Num: num, Sec: sec, Active: active, Price: price, Balance: balance, Qty: qty}, true
}

// TradeFromRow converts one acc_trd row: [trade_num, order_num, sec, price, qty, ts_ms].
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
	return Trade{Num: num, OrderNum: orderNum, Sec: sec, Price: price, Qty: qty, TsMs: tsMs}, true
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
