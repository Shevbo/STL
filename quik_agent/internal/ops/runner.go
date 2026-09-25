package ops

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// Handler performs one catalogued operation. Registered per id at wiring time, so
// the catalog (what may run) stays separate from the implementation (how it runs).
type Handler func(ctx context.Context, args map[string]string) (string, error)

// Deps are the facts the runner needs from the rest of the agent to enforce its own
// rules. Kept as functions, not values: both change while the agent runs.
type Deps struct {
	// RealRobotsPaused reports whether every REAL robot is paused. Guards the
	// high-risk operations: a QUIK restart makes the tape replay the day, and
	// robots on such a replay have already piled real orders (13.07.2026).
	RealRobotsPaused func() bool
	// KillSwitch reports the agent-wide stop. While it is on, nothing that changes
	// state runs, confirmed or not.
	KillSwitch func() bool
	// Now is injectable for tests.
	Now func() int64
}

// Runner executes operations under the catalog's rules.
type Runner struct {
	mu        sync.Mutex
	handlers  map[string]Handler
	confirms  *Confirmations
	deps      Deps
	// Journal receives every attempt, allowed or refused, BEFORE and AFTER it runs:
	// an operation that hung must be visible as unfinished, not absent.
	Journal func(entry string)
}

func NewRunner(deps Deps, confirms *Confirmations) *Runner {
	if deps.Now == nil {
		deps.Now = func() int64 { return time.Now().UnixMilli() }
	}
	return &Runner{handlers: map[string]Handler{}, confirms: confirms, deps: deps}
}

// Register wires an implementation to a catalogued id. Registering an id that is
// not in the catalog is a programming error and panics at startup rather than
// quietly widening what the agent can do.
func (r *Runner) Register(id string, h Handler) {
	if _, err := Lookup(id); err != nil {
		panic("ops: " + err.Error())
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.handlers[id] = h
}

func (r *Runner) log(format string, a ...any) {
	if r.Journal != nil {
		r.Journal(fmt.Sprintf(format, a...))
	}
}

// Run validates and performs one operation. The order of checks is deliberate:
// catalog, then arguments, then kill-switch, then confirmation, then the paused-robot
// gate — each refusal names its own reason, so an operator reading the journal during
// an incident learns what to fix.
func (r *Runner) Run(ctx context.Context, op string, args map[string]string,
	confirmID string) (string, error) {
	spec, err := Lookup(op)
	if err != nil {
		r.log("ОТКАЗ %s: %v", op, err)
		return "", err
	}
	if err := spec.CheckArgs(args); err != nil {
		r.log("ОТКАЗ %s: %v", op, err)
		return "", err
	}
	if spec.NeedsConfirm() {
		if r.deps.KillSwitch != nil && r.deps.KillSwitch() {
			err := fmt.Errorf("взведён стоп агента: изменяющие операции запрещены")
			r.log("ОТКАЗ %s: %v", op, err)
			return "", err
		}
		if err := r.confirms.Use(confirmID, op, args, r.deps.Now()); err != nil {
			r.log("ОТКАЗ %s: %v", op, err)
			return "", err
		}
	}
	if spec.RequiresPausedRobots {
		if r.deps.RealRobotsPaused == nil || !r.deps.RealRobotsPaused() {
			err := fmt.Errorf("сначала поставьте реальных роботов на паузу: " +
				"%s заставит ленту переиграть день", op)
			r.log("ОТКАЗ %s: %v", op, err)
			return "", err
		}
	}
	r.mu.Lock()
	h, ok := r.handlers[op]
	r.mu.Unlock()
	if !ok {
		err := fmt.Errorf("операция %s есть в каталоге, но не реализована в этой сборке", op)
		r.log("ОТКАЗ %s: %v", op, err)
		return "", err
	}

	r.log("СТАРТ %s args=%v", op, args)
	out, err := h(ctx, args)
	if err != nil {
		r.log("ОШИБКА %s: %v", op, err)
		return out, err
	}
	r.log("ГОТОВО %s", op)
	return out, nil
}
