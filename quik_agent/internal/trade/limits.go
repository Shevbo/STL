package trade

import (
	"fmt"
	"math"
	"strings"
	"sync"
	"time"
)

// Limits holds the agent-side hard limits enforced BEFORE anything reaches the Lua
// bridge / QUIK. They are a SECOND line of defense on top of STL's identical checks
// (defense in depth). A request failing ANY limit is rejected in the agent and is
// NEVER sent to Lua. Secrets/account are not part of limits and are never hardcoded.
//
// All fields are plain config values (see internal/config). The master flag
// TradingEnabled defaults to false: when off, ALL order commands are rejected.
type Limits struct {
	// TradingEnabled is the master flag. false (default) => every order command is
	// rejected with reason "trading disabled". Guard 3: no order without an explicit,
	// confirmed command AND this flag on.
	TradingEnabled bool
	// MaxContractsPerOrder caps the quantity of a single placement.
	MaxContractsPerOrder int64
	// MaxWorkingContracts caps the total resting (working) quantity across all open
	// orders at once.
	MaxWorkingContracts int64
	// PriceCollarFrac is the maximum adverse fractional deviation allowed from a
	// reference price (e.g. 0.002 = 0.2%). Used by CheckCollar.
	PriceCollarFrac float64
	// InstrumentWhitelist is the set of allowed instrument codes. Empty => nothing is
	// allowed (fail closed).
	InstrumentWhitelist []string
	// DailyOrderCap is the maximum number of placements per calendar day (agent local
	// time). 0 => no placements allowed (fail closed).
	DailyOrderCap int
}

// RejectReason is a stable machine-readable reason for a limit rejection. It is also
// used as human text on the OrderUpdate REJECTED.
type RejectReason string

const (
	ReasonTradingDisabled RejectReason = "trading disabled (quik_trading_enabled=false)"
	ReasonNotWhitelisted  RejectReason = "instrument not whitelisted"
	ReasonQtyNonPositive  RejectReason = "quantity must be positive"
	ReasonQtyPerOrder     RejectReason = "quantity exceeds max_contracts_per_order"
	ReasonWorkingCap      RejectReason = "would exceed max_working_contracts"
	ReasonPriceNonPositive RejectReason = "price must be positive"
	ReasonDailyCap        RejectReason = "daily_order_cap reached"
	ReasonBlocked         RejectReason = "blocked by kill-switch (cleared explicitly)"
	ReasonCollarHit       RejectReason = "price beyond collar"
)

// Guard tracks per-day placement counts and resting quantity so the cap and the
// working limit can be enforced. It is safe for concurrent use. The order manager
// owns one Guard and consults it under its own lock; the methods here only guard the
// counters themselves.
type Guard struct {
	limits Limits

	mu          sync.Mutex
	day         string // YYYY-MM-DD of the current count window
	placedToday int
	// nowFn is injectable for tests.
	nowFn func() time.Time
}

// NewGuard builds a Guard for the given limits.
func NewGuard(l Limits) *Guard {
	return &Guard{limits: l, nowFn: time.Now}
}

// Limits returns the configured limits (read-only copy).
func (g *Guard) Limits() Limits { return g.limits }

func (g *Guard) now() time.Time {
	if g.nowFn != nil {
		return g.nowFn()
	}
	return time.Now()
}

// rollDay resets the daily counter when the calendar day changes. Caller holds mu.
func (g *Guard) rollDay() {
	d := g.now().Format("2006-01-02")
	if d != g.day {
		g.day = d
		g.placedToday = 0
	}
}

// whitelisted reports whether code is allowed. Case-insensitive, trimmed.
func (l Limits) whitelisted(code string) bool {
	c := strings.ToLower(strings.TrimSpace(code))
	for _, w := range l.InstrumentWhitelist {
		if strings.ToLower(strings.TrimSpace(w)) == c {
			return true
		}
	}
	return false
}

