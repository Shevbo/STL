// Package recon is the pure comparator that decides whether the QUIK-hosted robots'
// believed books match QUIK fact (accounts.Snapshot + runner.Server.LastStatuses()) and
// computes a deterministic, operator-confirmable align Plan when they do not. No HTTP,
// no execution here — Task 7/8 wire Evaluate's inputs and act on its Plan.
//
// Attribution is BY TAG (the QUIK brokerref, stamped from the order COMMENT — see
// accounts.Order/Trade.Tag and trade.ownerTag):
//   - Tag == a deployed robot's tag (its ID) -> that robot's order/trade.
//   - Tag == "recon" -> the agent's own in-flight align order: NOT manual, NOT a mismatch,
//     never produces a step (a resting recon order is skipped, which is what preserves the
//     old "don't pile a second align on a symbol already being worked" behaviour without a
//     separate pending-symbol map).
//   - Tag == "" or any unknown value -> MANUAL (the operator's own trading on the same
//     account, or an order placed straight in the QUIK terminal): excluded from robot
//     recon, listed in Report.Manual, NEVER a mismatch, NEVER in an align plan (invariant
//     #1). Unknown tags are "not ours to touch".
//
// Position is CONTEXTUAL only. QUIK's account-net position mixes the operator's manual
// book with every robot's and cannot be split per robot, and summing a robot's tagged
// fills is unreliable (the trades ring holds only the last 500). So position is NEVER
// reconciled against account-net and NEVER generates a step. The recon SIGNAL for a robot
// is its orders + a bidirectional match of its recent fills against tagged QUIK trades;
// the robot's believed position and the raw account net are shown side by side for context
// (RobotCheck.Position and Report.Manual.AccountNet) but neither is checked against the
// other.
package recon

import (
	"fmt"
	"sort"
)

// reconTag is the reserved brokerref value the agent stamps on its own align orders. A
// QUIK order/trade carrying it is agent-owned and in-flight — skipped by classification.
const reconTag = "recon"

// staleThresholdMs gates the whole report STALE when either snapshot age exceeds it: an
// align decision must never be computed from data this old, even though the individual
// checks are still rendered from whatever data is at hand.
const staleThresholdMs = 30_000

// RobotView is one robot's belief about its own book, adapted from
// runner.Server.LastStatuses() (real robots) or the STL robot registry (paper robots).
// Paper robots are EXCLUDED from all QUIK matching: they place no real QUIK orders, so
// they carry no tag and contribute nothing to orders/trades/RobotChecks — see Evaluate.
type RobotView struct {
	ID, Symbol string
	// Tag is the brokerref the robot's real orders carry (== its ID, the value stamped
	// into the QUIK order COMMENT). Classification matches an Acc order/trade tag against
	// this. Empty for a robot that never places tagged orders (e.g. paper).
	Tag       string
	Paper     bool
	Position  int64
	AvgPrice  float64
	OrderNums []string  // QUIK order_nums of the robot's working orders
	FillKeys  []FillKey // recent real fills (paper robots contribute none)
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
	// Tag is the owner brokerref (robot ID / "recon" / "" for manual) — the whole basis
	// of attribution.
	Tag string
}

type Trade struct {
	Num, OrderNum, Sec string
	Price              float64
	Qty                int64
	TsMs               int64
	// Tag is the owner brokerref inherited from the order that produced this trade.
	Tag string
}

// AccView is accounts.Snapshot adapted for the comparator. PosAgeMs/OrdAgeMs gate
// staleness (negative means "never published", same as a too-old age — see Evaluate);
// PosAtMs/OrdAtMs are the ABSOLUTE receipt stamps of the underlying tables and are what
// Plan.ID hashes (see plan.go) — unlike the ages, they do not drift between reads of an
// unchanged table.
type AccView struct {
	Positions          []Position
	Orders             []Order
	Trades             []Trade
	PosAgeMs, OrdAgeMs int64
	PosAtMs, OrdAtMs   int64
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
	Robots    []RobotView
	Acc       AccView            // adapted from accounts.Snapshot
	Trans     []TransCheck       // pre-flagged hung/rejected trans from the Manager's pending state
	PriceStep map[string]float64 // symbol -> exchange price step; missing => exact-match trade fuzzing
	NowMs     int64
}

