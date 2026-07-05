// Package recon is the pure comparator that decides whether the QUIK-hosted robots'
// believed books match QUIK fact (accounts.Snapshot + runner.Server.LastStatuses()) and
// computes a deterministic, operator-confirmable align Plan when they do not. No HTTP,
// no execution here — Task 7/8 wire Evaluate's inputs and act on its Plan.
package recon

import (
	"fmt"
	"sort"
)

// staleThresholdMs gates the whole report STALE when either snapshot age exceeds it: an
// align decision must never be computed from data this old, even though the individual
// checks are still rendered from whatever data is at hand.
const staleThresholdMs = 30_000

// RobotView is one robot's belief about its own book, adapted from
// runner.Server.LastStatuses() (real robots) or the STL robot registry (paper robots).
// Paper robots are EXCLUDED from all QUIK matching: their Position/OrderNums/FillKeys
// never contribute to a PosCheck/OrderCheck/TradeCheck — see Evaluate.
type RobotView struct {
	ID, Symbol string
	Paper      bool
	Position   int64
	AvgPrice   float64
	OrderNums  []string  // QUIK order_nums of the robot's working orders
	FillKeys   []FillKey // recent real fills (paper robots contribute none)
}

// FillKey identifies one fill a robot believes happened, for cross-checking against
// AccView.Trades.
type FillKey struct {
	OrderNum string
	Qty      int64
	Price    float64
}

// Position/Order/Trade mirror accounts.Snapshot's row shapes field-for-field; the
// wiring layer (Task 7/8) copies accounts.Position/Order/Trade into these verbatim.
type Position struct {
	Sec string
	Net int64
	Avg float64
}

type Order struct {
	Num, Sec     string
	Active       bool
	Price        float64
	Balance, Qty int64
}

type Trade struct {
	Num, OrderNum, Sec string
	Price              float64
	Qty                int64
	TsMs               int64
}

// AccView is accounts.Snapshot adapted for the comparator.
type AccView struct {
	Positions          []Position
	Orders             []Order
	Trades             []Trade
	PosAgeMs, OrdAgeMs int64
}

// TransCheck surfaces one hung-past-reconcile or rejected transaction, pre-flagged by
// the Manager's pending state. Evaluate passes these through unchanged into
// Report.Trans; a not-OK entry flips Report.State to MISMATCH but never generates a
// Step — there is nothing an align plan can automatically do about a stuck/rejected
// transaction, it is surfaced for the operator to read.
type TransCheck struct {
	TransID      int64
	Status, Text string
	OK           bool
}

// Inputs is everything Evaluate needs. It is pure data: no clock, no randomness, no I/O
// — the caller supplies NowMs so Evaluate is a deterministic function of its arguments
// alone (same Inputs, in any slice/map iteration order, always produce the same Report).
type Inputs struct {
	Robots       []RobotView
	HumanOrders  map[string]bool    // order_nums owned by the human path (Manager, non-"rr:")
	Acc          AccView            // adapted from accounts.Snapshot
	Trans        []TransCheck       // pre-flagged hung/rejected trans from the Manager's pending state
	ManualOffset map[string]int64   // symbol -> operator-declared manual position
	PriceStep    map[string]float64 // symbol -> exchange price step; missing => exact-match trade fuzzing
	NowMs        int64
}

// PosCheck is one symbol's position reconciliation: real robots' Position summed, plus
// the operator's manual offset, checked against QUIK's net position for that symbol. A
// symbol missing from AccView.Positions is treated as Quik net 0.
type PosCheck struct {
	Symbol                        string
	RobotsSum, Quik, ManualOffset int64
	OK                            bool
}

// OrderCheck is one QUIK-active order's ownership resolution, OR one real robot's claimed
// order_num that QUIK does not have active. Owner is a robot ID, "human", "ORPHAN" (an
// active QUIK order nobody claims), or "MISSING:<robotID>" (a robot's order_num that QUIK
// does not have active).
type OrderCheck struct {
	OrderNum, Owner string
	OK              bool
}

// TradeCheck is one real robot's believed fill matched (or not) against a QUIK trade by
// order_num, qty and price (within one price step, or exact if the step is unknown).
// Informational only — never generates a Step, never by itself flips Report.State.
type TradeCheck struct {
	TradeID, OrderNum string
	Matched           bool
}

