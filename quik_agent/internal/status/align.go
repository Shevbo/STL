package status

// Aligner executes an operator-confirmed recon align Plan (Deps.AlignExec's
// implementation, wired in Task 9). Order-bearing steps are REAL trading
// actions: they go through trade.Manager's FULL placement/cancel path, so the
// Guard limits and the master flag gate them there — the Aligner never
// pre-checks or bypasses those gates; a disarmed agent simply rejects the
// order and that rejection becomes the step's error.

import (
	"fmt"
	"math"

	"shectory/quik_agent/internal/recon"

	quikv1 "shectory/quik_agent/internal/pb"
)

// alignOrderManager is trade.Manager's align surface (narrow interface so the
// Aligner is unit-testable with fakes; *trade.Manager satisfies it implicitly).
type alignOrderManager interface {
	// PlaceOrderErr is the full PlaceOrder path returning the rejection reason.
	PlaceOrderErr(*quikv1.PlaceOrder) error
	// CancelOrphan cancels an order_num the Manager does not track (recon ORPHAN).
	CancelOrphan(orderNum, sec string) error
}

// fixStateSender is runner.Server's align surface.
type fixStateSender interface {
	SendFixState(*quikv1.FixRobotState) error
}

// Aligner holds the wiring for Execute. Field names match the concrete types
// the caller assigns (Manager *trade.Manager, Runner *runner.Server, Provider
// *quikdde.Provider) — Go structural typing makes those assignments compile
// unchanged, exactly like Deps' fields.
type Aligner struct {
	Manager  alignOrderManager
	Runner   fixStateSender
	Provider tickProvider
	NowMs    func() int64
}

// Execute runs the plan's steps SEQUENTIALLY. The first step error stops the
// sequence: the failing step carries its error and every remaining step is
// reported skipped (never executed) — a half-applied plan must be re-evaluated
// from a fresh recon picture, not pushed through.
func (a *Aligner) Execute(plan recon.Plan) []StepResult {
	results := make([]StepResult, 0, len(plan.Steps))
	failed := false
	for _, step := range plan.Steps {
		if failed {
			results = append(results, StepResultFrom(step, false, "skipped: previous step failed"))
			continue
		}
		if err := a.executeStep(plan.ID, step); err != nil {
			failed = true
			results = append(results, StepResultFrom(step, false, err.Error()))
			continue
		}
		results = append(results, StepResultFrom(step, true, ""))
	}
	return results
}

func (a *Aligner) executeStep(planID string, s recon.Step) error {
	switch s.Kind {
	case "cancel_order":
		return a.Manager.CancelOrphan(s.OrderNum, s.Symbol)
	case "close_position":
		return a.closePosition(planID, s)
	case "fix_state":
		// A fix_state step exists because the robot believes in a working order
		// QUIK does not have — clear that phantom belief and pin the robot's
		// book to the plan's SetPos/SetAvg. The Detail doubles as the journal
		// note the runner records.
		return a.Runner.SendFixState(&quikv1.FixRobotState{
			RobotId:      s.RobotID,
			SetPosition:  s.SetPos,
			SetAvgPrice:  s.SetAvg,
			ClearWorking: true,
			Note:         s.Detail,
		})
	default:
		return fmt.Errorf("unknown align step kind %q", s.Kind)
	}
}

// closePosition places ONE limit order for the unexplained excess. SIGN
// CONVENTION (from recon.closePositionStep): Step.Qty POSITIVE = the account
// is LONG in excess => SELL |Qty|; NEGATIVE = the account is SHORT of the
// robots' claim => BUY back |Qty|. Price = the provider's current last for the
// symbol, quantized to the instrument price step (a price that cannot exist on
// the exchange is never sent); missing price or step fails the step.
func (a *Aligner) closePosition(planID string, s recon.Step) error {
	if s.Qty == 0 {
		return fmt.Errorf("close_position %s: zero qty", s.Symbol)
	}
	side, qty := quikv1.Side_SIDE_SELL, s.Qty
	if s.Qty < 0 {
		side, qty = quikv1.Side_SIDE_BUY, -s.Qty
	}

	var last float64
	for _, t := range a.Provider.Ticks() {
		if t.Code == s.Symbol {
			last = t.Last
			break
		}
	}
	if last <= 0 {
		return fmt.Errorf("close_position %s: no current last price", s.Symbol)
	}
	var step float64
	for _, p := range a.Provider.Params() {
		if p.Code == s.Symbol {
			step = p.PriceStep
			break
		}
	}
	if step <= 0 {
		return fmt.Errorf("close_position %s: no price step", s.Symbol)
	}

	return a.Manager.PlaceOrderErr(&quikv1.PlaceOrder{
		ClientId: "recon:" + planID,
		Code:     s.Symbol,
		Side:     side,
		Price:    quantizePrice(last, step, side == quikv1.Side_SIDE_BUY),
		Quantity: qty,
	})
}

// quantizePrice snaps price onto the instrument's price-step grid in the
// FILL-FAVORING direction for the side: a SELL limit rounds DOWN (last=89173,
// step=10 => 89170), a BUY limit rounds UP (=> 89180), so the aligning order
// is at least as marketable as the last trade. A price already on the grid is
// unchanged for both sides (the epsilon absorbs float division noise).
func quantizePrice(price, step float64, buy bool) float64 {
	n := price / step
	if buy {
		n = math.Ceil(n - 1e-9)
	} else {
		n = math.Floor(n + 1e-9)
	}
	return n * step
}
