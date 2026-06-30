// Package link is the gRPC client that dials OUT to STL and runs the single
// long-lived bidi stream QuikAgentLink.Session.
//
// The agent sits behind NAT with no inbound port, so it always initiates the
// connection. It sends Register first, then Heartbeat / SecuritiesSnapshot /
// MarketDataTick / OrderBook / ParamsSnapshot / Diagnostics / Alert frames, and
// receives Ack + Command. Auth is a Bearer token in gRPC metadata
// "authorization: Bearer <token>", read from the env var named in the config
// (never hardcoded). Phase 1 is READ-ONLY: no order transactions are sent.
package link

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"

	"shectory/quik_agent/internal/health"
	"shectory/quik_agent/internal/notify"
	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

// Options configures the link.
type Options struct {
	// Target is the STL gRPC endpoint, e.g. "stl.example.com:8443".
	Target string
	// Insecure dials without TLS (dev/LAN only).
	Insecure bool
	// Token is the Bearer value (read by the caller from the configured env var).
	Token string
	// AgentVersion / BuildRev identify this build to STL.
	AgentVersion string
	BuildRev     uint32
	// HostName is the Windows host name.
	HostName string
	// QuikDataRoot is reported in Register.
	QuikDataRoot string
	// HeartbeatInterval is the Heartbeat cadence.
	HeartbeatInterval time.Duration
	// PollInterval is the market-data flush cadence.
	PollInterval time.Duration
	// Provider supplies read-only market data snapshots.
	Provider *quikdde.Provider
	// QuikAlive reports whether QUIK itself looks reachable (DDE has ticked recently).
	QuikAlive func() bool
	// OnSelfUpdate is invoked on COMMAND_TYPE_SELF_UPDATE. Return true if a restart
	// was staged (the agent will exit).
	OnSelfUpdate func() (bool, error)
	// OnRestart is invoked on COMMAND_TYPE_RESTART.
	OnRestart func()

	// ---- resilience / diagnostics (sub-agent D, additive) ----

	// Thresholds parameterise the health state machine (stale/down tick ages).
	// Zero fields fall back to health.Defaults().
	Thresholds health.Thresholds
	// Reconnects, if set, supplies the DDE-restart count from the watchdog so it
	// appears in Diagnostics.reconnects_since_start. When nil, the link's own
	// session-reconnect counter is used.
	Reconnects func() uint32
	// HaveTicked reports whether at least one DDE tick has arrived (so a zero
	// freshness at startup does not read as "fresh"). When nil it is derived from
	// the provider's freshness.
	HaveTicked func() bool
	// Notifier is the LOCAL out-of-band alert fallback, used only when the gRPC
	// link is down (so a link outage can still be signalled). When nil it is a
	// no-op (notify.Nop()).
	Notifier notify.Notifier

	// ---- Phase 2: order / execution (sub-agent A, additive) ----

	// Trade is the order manager. When nil (Phase 1 build), every Phase 2
	// OrchestratorMessage (place/cancel/kill/start/stop) is dropped, so the agent
	// stays strictly read-only. When set, the manager enforces the hard limits +
	// master flag before anything reaches QUIK. The link feeds itself as the Emitter
	// to the manager so OrderUpdate/TransReply/ExecutionUpdate ride the live stream.
	Trade TradeManager
}

// TradeManager is the subset of the order manager the link drives. internal/trade
// *Manager satisfies it. Kept as an interface so the link does not import the trade
// package's concrete type and Phase 1 builds without it.
type TradeManager interface {
	PlaceOrder(*quikv1.PlaceOrder)
	CancelOrder(*quikv1.CancelOrder)
	ReplaceOrder(*quikv1.ReplaceOrder)
	KillSwitch(*quikv1.KillSwitch)
	StartExecution(*quikv1.StartExecution)
	StopExecution(*quikv1.StopExecution)
	// ApplyLimits adopts a SetLimits pushed by STL (whitelist + caps; master flag stays
	// dual). EffectiveLimits returns the agent's current limits for the agent->STL echo.
	ApplyLimits(*quikv1.SetLimits)
	EffectiveLimits() *quikv1.LimitsState
}