// Report is the full reconciliation result for one evaluation instant.
type Report struct {
	State     string // "OK" | "MISMATCH" | "STALE"
	Positions []PosCheck
	Orders    []OrderCheck
	Trades    []TradeCheck
	Trans     []TransCheck
	Plan      *Plan // nil unless State == "MISMATCH"
}

// Evaluate compares the robots' believed books against QUIK fact and, on a mismatch,
// computes a deterministic align Plan. Every output slice is sorted (Positions by
// Symbol, Orders by OrderNum, Trades by TradeID/OrderNum, Steps by
// Kind/Symbol/OrderNum/RobotID) so that no map iteration order — nor the caller's input
// slice order — ever leaks into the Report or the Plan.ID hash.
func Evaluate(in Inputs) Report {
	posChecks := evalPositions(in)
	ordChecks, ordSteps := evalOrders(in)
	tradeChecks := evalTrades(in)
	transChecks := append([]TransCheck(nil), in.Trans...)

	sort.SliceStable(posChecks, func(i, j int) bool { return posChecks[i].Symbol < posChecks[j].Symbol })
	sort.SliceStable(ordChecks, func(i, j int) bool { return ordChecks[i].OrderNum < ordChecks[j].OrderNum })
	sort.SliceStable(tradeChecks, func(i, j int) bool {
		if tradeChecks[i].TradeID != tradeChecks[j].TradeID {
			return tradeChecks[i].TradeID < tradeChecks[j].TradeID
		}
		return tradeChecks[i].OrderNum < tradeChecks[j].OrderNum
	})
	sort.SliceStable(transChecks, func(i, j int) bool { return transChecks[i].TransID < transChecks[j].TransID })

	rep := Report{
		Positions: posChecks,
		Orders:    ordChecks,
		Trades:    tradeChecks,
		Trans:     transChecks,
	}

	// STALE gates everything else: the checks above still render from whatever data is
	// at hand, but no Plan is ever computed from stale data, and State is never
	// falsely reported OK.
	if in.Acc.PosAgeMs > staleThresholdMs || in.Acc.OrdAgeMs > staleThresholdMs {
		rep.State = "STALE"
		return rep
	}

	mismatch := anyNotOK(posChecks) || anyOrderNotOK(ordChecks) || anyTransNotOK(transChecks)
	if !mismatch {
		rep.State = "OK"
		return rep
	}
	rep.State = "MISMATCH"

	// close_position is suppressed for any symbol a MISSING/ORPHAN order finding
	// already explains — an operator should resolve the order first, not fight the
	// robot's own working order with a position trade.
	explainedSymbol := map[string]bool{}
	for _, s := range ordSteps {
		explainedSymbol[s.Symbol] = true
	}
	steps := append([]Step(nil), ordSteps...)
	for _, p := range posChecks {
		if p.OK || explainedSymbol[p.Symbol] {
			continue
		}
		steps = append(steps, closePositionStep(p))
	}
	sortSteps(steps)

	rep.Plan = &Plan{
		Steps: steps,
		ID:    planID(steps, in.Acc.PosAgeMs, in.Acc.OrdAgeMs),
	}
	return rep
}

func anyNotOK(checks []PosCheck) bool {
	for _, c := range checks {
		if !c.OK {
			return true
		}
	}
	return false
}

func anyOrderNotOK(checks []OrderCheck) bool {
	for _, c := range checks {
		if !c.OK {
			return true
		}
	}
	return false
}

func anyTransNotOK(checks []TransCheck) bool {
	for _, c := range checks {
		if !c.OK {
			return true
		}
	}
	return false
}

// closePositionStep builds the single corrective step for a symbol whose position
// equation failed and that no MISSING/ORPHAN order finding explains. Qty is signed:
// positive means the account is LONG in excess of the robots' claimed books (the align
// step must SELL that excess); negative means the account is SHORT of what the robots
// claim (the align step must BUY back).
func closePositionStep(p PosCheck) Step {
	qty := p.Quik - p.RobotsSum - p.ManualOffset
	dir, amt := "BUY", -qty
	if qty > 0 {
		dir, amt = "SELL", qty
	}
	return Step{
		Kind:   "close_position",
		Symbol: p.Symbol,
		Qty:    qty,
		Detail: fmt.Sprintf(
			"%s: robots' combined position %d + manual offset %d != QUIK net %d, and no robot/order claims the excess — %s %d to align",
			p.Symbol, p.RobotsSum, p.ManualOffset, p.Quik, dir, amt),
	}
}