// PlaceCheck is the input to a placement limit check.
type PlaceCheck struct {
	Code     string
	Price    float64
	Quantity int64
	// CurrentWorking is the total resting quantity BEFORE this order (the manager
	// supplies it from its working-order book).
	CurrentWorking int64
}

// CheckPlace validates a placement against every hard limit WITHOUT mutating any
// counter. It returns ok=false and a reason on the first violation. The master flag
// is checked first. Counting toward the daily cap happens in CommitPlace, only after
// the manager has decided to actually send the order.
func (g *Guard) CheckPlace(p PlaceCheck) (bool, RejectReason) {
	if !g.limits.TradingEnabled {
		return false, ReasonTradingDisabled
	}
	if !g.limits.whitelisted(p.Code) {
		return false, ReasonNotWhitelisted
	}
	if p.Quantity <= 0 {
		return false, ReasonQtyNonPositive
	}
	if g.limits.MaxContractsPerOrder > 0 && p.Quantity > g.limits.MaxContractsPerOrder {
		return false, ReasonQtyPerOrder
	}
	if g.limits.MaxWorkingContracts > 0 && p.CurrentWorking+p.Quantity > g.limits.MaxWorkingContracts {
		return false, ReasonWorkingCap
	}
	if p.Price <= 0 || math.IsNaN(p.Price) || math.IsInf(p.Price, 0) {
		return false, ReasonPriceNonPositive
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.rollDay()
	if g.placedToday >= g.dailyCap() {
		return false, ReasonDailyCap
	}
	return true, ""
}

func (g *Guard) dailyCap() int {
	if g.limits.DailyOrderCap < 0 {
		return 0
	}
	return g.limits.DailyOrderCap
}

// CommitPlace records one placement against the daily cap. Call it only once the
// order is actually about to be sent to Lua (after CheckPlace passed). It re-checks
// the cap atomically and returns false if the cap was hit in the meantime.
func (g *Guard) CommitPlace() (bool, RejectReason) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.rollDay()
	if g.placedToday >= g.dailyCap() {
		return false, ReasonDailyCap
	}
	g.placedToday++
	return true, ""
}

// PlacedToday returns the current day's placement count (for diagnostics/tests).
func (g *Guard) PlacedToday() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.rollDay()
	return g.placedToday
}

// CheckCollar reports whether price is within the collar around reference for the
// given side. For a BUY, prices ABOVE reference*(1+frac) are adverse; for a SELL,
// prices BELOW reference*(1-frac) are adverse. frac<=0 disables the check (allow).
// This is used both for an explicit PlaceOrder.collar override and for the maker
// loop's worst-price bound.
func CheckCollar(buy bool, reference, price, frac float64) (bool, RejectReason) {
	if frac <= 0 || reference <= 0 {
		return true, ""
	}
	if buy {
		if price > reference*(1+frac) {
			return false, ReasonCollarHit
		}
	} else {
		if price < reference*(1-frac) {
			return false, ReasonCollarHit
		}
	}
	return true, ""
}

// WorstPrice returns the collar bound price for a side given a reference price and
// fraction: the highest tolerable buy price, or the lowest tolerable sell price.
func WorstPrice(buy bool, reference, frac float64) float64 {
	if frac <= 0 || reference <= 0 {
		return reference
	}
	if buy {
		return reference * (1 + frac)
	}
	return reference * (1 - frac)
}

// String renders limits for log/diagnostics without leaking anything sensitive
// (there is nothing sensitive here; account/secrets are not part of limits).
func (l Limits) String() string {
	return fmt.Sprintf("trading_enabled=%v max_per_order=%d max_working=%d collar=%.4f whitelist=%v daily_cap=%d",
		l.TradingEnabled, l.MaxContractsPerOrder, l.MaxWorkingContracts, l.PriceCollarFrac, l.InstrumentWhitelist, l.DailyOrderCap)
}