// Link owns the connection lifecycle and reconnect loop.
type Link struct {
	opt Options

	seq atomic.Uint64

	mu       sync.RWMutex
	subs     map[string]struct{} // codes the orchestrator subscribed to
	startedAt time.Time
	reconnects uint32

	// rawSent tracks the last-sent lastMutationMs per sheet name so unchanged
	// RawTables are not re-sent. Accessed only from the sendLoop goroutine (one
	// per session), so no extra locking is needed. Reset per session in runOnce.
	rawSent map[string]int64

	// hmon tracks channel-state transitions to raise Alerts. Accessed only from
	// the sendLoop goroutine (one per session), so no extra locking is needed.
	hmon *health.Monitor

	// curStream is the live session stream for the trade Emitter, which is called
	// from the manager's own goroutines (not the send/recv loops). Guarded by
	// streamMu. nil between sessions; Emit* drop quietly when nil.
	streamMu  sync.Mutex
	curStream quikv1.QuikAgentLink_SessionClient

	// sendMu serializes stream.Send across the sendLoop and the trade Emitter
	// goroutines (a gRPC client stream allows only one concurrent Send).
	sendMu sync.Mutex
}

// New builds a Link. The token is taken from opt.Token (the caller reads it from
// the configured env var); it is never logged.
func New(opt Options) *Link {
	if opt.HeartbeatInterval <= 0 {
		opt.HeartbeatInterval = 15 * time.Second
	}
	if opt.PollInterval <= 0 {
		opt.PollInterval = 5 * time.Second
	}
	if opt.Provider == nil {
		opt.Provider = quikdde.Default
	}
	if opt.Notifier == nil {
		opt.Notifier = notify.Nop()
	}
	if opt.HaveTicked == nil {
		prov := opt.Provider
		opt.HaveTicked = func() bool { return prov.FreshnessMs() > 0 || prov.LastMutationMs() > 0 }
	}
	return &Link{
		opt:       opt,
		subs:      map[string]struct{}{},
		startedAt: time.Now(),
		hmon:      health.NewMonitor(),
	}
}

// reconnectCount returns the reconnect counter surfaced in Diagnostics: the
// watchdog's DDE-restart count when wired, else the link's own session reconnects.
func (l *Link) reconnectCount() uint32 {
	if l.opt.Reconnects != nil {
		return l.opt.Reconnects()
	}
	return atomic.LoadUint32(&l.reconnects)
}

// healthInputs samples the current health signals for the state machine. LinkState
// is passed in because, from inside an open session, the link is UP; the down case
// is handled by the local notifier in main.
func (l *Link) healthInputs(linkConnected bool) health.Inputs {
	quikAlive := true
	if l.opt.QuikAlive != nil {
		quikAlive = l.opt.QuikAlive()
	}
	return health.Inputs{
		DDEServerAlive:       quikdde.Alive(),
		HaveTicked:           l.opt.HaveTicked(),
		LastTickAgeMs:        l.opt.Provider.FreshnessMs(),
		QuikAlive:            quikAlive,
		LinkConnected:        linkConnected,
		ReconnectsSinceStart: l.reconnectCount(),
		UptimeSec:            int64(time.Since(l.startedAt).Seconds()),
	}
}

func (l *Link) nextSeq() uint64 { return l.seq.Add(1) }

// bearerCreds attaches "authorization: Bearer <token>" to every RPC on the stream.
type bearerCreds struct {
	token      string
	requireTLS bool
}

func (b bearerCreds) GetRequestMetadata(ctx context.Context, uri ...string) (map[string]string, error) {
	if b.token == "" {
		return nil, fmt.Errorf("link: empty bearer token")
	}
	return map[string]string{"authorization": "Bearer " + b.token}, nil
}

func (b bearerCreds) RequireTransportSecurity() bool { return b.requireTLS }

