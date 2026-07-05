// Package runner hosts the loopback gRPC bridge the bundled Python robot-runner
// connects to: ticks out, orders in (re-checked by the agent Guard downstream),
// STL control relayed, status reports forwarded to STL. 127.0.0.1 only.
package runner

import (
	"context"
	"net"
	"strings"
	"sync"
	"time"

	"google.golang.org/grpc"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

// TickSource supplies the freshest tick per instrument (poll-based fan-out).
type TickSource interface{ Snapshot() []*quikv1.MarketDataTick }

// OrderSink is where runner orders go — satisfied by *trade.Manager, which
// re-checks the hard limits (whitelist/qty/collar/daily cap/master flag).
type OrderSink interface {
	PlaceOrder(*quikv1.PlaceOrder)
	CancelOrder(*quikv1.CancelOrder)
	KillSwitch(*quikv1.KillSwitch)
}

// StatusSink forwards runner status reports to STL — satisfied by *link.Link.
type StatusSink interface{ ForwardRobotStatus(*quikv1.RobotStatusReport) }

type ServerCfg struct {
	Store  *robots.Store
	Ticks  TickSource
	Orders OrderSink
	Status StatusSink
	Logf   func(string, ...any)
}

const (
	tickPollInterval = 250 * time.Millisecond
	healthyWithin    = 90 * time.Second // runner counts healthy if it reported recently
	runnerPrefix     = "rr:"
)

type Server struct {
	cfg ServerCfg

	mu           sync.Mutex
	ctrlCh       chan *quikv1.RunnerControl // nil when no runner control stream
	eventSubs    []chan *quikv1.OrderUpdate
	tapeSubs     []chan *quikv1.TapeBatch
	lastReport   time.Time
	ctrlAttached bool
}

func NewServer(cfg ServerCfg) *Server {
	if cfg.Logf == nil {
		cfg.Logf = func(string, ...any) {}
	}
	return &Server{cfg: cfg}
}

// Serve blocks until ctx is done. addr must be loopback, e.g. "127.0.0.1:50071".
func (s *Server) Serve(ctx context.Context, addr string) error {
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return s.serveListener(ctx, lis)
}

func (s *Server) serveListener(ctx context.Context, lis net.Listener) error {
	gs := grpc.NewServer()
	quikv1.RegisterRunnerBridgeServer(gs, s)
	go func() { <-ctx.Done(); gs.GracefulStop() }()
	s.cfg.Logf("runner bridge listening on %s", lis.Addr())
	return gs.Serve(lis)
}

// ---- runner -> agent ----

func (s *Server) PlaceRunnerOrder(_ context.Context, p *quikv1.PlaceOrder) (*quikv1.BridgeAck, error) {
	if !strings.HasPrefix(p.GetClientId(), runnerPrefix) {
		return &quikv1.BridgeAck{Ok: false, Error: "client_id must be prefixed rr:"}, nil
	}
	s.cfg.Orders.PlaceOrder(p)
	return &quikv1.BridgeAck{Ok: true}, nil
}

func (s *Server) CancelRunnerOrder(_ context.Context, c *quikv1.CancelOrder) (*quikv1.BridgeAck, error) {
	s.cfg.Orders.CancelOrder(c)
	return &quikv1.BridgeAck{Ok: true}, nil
}

func (s *Server) ReportStatus(_ context.Context, r *quikv1.RobotStatusReport) (*quikv1.BridgeAck, error) {
	s.mu.Lock()
	s.lastReport = time.Now()
	s.mu.Unlock()
	r.RunnerHealthy = true
	s.cfg.Status.ForwardRobotStatus(r)
	return &quikv1.BridgeAck{Ok: true}, nil
}

// ---- agent -> runner streams ----

func (s *Server) StreamTicks(f *quikv1.TickFilter, stream quikv1.RunnerBridge_StreamTicksServer) error {
	want := map[string]bool{}
	for _, c := range f.GetCodes() {
		want[c] = true
	}
	t := time.NewTicker(tickPollInterval)
	defer t.Stop()
	sent := map[string]int64{} // code -> last received_at we already pushed
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case <-t.C:
			for _, tick := range s.cfg.Ticks.Snapshot() {
				if len(want) > 0 && !want[tick.GetCode()] {
					continue
				}
				if sent[tick.GetCode()] >= tick.GetReceivedAtUnixMs() {
					continue // unchanged since last push
				}
				sent[tick.GetCode()] = tick.GetReceivedAtUnixMs()
				if err := stream.Send(tick); err != nil {
					return err
				}
			}
		}
	}
}

