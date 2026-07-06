package status

// Aligner executes an operator-confirmed recon align Plan (Deps.AlignExec's
// implementation, wired in Task 9). The only order-bearing step is
// "cancel_order": it goes through trade.Manager's FULL cancel path, so the
// master flag / kill-switch semantics gate it there — the Aligner never
// pre-checks or bypasses those gates; a disarmed agent simply rejects the
// cancel and that rejection becomes the step's error. "close_position" is
// NOT order-bearing: recon no longer generates it (an "excess account
// position" is contextual — it can include the operator's own manual
// trading, not just robot activity — so it is surfaced for CONTEXT ONLY).
// The Aligner has no wired capability to place an order at all (see
// alignOrderManager below); a stray/legacy close_position Step.Kind always
// refuses.

import (
	"fmt"

	"shectory/quik_agent/internal/recon"

	quikv1 "shectory/quik_agent/internal/pb"
)

// alignOrderManager is trade.Manager's align surface (narrow interface so the
// Aligner is unit-testable with fakes; *trade.Manager satisfies it implicitly).
// Deliberately NO place-order method: the Aligner must never be ABLE to place
// a real order, not merely refrain from doing so (close_position is inert —
// see the package doc comment above).
type alignOrderManager interface {
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
	for i, step := range plan.Steps {
		if failed {
			results = append(results, StepResultFrom(step, false, "skipped: previous step failed"))
			continue
		}
		if err := a.executeStep(plan.ID, i, step); err != nil {
			failed = true
			results = append(results, StepResultFrom(step, false, err.Error()))
			continue
		}
		results = append(results, StepResultFrom(step, true, ""))
	}
	return results
}

func (a *Aligner) executeStep(planID string, stepIndex int, s recon.Step) error {
	switch s.Kind {
	case "cancel_order":
		return a.Manager.CancelOrphan(s.OrderNum, s.Symbol)
	case "close_position":
		return a.closePosition(s)
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

// closePosition is a HARD REFUSAL: it places no order under any input. recon
// no longer generates "close_position" steps — an "excess account position"
// is contextual (it can include the operator's OWN manual trading, not just
// robot activity), so it is now reported for context only, never
// auto-corrected. This case exists solely so a stray/legacy close_position
// Step.Kind (e.g. a plan persisted from before this change, or a future bug
// that resurrects generation) can never place a real account-flattening
// order; alignOrderManager doesn't even expose a place-order method, so
// reintroducing this by accident would fail to compile, not just fail this
// check.
func (a *Aligner) closePosition(s recon.Step) error {
	return fmt.Errorf("close_position отключён: recon больше не генерирует этот шаг (позиция сверяется справочно)")
}