// Run dials and maintains the session until ctx is cancelled. It reconnects with
// backoff on any error. Returns ctx.Err() when the context is done.
//
// When the gRPC link itself is down (no live session to carry an Alert), a
// LINK_DOWN alert is sent over the LOCAL out-of-band notifier so a link outage can
// still be signalled; a LINK_RECOVERED INFO follows once a session re-establishes.
func (l *Link) Run(ctx context.Context) error {
	backoff := time.Second
	const maxBackoff = 30 * time.Second
	linkDownSignalled := false
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		// A new session is about to start; if we previously signalled the link as
		// down over the local fallback, announce recovery now.
		if linkDownSignalled {
			l.notifyLocal(notify.SeverityInfo, health.CodeLinkRecovered, "gRPC link re-established", false)
			linkDownSignalled = false
		}
		err := l.runOnce(ctx)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err != nil {
			fmt.Println("link: session ended:", err)
		}
		atomic.AddUint32(&l.reconnects, 1)
		// The session ended without ctx cancellation: the link is down. Signal it
		// out-of-band exactly once (CRITICAL, SMS-dubbed in Phase 2).
		if !linkDownSignalled {
			l.notifyLocal(notify.SeverityCritical, health.CodeLinkDown, "gRPC link to STL is down (reconnecting)", true)
			linkDownSignalled = true
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
		if backoff < maxBackoff {
			backoff *= 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
		}
	}
}

func (l *Link) dialOpts() []grpc.DialOption {
	creds := bearerCreds{token: l.opt.Token, requireTLS: !l.opt.Insecure}
	opts := []grpc.DialOption{grpc.WithPerRPCCredentials(creds)}
	if l.opt.Insecure {
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	} else {
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewClientTLSFromCert(nil, "")))
	}
	return opts
}

func (l *Link) runOnce(ctx context.Context) error {
	conn, err := grpc.NewClient(l.opt.Target, l.dialOpts()...)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	client := quikv1.NewQuikAgentLinkClient(conn)
	streamCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	stream, err := client.Session(streamCtx)
	if err != nil {
		return fmt.Errorf("open session: %w", err)
	}

	// First frame: Register.
	if err := l.sendRegister(stream); err != nil {
		return fmt.Errorf("register: %w", err)
	}

	// Fresh per-session change-detection state: a reconnect re-sends every sheet
	// once so STL has a full picture again.
	l.rawSent = map[string]int64{}

	// Publish the live stream for the trade Emitter; clear it when the session ends.
	l.setStream(stream)
	defer l.setStream(nil)

	// Receiver loop runs in its own goroutine; sender loop drives heartbeats and
	// market-data flushes. Either ending tears down the session.
	errCh := make(chan error, 2)
	go func() { errCh <- l.recvLoop(stream, cancel) }()
	go func() { errCh <- l.sendLoop(streamCtx, stream) }()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-errCh:
		return err
	}
}

func (l *Link) sendRegister(stream quikv1.QuikAgentLink_SessionClient) error {
	_, offSec := time.Now().Zone()
	msg := &quikv1.AgentMessage{
		Seq: l.nextSeq(),
		Payload: &quikv1.AgentMessage_Register{
			Register: &quikv1.Register{
				AgentVersion:      l.opt.AgentVersion,
				HostName:          l.opt.HostName,
				QuikDataRoot:      l.opt.QuikDataRoot,
				TimezoneOffsetMin: int32(offSec / 60),
				BuildRev:          l.opt.BuildRev,
			},
		},
	}
	return stream.Send(msg)
}

// sendMsg sends a fully built AgentMessage with the next sequence number. sendMu
// serializes Send across the sendLoop and the trade Emitter goroutines.
func (l *Link) sendMsg(stream quikv1.QuikAgentLink_SessionClient, msg *quikv1.AgentMessage) error {
	l.sendMu.Lock()
	defer l.sendMu.Unlock()
	msg.Seq = l.nextSeq()
	return stream.Send(msg)
}
