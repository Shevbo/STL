package recon

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
)

// Plan is the deterministic, operator-confirmable align plan computed when a Report is
// MISMATCH. ID is stable across calls with identical Steps + snapshot ages, so a
// confirm request naming an ID can be safely rejected by the wiring layer (Task 7/8) if
// the underlying picture changed underneath it.
type Plan struct {
	ID    string
	Steps []Step
}

// Step is one corrective action in an align Plan.
//   - "cancel_order": an ORPHAN active QUIK order (OrderNum, Symbol set).
//   - "close_position": excess account position that no robot/order claims (Symbol,
//     signed Qty set — positive means SELL the excess, negative means BUY it back).
//   - "fix_state": a robot's belief about a working order that QUIK does not have
//     (RobotID, OrderNum, Symbol set; SetPos/SetAvg carry the robot's CURRENT
//     position/avg to reset to once its phantom working order is cleared).
type Step struct {
	Kind     string
	Detail   string
	Symbol   string
	OrderNum string
	Qty      int64
	RobotID  string
	SetPos   int64
	SetAvg   float64
}

// sortSteps orders steps by (Kind, Symbol, OrderNum, RobotID) so that Report/Plan
// output never depends on the order in which the caller's Inputs slices (or any map
// iterated while building them) were arranged.
func sortSteps(steps []Step) {
	sort.SliceStable(steps, func(i, j int) bool {
		if steps[i].Kind != steps[j].Kind {
			return steps[i].Kind < steps[j].Kind
		}
		if steps[i].Symbol != steps[j].Symbol {
			return steps[i].Symbol < steps[j].Symbol
		}
		if steps[i].OrderNum != steps[j].OrderNum {
			return steps[i].OrderNum < steps[j].OrderNum
		}
		return steps[i].RobotID < steps[j].RobotID
	})
}

// planID hashes the canonical JSON of the (already-sorted) steps plus the snapshot ages
// that gated the evaluation, and returns the first 12 hex chars of the sha256 digest. No
// time.Now/rand: identical Steps + ages always yield the same ID, and any change to
// either changes it.
func planID(steps []Step, posAgeMs, ordAgeMs int64) string {
	payload := struct {
		Steps    []Step
		PosAgeMs int64
		OrdAgeMs int64
	}{Steps: steps, PosAgeMs: posAgeMs, OrdAgeMs: ordAgeMs}
	data, err := json.Marshal(payload)
	if err != nil {
		// payload contains only strings/ints/floats/slices of structs thereof — it
		// cannot fail to marshal. Kept as a defensive fallback, not a live path.
		data = []byte(err.Error())
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])[:12]
}
