package link

import (
	quikv1 "shectory/quik_agent/internal/pb"
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