func (s *Server) StreamOrderEvents(f *quikv1.EventsFilter, stream quikv1.RunnerBridge_StreamOrderEventsServer) error {
	ch := make(chan *quikv1.OrderUpdate, 256)
	s.mu.Lock()
	s.eventSubs = append(s.eventSubs, ch)
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		for i, c := range s.eventSubs {
			if c == ch {
				s.eventSubs = append(s.eventSubs[:i], s.eventSubs[i+1:]...)
				break
			}
		}
		s.mu.Unlock()
	}()
	prefix := f.GetClientPrefix()
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case u := <-ch:
			if prefix != "" && !strings.HasPrefix(u.GetClientId(), prefix) {
				continue
			}
			if err := stream.Send(u); err != nil {
				return err
			}
		}
	}
}

func (s *Server) StreamControl(h *quikv1.ControlHello, stream quikv1.RunnerBridge_StreamControlServer) error {
	ch := make(chan *quikv1.RunnerControl, 64)
	s.mu.Lock()
	s.ctrlCh = ch
	s.ctrlAttached = true
	s.mu.Unlock()
	s.cfg.Logf("runner connected (version=%s pid=%d)", h.GetRunnerVersion(), h.GetPid())
	// Zero-touch resume: replay every persisted spec as a Deploy on (re)connect.
	for _, spec := range s.cfg.Store.All() {
		ch <- &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Deploy{
			Deploy: &quikv1.DeployRobot{Spec: spec}}}
		if s.cfg.Store.Paused(spec.GetRobotId()) {
			ch <- &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Pause{
				Pause: &quikv1.PauseRobot{RobotId: spec.GetRobotId()}}}
		}
	}
	defer func() {
		s.mu.Lock()
		if s.ctrlCh == ch {
			s.ctrlCh = nil
			s.ctrlAttached = false
		}
		s.mu.Unlock()
	}()
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case rc := <-ch:
			if err := stream.Send(rc); err != nil {
				return err
			}
		}
	}
}

func (s *Server) StreamTape(f *quikv1.TickFilter, stream quikv1.RunnerBridge_StreamTapeServer) error {
	want := map[string]bool{}
	for _, c := range f.GetCodes() {
		want[c] = true
	}
	ch := make(chan *quikv1.TapeBatch, 256)
	s.mu.Lock()
	s.tapeSubs = append(s.tapeSubs, ch)
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		for i, c := range s.tapeSubs {
			if c == ch {
				s.tapeSubs = append(s.tapeSubs[:i], s.tapeSubs[i+1:]...)
				break
			}
		}
		s.mu.Unlock()
	}()
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case b := <-ch:
			if len(want) > 0 && !want[b.GetCode()] {
				continue
			}
			if err := stream.Send(b); err != nil {
				return err
			}
		}
	}
}

// FanTape forwards an all-trades batch to runner subscribers.
func (s *Server) FanTape(b *quikv1.TapeBatch) {
	s.mu.Lock()
	subs := append([]chan *quikv1.TapeBatch(nil), s.tapeSubs...)
	s.mu.Unlock()
	for _, ch := range subs {
		select {
		case ch <- b:
		default: // slow consumer: drop the batch, bars re-sync from the next one
		}
	}
}

// ---- agent-side entry points ----

// PushControl relays an STL command to the connected runner (drops when absent —
// commands are optional; persisted state is replayed on the next connect anyway).
func (s *Server) PushControl(rc *quikv1.RunnerControl) {
	s.mu.Lock()
	ch := s.ctrlCh
	s.mu.Unlock()
	if ch == nil {
		return
	}
	select {
	case ch <- rc:
	default:
		s.cfg.Logf("runner control channel full; dropping command")
	}
}

// FanOrderEvent forwards a manager order event to runner subscribers.
func (s *Server) FanOrderEvent(u *quikv1.OrderUpdate) {
	s.mu.Lock()
	subs := append([]chan *quikv1.OrderUpdate(nil), s.eventSubs...)
	s.mu.Unlock()
	for _, ch := range subs {
		select {
		case ch <- u:
		default: // slow consumer: drop rather than block the trade path
		}
	}
}

// RunnerHealthy reports whether the runner has an open control stream AND
// reported status recently. Used by the startup self-check traffic light.
func (s *Server) RunnerHealthy() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ctrlAttached && time.Since(s.lastReport) < healthyWithin
}
