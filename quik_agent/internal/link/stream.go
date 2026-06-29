package link

import (
	"context"
	"fmt"
	"io"
	"time"

	"shectory/quik_agent/internal/health"
	"shectory/quik_agent/internal/notify"
	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

// sendLoop drives Heartbeat (HeartbeatInterval) and market-data flushes
// (PollInterval). It also sends one full SecuritiesSnapshot + ParamsSnapshot at
// startup so STL has reference data immediately.
func (l *Link) sendLoop(ctx context.Context, stream quikv1.QuikAgentLink_SessionClient) error {
	// Initial reference data.
	if err := l.flushSecurities(stream, true); err != nil {
		return err
	}
	if err := l.flushParams(stream); err != nil {
		return err
	}

	hb := time.NewTicker(l.opt.HeartbeatInterval)
	defer hb.Stop()
	poll := time.NewTicker(l.opt.PollInterval)
	defer poll.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-hb.C:
			if err := l.sendHeartbeat(stream); err != nil {
				return err
			}
			// Evaluate health once, raise transition alerts, then send diagnostics
			// from the same snapshot so they stay consistent.
			snap := l.evalAndAlert(stream)
			if err := l.sendDiagnosticsSnapshot(stream, snap); err != nil {
				return err
			}
		case <-poll.C:
			if err := l.flushMarketData(stream); err != nil {
				return err
			}
			// Reference data (securities + params) is populated by DDE AFTER the
			// initial startup flush, so re-send it every poll; otherwise the first
			// (empty) snapshot would stick and /securities + /params stay empty.
			if err := l.flushSecurities(stream, true); err != nil {
				return err
			}
			if err := l.flushParams(stream); err != nil {
				return err
			}
			// Additive generic passthrough: push every QUIK DDE sheet as a
			// RawTable so any table the user exports shows up in STL.
			if err := l.flushRawTables(stream); err != nil {
				return err
			}
		}
	}
}

// recvLoop reads Ack and Command frames from STL. cancel tears down the session
// when the stream closes.
func (l *Link) recvLoop(stream quikv1.QuikAgentLink_SessionClient, cancel context.CancelFunc) error {
	defer cancel()
	for {
		msg, err := stream.Recv()
		if err == io.EOF {
			return fmt.Errorf("stream closed by STL")
		}
		if err != nil {
			return err
		}
		switch p := msg.GetPayload().(type) {
		case *quikv1.OrchestratorMessage_Ack:
			// Liveness only; nothing to do with the acked seq for now.
			_ = p.Ack.GetAckSeq()
		case *quikv1.OrchestratorMessage_Command:
			l.handleCommand(stream, p.Command)
		}
	}
}

func (l *Link) handleCommand(stream quikv1.QuikAgentLink_SessionClient, cmd *quikv1.Command) {
	if cmd == nil {
		return
	}
	switch cmd.GetType() {
	case quikv1.CommandType_COMMAND_TYPE_SUBSCRIBE:
		if code := cmd.GetArgs()["code"]; code != "" {
			l.mu.Lock()
			l.subs[code] = struct{}{}
			l.mu.Unlock()
		}
	case quikv1.CommandType_COMMAND_TYPE_UNSUBSCRIBE:
		if code := cmd.GetArgs()["code"]; code != "" {
			l.mu.Lock()
			delete(l.subs, code)
			l.mu.Unlock()
		}
	case quikv1.CommandType_COMMAND_TYPE_REQUEST_DIAGNOSTICS:
		_ = l.sendDiagnostics(stream)
	case quikv1.CommandType_COMMAND_TYPE_REQUEST_SECURITIES:
		_ = l.flushSecurities(stream, true)
	case quikv1.CommandType_COMMAND_TYPE_SELF_UPDATE:
		if l.opt.OnSelfUpdate != nil {
			if staged, err := l.opt.OnSelfUpdate(); err != nil {
				_ = l.sendAlert(stream, quikv1.AlertSeverity_ALERT_SEVERITY_WARN, "SELF_UPDATE_FAILED", err.Error())
			} else if staged {
				_ = l.sendAlert(stream, quikv1.AlertSeverity_ALERT_SEVERITY_INFO, "SELF_UPDATE_STAGED", "restarting into new build")
			}
		}
	case quikv1.CommandType_COMMAND_TYPE_RESTART:
		if l.opt.OnRestart != nil {
			l.opt.OnRestart()
		}
	}
}

func (l *Link) sendHeartbeat(stream quikv1.QuikAgentLink_SessionClient) error {
	quikAlive := true
	if l.opt.QuikAlive != nil {
		quikAlive = l.opt.QuikAlive()
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_Heartbeat{
			Heartbeat: &quikv1.Heartbeat{
				SentAtUnixMs: time.Now().UnixMilli(),
				DdeAlive:     quikdde.Alive(),
				QuikAlive:    quikAlive,
				LastTickAgeMs: l.opt.Provider.FreshnessMs(),
			},
		},
	})
}

// evalAndAlert classifies channel health via the health package, raises an Alert
// for every state transition (with the right severity / machine code), and returns
// the snapshot so the caller can send a matching Diagnostics frame. Inside an open
// session the link channel is UP; the link-DOWN case is covered by the local
// notifier in main when the session cannot be established at all.
func (l *Link) evalAndAlert(stream quikv1.QuikAgentLink_SessionClient) health.Snapshot {
	in := l.healthInputs(true)
	snap := health.Evaluate(in, l.opt.Thresholds)
	for _, a := range l.hmon.Step(snap) {
		// Primary path: the alert goes to STL over the live stream.
		_ = l.sendAlert(stream, a.Severity, a.Code, a.Message)
	}
	return snap
}

// sendDiagnosticsSnapshot sends a Diagnostics frame built from an already-computed
// snapshot (so heartbeat-time evaluation and diagnostics stay consistent).
func (l *Link) sendDiagnosticsSnapshot(stream quikv1.QuikAgentLink_SessionClient, snap health.Snapshot) error {
	in := l.healthInputs(true)
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_Diagnostics{
			Diagnostics: health.Diagnostics(snap, in),
		},
	})
}

// sendDiagnostics computes a fresh snapshot and sends it (used by the on-demand
// REQUEST_DIAGNOSTICS command). It does not raise transition alerts; only the
// heartbeat path does, to avoid double-firing on a polled request.
func (l *Link) sendDiagnostics(stream quikv1.QuikAgentLink_SessionClient) error {
	in := l.healthInputs(true)
	snap := health.Evaluate(in, l.opt.Thresholds)
	return l.sendDiagnosticsSnapshot(stream, snap)
}

// notifyLocal sends an alert over the LOCAL out-of-band fallback (used when the
// gRPC link itself is down). It is best-effort and never blocks the agent.
func (l *Link) notifyLocal(sev notify.Severity, code, message string, smsDub bool) {
	if l.opt.Notifier == nil || !l.opt.Notifier.Enabled() {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	_ = l.opt.Notifier.Send(ctx, sev, code, message, smsDub)
}

func (l *Link) sendAlert(stream quikv1.QuikAgentLink_SessionClient, sev quikv1.AlertSeverity, code, message string) error {
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_Alert{
			Alert: &quikv1.Alert{
				Severity:       sev,
				Code:           code,
				Message:        message,
				RaisedAtUnixMs: time.Now().UnixMilli(),
			},
		},
	})
}