// OrderCheck is one robot-attributable active QUIK order's resolution. It is produced
// ONLY for orders that belong to a robot:
//   - Owner == a robot ID, OK == true: a tagged QUIK order the robot knows about.
//   - Owner == a robot ID, OK == false: ROBOT_ORPHAN — a QUIK order tagged for the robot
//     that the robot does NOT know (e.g. lost across a restart); an align cancel_order.
//   - Owner == "MISSING:<robotID>", OK == false: an order the robot believes is still
//     working but QUIK has no such active order; an align fix_state.
//
// MANUAL (untagged/unknown) and "recon" orders produce NO OrderCheck — manual goes to
// Report.Manual.Orders, recon is skipped entirely.
type OrderCheck struct {
	OrderNum, Owner string
	OK              bool
}

// TradeCheck is one real robot's believed fill matched (or not) against a tagged QUIK
// trade by tag, order_num, qty and price (within one price step, or exact if the step is
// unknown). Informational display of the forward direction; the per-robot TradesOK verdict
// (RobotCheck.TradesOK) additionally accounts for the reverse direction (a tagged QUIK
// trade the robot never recorded) and is what flips Report.State.
type TradeCheck struct {
	TradeID, OrderNum string
	Matched           bool
}

// ManualOrder is one active QUIK order attributed to the operator's manual trading
// (untagged or unknown tag). Shown in the "Ручная торговля (не сверяется)" section, never
// reconciled, never in an align plan.
type ManualOrder struct {
	OrderNum, Sec string
}

// PosLine is one symbol's raw QUIK account-net position, shown for context only.
type PosLine struct {
	Sec string
	Net int64
}

// ManualView is the manual-trading block: the operator's own working orders plus the raw
// account net, both purely contextual (invariant #1 / #5 — nothing here can be a mismatch
// or an align step).
type ManualView struct {
	Orders     []ManualOrder
	AccountNet []PosLine
}

// RobotCheck is one real robot's self-consistency summary: its believed position (shown,
// never reconciled against account-net) and whether its orders and recent trades line up
// with tagged QUIK fact. OrdersOK==false means a ROBOT_ORPHAN or MISSING; TradesOK==false
// means a bidirectional fill/trade mismatch. Either flips Report.State to MISMATCH.
type RobotCheck struct {
	ID, Symbol string
	Position   int64
	OrdersOK   bool
	TradesOK   bool
}

// Report is the full reconciliation result for one evaluation instant.
type Report struct {
	State       string // "OK" | "MISMATCH" | "STALE"
	Orders      []OrderCheck
	Trades      []TradeCheck
	Trans       []TransCheck
	Manual      ManualView
	RobotChecks []RobotCheck
	Plan        *Plan // nil unless State == "MISMATCH"
}

