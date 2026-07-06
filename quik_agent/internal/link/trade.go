package link

import (
	"crypto/sha256"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/status"
)

// Phase 2: the link implements trade.Emitter so the order manager can push
// OrderUpdate / TransReply / ExecutionUpdate frames to STL over the live session.
// The manager runs across reconnects, so it never holds a stream itself; instead it
// calls these methods, which send over whichever stream is currently published
// (setStream in runOnce). Between sessions the stream is nil and emits drop quietly —
// STL re-reads order state on reconnect. This keeps Phase 1 send paths untouched: the
// same sendMsg + sendMu serialise these alongside the heartbeat/market-data flushes.

// SetTrade wires the order manager AFTER construction. This resolves the cycle: the
// manager needs the link as its trade.Emitter, and the link needs the manager to
// dispatch Phase 2 OrchestratorMessages. main builds the link, then the manager (with
// the link as Emitter), then calls SetTrade. nil keeps the agent read-only.
func (l *Link) SetTrade(t TradeManager) {
	l.opt.Trade = t
}

// setStream publishes (or clears) the live session stream for the Emitter.
func (l *Link) setStream(s quikv1.QuikAgentLink_SessionClient) {
	l.streamMu.Lock()
	l.curStream = s
	l.streamMu.Unlock()
}

func (l *Link) currentStream() quikv1.QuikAgentLink_SessionClient {
	l.streamMu.Lock()
	defer l.streamMu.Unlock()
	return l.curStream
}

// IsUp reports whether a session is currently open (Deps.LinkUp, Task 9's
// status showcase — the agent's own view of link health, independent of
// STL's Ack liveness).
func (l *Link) IsUp() bool {
	return l.currentStream() != nil
}

// EmitOrderUpdate sends an OrderUpdate frame (trade.Emitter).
func (l *Link) EmitOrderUpdate(u *quikv1.OrderUpdate) error {
	stream := l.currentStream()
	if stream == nil {
		return nil
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_OrderUpdate{OrderUpdate: u},
	})
}

// EmitTransReply sends a TransReply frame (trade.Emitter).
func (l *Link) EmitTransReply(r *quikv1.TransReply) error {
	stream := l.currentStream()
	if stream == nil {
		return nil
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_TransReply{TransReply: r},
	})
}

// EmitExecutionUpdate sends an ExecutionUpdate frame (trade.Emitter).
func (l *Link) EmitExecutionUpdate(u *quikv1.ExecutionUpdate) error {
	stream := l.currentStream()
	if stream == nil {
		return nil
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_ExecutionUpdate{ExecutionUpdate: u},
	})
}

// EmitAlert sends an out-of-band Alert frame (trade.Emitter) — used when the trade
// manager detects a silent failure (a placement QUIK never replied to), so STL surfaces
// it (UI + Telegram) instead of the operator discovering it on a stuck order.
func (l *Link) EmitAlert(sev quikv1.AlertSeverity, code, message string) error {
	stream := l.currentStream()
	if stream == nil {
		return nil
	}
	return l.sendAlert(stream, sev, code, message)
}

// EmitStatusSnapshot sends the local status showcase JSON as an
// AgentStatusSnapshot frame — mirrors EmitAlert exactly: drops quietly when no
// session is open (STL keeps whatever it last received; the agent's own
// /api/status is unaffected either way).
func (l *Link) EmitStatusSnapshot(statusJSON []byte, genMs int64) error {
	stream := l.currentStream()
	if stream == nil {
		return nil
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_StatusSnapshot{StatusSnapshot: &quikv1.AgentStatusSnapshot{
			StatusJson:        string(statusJSON),
			GeneratedAtUnixMs: genMs,
		}},
	})
}

// SetStatusDeps wires the status.Deps used to build the periodic
// AgentStatusSnapshot mirror (main.go, AFTER robot hosting is constructed —
// same "build the pieces, wire them in" timing as SetTrade/SetRobots). Safe to
// call before or after Run starts (guarded independently of the sendLoop-only
// hash/timestamp state).
func (l *Link) SetStatusDeps(d status.Deps) {
	l.statusDepsMu.Lock()
	l.statusDeps = d
	l.hasStatusDeps = true
	l.statusDepsMu.Unlock()
}

// maybeSendStatusSnapshot builds the local showcase JSON (status.BuildStatus)
// and emits it to STL only when BOTH: the content materially changed (sha256
// differs from the last successful send) AND at least StatusSnapshotMinSec
// elapsed since that send. Called once per heartbeat tick from sendLoop; a
// build error is swallowed (never breaks the heartbeat cadence over a
// snapshot problem — the agent's own /api/status would show the same error
// surface if this failed structurally).
func (l *Link) maybeSendStatusSnapshot(nowMs int64) error {
	l.statusDepsMu.RLock()
	deps, ok := l.statusDeps, l.hasStatusDeps
	l.statusDepsMu.RUnlock()
	if !ok {
		return nil
	}
	data, err := status.BuildStatus(deps)
	if err != nil {
		return nil
	}
	sum := sha256.Sum256(data)
	if sum == l.lastStatusHash {
		return nil // unchanged since the last send
	}
	minMs := int64(l.opt.StatusSnapshotMinSec) * 1000
	if l.hasSentStatus && nowMs-l.lastStatusSentMs < minMs {
		return nil // changed, but the floor cadence has not elapsed yet
	}
	if err := l.EmitStatusSnapshot(data, nowMs); err != nil {
		return err
	}
	l.lastStatusHash = sum
	l.lastStatusSentMs = nowMs
	l.hasSentStatus = true
	return nil
}
