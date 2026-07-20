package link

import (
	"fmt"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

// Robot hosting: STL deploys RobotSpecs to the agent; the link persists them into
// the local store (source of truth for zero-touch resume) and relays each command
// to the runner bridge when a runner is attached. Status flows the other way:
// runner -> bridge -> ForwardRobotStatus -> STL (dropped quietly between sessions —
// the runner never blocks on STL availability).

// RunnerControlSink is the runner-bridge subset the link relays commands into.
// runner.Server satisfies it. Interface keeps link free of the runner package.
// FanOrderEvent injects a fabricated fill through the runner's normal event path
// (same mechanism the journal auto-heal uses) — for operator manual-trade recording.
type RunnerControlSink interface {
	PushControl(*quikv1.RunnerControl)
	FanOrderEvent(*quikv1.OrderUpdate)
}

// SetRobots wires the persisted store + runner bridge AFTER construction (same
// pattern as SetTrade — main builds link, bridge, then connects them).
func (l *Link) SetRobots(store *robots.Store, runner RunnerControlSink) {
	l.opt.Robots = store
	l.opt.Runner = runner
}

// handleRobotMsg persists a robot command and relays it to the runner.
func (l *Link) handleRobotMsg(msg *quikv1.OrchestratorMessage) {
	if l.opt.Robots == nil {
		return
	}
	var rc *quikv1.RunnerControl
	switch p := msg.GetPayload().(type) {
	case *quikv1.OrchestratorMessage_DeployRobot:
		_ = l.opt.Robots.Put(p.DeployRobot.GetSpec())
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Deploy{Deploy: p.DeployRobot}}
	case *quikv1.OrchestratorMessage_UndeployRobot:
		_ = l.opt.Robots.Delete(p.UndeployRobot.GetRobotId())
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Undeploy{Undeploy: p.UndeployRobot}}
	case *quikv1.OrchestratorMessage_SetRobotParams:
		if spec := l.opt.Robots.Get(p.SetRobotParams.GetRobotId()); spec != nil {
			spec.ParamsJson = p.SetRobotParams.GetParamsJson()
			_ = l.opt.Robots.Put(spec) // Put preserves DeployedAtMs once already set
			_ = l.opt.Robots.TouchParams(p.SetRobotParams.GetRobotId())
		}
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_SetParams{SetParams: p.SetRobotParams}}
	case *quikv1.OrchestratorMessage_PauseRobot:
		_ = l.opt.Robots.SetPaused(p.PauseRobot.GetRobotId(), true)
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Pause{Pause: p.PauseRobot}}
	case *quikv1.OrchestratorMessage_StartRobot:
		_ = l.opt.Robots.SetPaused(p.StartRobot.GetRobotId(), false)
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Start{Start: p.StartRobot}}
	case *quikv1.OrchestratorMessage_FlattenRobot:
		// operator market-close + pause; persist paused so it survives a restart
		_ = l.opt.Robots.SetPaused(p.FlattenRobot.GetRobotId(), true)
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Flatten{Flatten: p.FlattenRobot}}
	case *quikv1.OrchestratorMessage_SetRobotPosition:
		// Belief-only correction relayed as a runner fix_state (never a real order).
		// Gate agent-side too (STL gates as well): confirm_id must echo the id AND
		// the robot must be PAUSED — never rewrite a live trading book. Fails silent
		// (fire-and-forget like the other relays); the operator reads back the mirror.
		sp := p.SetRobotPosition
		if sp.GetConfirmId() != sp.GetRobotId() || !l.opt.Robots.Paused(sp.GetRobotId()) {
			return
		}
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_FixState{FixState: &quikv1.FixRobotState{
			RobotId:      sp.GetRobotId(),
			SetPosition:  sp.GetPosition(),
			SetAvgPrice:  sp.GetAvgPrice(),
			ClearWorking: true,
			Note:         "STL: ручная установка позиции (оператор)",
		}}}
	case *quikv1.OrchestratorMessage_RecordRobotFill:
		// Inject an operator manual-trade fill through the runner's real fill path
		// (fabricated OrderUpdate -> on_order_event -> _apply_fill), so P&L is realized
		// and it lands in the fill history — for a position the operator closed by hand
		// (an untagged QUIK trade the auto-heal skips). Gate: confirm_id echoes the id
		// AND the robot is PAUSED. symbol comes from the spec; the client_id is stable
		// per (ts,qty) so a re-send is idempotent (runner dedups fills per client_id).
		rf := p.RecordRobotFill
		spec := l.opt.Robots.Get(rf.GetRobotId())
		if spec == nil || rf.GetConfirmId() != rf.GetRobotId() || rf.GetQty() <= 0 ||
			!l.opt.Robots.Paused(rf.GetRobotId()) {
			return
		}
		side := quikv1.Side_SIDE_BUY
		if rf.GetSide() == "sell" {
			side = quikv1.Side_SIDE_SELL
		}
		if l.opt.Runner != nil {
			l.opt.Runner.FanOrderEvent(&quikv1.OrderUpdate{
				ClientId: fmt.Sprintf("rr:%s:manual:%d:%d", rf.GetRobotId(), rf.GetTsUnixMs(), rf.GetQty()),
				OrderId:  fmt.Sprintf("manual-%d", rf.GetTsUnixMs()),
				Code:     spec.GetSymbol(),
				Side:     side,
				State:    quikv1.OrderState_ORDER_STATE_FILLED,
				Price:    rf.GetPrice(),
				Quantity: rf.GetQty(),
				Filled:   rf.GetQty(),
				Text:     "manual close recorded by operator (STL)",
				TsUnixMs: rf.GetTsUnixMs(),
			})
		}
		return // FanOrderEvent already delivered; nothing to PushControl
	default:
		return
	}
	if l.opt.Runner != nil && rc != nil {
		l.opt.Runner.PushControl(rc)
	}
}

// ForwardRobotStatus sends an agent-hosted robot status report to STL. Satisfies
// runner.StatusSink. Dropped silently when no session is open (isolation).
func (l *Link) ForwardRobotStatus(r *quikv1.RobotStatusReport) {
	stream := l.currentStream()
	if stream == nil {
		return
	}
	_ = l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_RobotStatusReport{RobotStatusReport: r},
	})
}