// evalPositions groups real (non-paper) robots' Position by symbol and checks
// sum + ManualOffset against QUIK's net position. A symbol present only in
// ManualOffset or only in QUIK positions still gets a row (defaults of 0 on the other
// side apply).
func evalPositions(in Inputs) []PosCheck {
	symbols := map[string]bool{}
	robotsSum := map[string]int64{}
	for _, r := range in.Robots {
		if r.Paper {
			continue
		}
		symbols[r.Symbol] = true
		robotsSum[r.Symbol] += r.Position
	}
	quikNet := map[string]int64{}
	for _, p := range in.Acc.Positions {
		symbols[p.Sec] = true
		quikNet[p.Sec] = p.Net
	}
	for sym := range in.ManualOffset {
		symbols[sym] = true
	}

	checks := make([]PosCheck, 0, len(symbols))
	for sym := range symbols {
		manual := in.ManualOffset[sym]
		sum := robotsSum[sym]
		net := quikNet[sym]
		checks = append(checks, PosCheck{
			Symbol:       sym,
			RobotsSum:    sum,
			Quik:         net,
			ManualOffset: manual,
			OK:           sum+manual == net,
		})
	}
	return checks
}

// evalOrders resolves ownership of every active QUIK order (robot / human / ORPHAN) and
// flags every real robot's claimed order_num that QUIK does not have active (MISSING).
// Paper robots' OrderNums never count as ownership and never generate a MISSING check.
func evalOrders(in Inputs) ([]OrderCheck, []Step) {
	activeByNum := map[string]Order{}
	for _, o := range in.Acc.Orders {
		if o.Active {
			activeByNum[o.Num] = o
		}
	}
	robotOwner := map[string]string{}
	for _, r := range in.Robots {
		if r.Paper {
			continue
		}
		for _, num := range r.OrderNums {
			robotOwner[num] = r.ID
		}
	}

	var checks []OrderCheck
	var steps []Step

	for num, o := range activeByNum {
		owner, ok := "ORPHAN", false
		if rid, isRobot := robotOwner[num]; isRobot {
			owner, ok = rid, true
		} else if in.HumanOrders[num] {
			owner, ok = "human", true
		}
		checks = append(checks, OrderCheck{OrderNum: num, Owner: owner, OK: ok})
		if !ok {
			steps = append(steps, Step{
				Kind:     "cancel_order",
				Symbol:   o.Sec,
				OrderNum: num,
				Detail: fmt.Sprintf(
					"QUIK order %s on %s is active but owned by neither a robot nor the human order path — orphan, cancel it",
					num, o.Sec),
			})
		}
	}

	for _, r := range in.Robots {
		if r.Paper {
			continue
		}
		for _, num := range r.OrderNums {
			if _, active := activeByNum[num]; active {
				continue
			}
			checks = append(checks, OrderCheck{OrderNum: num, Owner: "MISSING:" + r.ID, OK: false})
			steps = append(steps, Step{
				Kind:     "fix_state",
				Symbol:   r.Symbol,
				OrderNum: num,
				RobotID:  r.ID,
				SetPos:   r.Position,
				SetAvg:   r.AvgPrice,
				Detail: fmt.Sprintf(
					"Robot %s believes order %s is still working but QUIK has no such active order — clear_working (drop the phantom working order) and reset the robot's state to its current position=%d avg=%.4f",
					r.ID, num, r.Position, r.AvgPrice),
			})
		}
	}

	return checks, steps
}

// evalTrades checks every real robot's believed FillKey against AccView.Trades by
// order_num, exact qty, and price within one PriceStep (or exact price if the symbol has
// no configured step). Paper robots contribute no FillKeys.
func evalTrades(in Inputs) []TradeCheck {
	var checks []TradeCheck
	for _, r := range in.Robots {
		if r.Paper {
			continue
		}
		step := in.PriceStep[r.Symbol]
		for _, fk := range r.FillKeys {
			matched, tradeID := false, ""
			for _, tr := range in.Acc.Trades {
				if tr.OrderNum != fk.OrderNum || tr.Qty != fk.Qty {
					continue
				}
				diff := tr.Price - fk.Price
				if diff < 0 {
					diff = -diff
				}
				if (step > 0 && diff <= step) || (step <= 0 && diff == 0) {
					matched, tradeID = true, tr.Num
					break
				}
			}
			checks = append(checks, TradeCheck{TradeID: tradeID, OrderNum: fk.OrderNum, Matched: matched})
		}
	}
	return checks
}