// Evaluate compares the robots' believed books against QUIK fact and, on a mismatch,
// computes a deterministic align Plan. Every output slice is sorted (Orders by OrderNum,
// Trades by TradeID/OrderNum/Matched, Trans by TransID, Manual.Orders by OrderNum,
// Manual.AccountNet by Sec, RobotChecks by ID, Steps by Kind/Symbol/OrderNum/RobotID) so
// that no map iteration order — nor the caller's input slice order — ever leaks into the
// Report or the Plan.ID hash.
func Evaluate(in Inputs) Report {
	// robotByTag keys the non-paper robots by their tag (its ID). Reserved/empty tags are
	// excluded so a manual ("") or agent ("recon") order can never collide onto a robot.
	robotByTag := map[string]RobotView{}
	var realRobots []RobotView
	for _, r := range in.Robots {
		if r.Paper {
			continue
		}
		realRobots = append(realRobots, r)
		if r.Tag != "" && r.Tag != reconTag {
			robotByTag[r.Tag] = r
		}
	}

	ordChecks, ordSteps, ordersBad, manualOrders := evalOrders(in, robotByTag)
	tradeChecks, tradesBad := evalTrades(in, robotByTag, realRobots)
	transChecks := append([]TransCheck(nil), in.Trans...)

	robotChecks := make([]RobotCheck, 0, len(realRobots))
	for _, r := range realRobots {
		robotChecks = append(robotChecks, RobotCheck{
			ID:       r.ID,
			Symbol:   r.Symbol,
			Position: r.Position,
			OrdersOK: !ordersBad[r.ID],
			TradesOK: !tradesBad[r.ID],
		})
	}

	accountNet := make([]PosLine, 0, len(in.Acc.Positions))
	for _, p := range in.Acc.Positions {
		accountNet = append(accountNet, PosLine{Sec: p.Sec, Net: p.Net})
	}

	sort.SliceStable(ordChecks, func(i, j int) bool { return ordChecks[i].OrderNum < ordChecks[j].OrderNum })
	sort.SliceStable(tradeChecks, func(i, j int) bool {
		if tradeChecks[i].TradeID != tradeChecks[j].TradeID {
			return tradeChecks[i].TradeID < tradeChecks[j].TradeID
		}
		if tradeChecks[i].OrderNum != tradeChecks[j].OrderNum {
			return tradeChecks[i].OrderNum < tradeChecks[j].OrderNum
		}
		return !tradeChecks[i].Matched && tradeChecks[j].Matched
	})
	sort.SliceStable(transChecks, func(i, j int) bool { return transChecks[i].TransID < transChecks[j].TransID })
	sort.SliceStable(manualOrders, func(i, j int) bool {
		if manualOrders[i].OrderNum != manualOrders[j].OrderNum {
			return manualOrders[i].OrderNum < manualOrders[j].OrderNum
		}
		return manualOrders[i].Sec < manualOrders[j].Sec
	})
	sort.SliceStable(accountNet, func(i, j int) bool { return accountNet[i].Sec < accountNet[j].Sec })
	sort.SliceStable(robotChecks, func(i, j int) bool { return robotChecks[i].ID < robotChecks[j].ID })

	rep := Report{
		Orders:      ordChecks,
		Trades:      tradeChecks,
		Trans:       transChecks,
		Manual:      ManualView{Orders: manualOrders, AccountNet: accountNet},
		RobotChecks: robotChecks,
	}

	// STALE gates everything else: the checks above still render from whatever data is
	// at hand, but no Plan is ever computed from stale data, and State is never falsely
	// reported OK. A negative age means the table has NEVER been published at all
	// (accounts.Store's -1 sentinel for "no data yet") — at least as stale as data that
	// is merely too old, never treated as fresh.
	if in.Acc.PosAgeMs < 0 || in.Acc.OrdAgeMs < 0 ||
		in.Acc.PosAgeMs > staleThresholdMs || in.Acc.OrdAgeMs > staleThresholdMs {
		rep.State = "STALE"
		return rep
	}

	// Only robot-attributable findings (ROBOT_ORPHAN/MISSING via ordChecks, a robot's
	// bidirectional trade mismatch via tradesBad) and pre-flagged trans issues flip State.
	// A MANUAL order/position can never appear in any of these (invariant #1 / #5).
	mismatch := anyOrderNotOK(ordChecks) || anyTransNotOK(transChecks) || len(tradesBad) > 0
	if !mismatch {
		rep.State = "OK"
		return rep
	}
	rep.State = "MISMATCH"

	steps := append([]Step(nil), ordSteps...)
	sortSteps(steps)
	rep.Plan = &Plan{
		Steps: steps,
		ID:    planID(steps, in.Acc.PosAtMs, in.Acc.OrdAtMs),
	}
	return rep
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

// evalOrders classifies every ACTIVE QUIK order by its tag and flags every real robot's
// claimed order_num that QUIK does not have active (MISSING). It returns the robot-only
// OrderChecks, the cancel_order/fix_state steps they imply, the set of robot IDs with an
// order finding (for RobotCheck.OrdersOK), and the manual (untagged/unknown-tag) orders.
// Inactive orders are terminal and reconcile nothing — they are ignored on every path.
func evalOrders(in Inputs, robotByTag map[string]RobotView) (checks []OrderCheck, steps []Step, ordersBad map[string]bool, manual []ManualOrder) {
	ordersBad = map[string]bool{}
	activeByNum := map[string]bool{}

	for _, o := range in.Acc.Orders {
		if !o.Active {
			continue
		}
		activeByNum[o.Num] = true

		switch {
		case o.Tag == "":
			// MANUAL: the operator's own working order. Never reconciled, never a step.
			manual = append(manual, ManualOrder{OrderNum: o.Num, Sec: o.Sec})
		case o.Tag == reconTag:
			// Agent's own in-flight align order: skip entirely (not manual, not a
			// mismatch). Skipping it is what suppresses a duplicate align on a symbol a
			// previous align is already working.
			continue
		default:
			r, ok := robotByTag[o.Tag]
			if !ok {
				// Unknown tag (e.g. a human order placed via the Manager whose raw
				// client_id became the tag): not ours to touch -> MANUAL.
				manual = append(manual, ManualOrder{OrderNum: o.Num, Sec: o.Sec})
				continue
			}
			if robotKnowsOrder(r, o.Num) {
				checks = append(checks, OrderCheck{OrderNum: o.Num, Owner: r.ID, OK: true})
			} else {
				checks = append(checks, OrderCheck{OrderNum: o.Num, Owner: r.ID, OK: false})
				ordersBad[r.ID] = true
				steps = append(steps, Step{
					Kind:     "cancel_order",
					Symbol:   o.Sec,
					OrderNum: o.Num,
					Detail: fmt.Sprintf(
						"QUIK order %s on %s is tagged for robot %s but the robot does not know it (ROBOT_ORPHAN — e.g. lost across a restart) — cancel it",
						o.Num, o.Sec, r.ID),
				})
			}
		}
	}

	for _, r := range in.Robots {
		if r.Paper {
			continue
		}
		for _, num := range r.OrderNums {
			if activeByNum[num] {
				continue
			}
			checks = append(checks, OrderCheck{OrderNum: num, Owner: "MISSING:" + r.ID, OK: false})
			ordersBad[r.ID] = true
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

	return checks, steps, ordersBad, manual
}

func robotKnowsOrder(r RobotView, num string) bool {
	for _, n := range r.OrderNums {
		if n == num {
			return true
		}
	}
	return false
}

// evalTrades bidirectionally matches every real robot's believed FillKey against QUIK
// trades tagged for that robot (tag == robot.Tag, order_num equal, qty equal, price within
// one PriceStep or exact if the symbol has no step). It returns the per-fill forward
// TradeChecks and the set of robot IDs whose fills do NOT line up — a robot fill with no
// matching tagged QUIK trade (forward) OR a tagged QUIK trade no fill claimed (reverse).
// Each QUIK trade row is consumed by at most one FillKey (a `used` set shared across all
// robots); because a trade's tag pins it to exactly one robot, robot iteration order can
// never change which robot consumes it, so the result is independent of input order. Paper
// robots contribute no FillKeys.
func evalTrades(in Inputs, robotByTag map[string]RobotView, realRobots []RobotView) (checks []TradeCheck, tradesBad map[string]bool) {
	tradesBad = map[string]bool{}
	used := make([]bool, len(in.Acc.Trades))

	for _, r := range realRobots {
		step := in.PriceStep[r.Symbol]
		for _, fk := range r.FillKeys {
			matched, tradeID := false, ""
			// A robot with no tag (a wiring quirk — real robots always carry one) can
			// match no tagged trade; its fills stay unmatched.
			if r.Tag != "" && r.Tag != reconTag {
				for i, tr := range in.Acc.Trades {
					if used[i] || tr.Tag != r.Tag || tr.OrderNum != fk.OrderNum || tr.Qty != fk.Qty {
						continue
					}
					diff := tr.Price - fk.Price
					if diff < 0 {
						diff = -diff
					}
					if (step > 0 && diff <= step) || (step <= 0 && diff == 0) {
						matched, tradeID = true, tr.Num
						used[i] = true
						break
					}
				}
			}
			checks = append(checks, TradeCheck{TradeID: tradeID, OrderNum: fk.OrderNum, Matched: matched})
			if !matched {
				tradesBad[r.ID] = true
			}
		}
	}

	// Reverse: a QUIK trade tagged for a known robot that no fill claimed means the robot
	// never recorded a fill reality shows — a genuine divergence within the live window.
	for i, tr := range in.Acc.Trades {
		if used[i] {
			continue
		}
		if r, ok := robotByTag[tr.Tag]; ok {
			tradesBad[r.ID] = true
		}
	}

	return checks, tradesBad
}
