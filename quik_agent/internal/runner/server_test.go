package runner

import (
	"context"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

type fakeOrders struct {
	placed []*quikv1.PlaceOrder
	killed int
}

func (f *fakeOrders) PlaceOrder(p *quikv1.PlaceOrder) { f.placed = append(f.placed, p) }
func (f *fakeOrders) CancelOrder(*quikv1.CancelOrder) {}
func (f *fakeOrders) KillSwitch(*quikv1.KillSwitch)   { f.killed++ }

type fakeStatus struct{ got []*quikv1.RobotStatusReport }

func (f *fakeStatus) ForwardRobotStatus(r *quikv1.RobotStatusReport) { f.got = append(f.got, r) }

type fakeTicks struct{}

func (fakeTicks) Snapshot() []*quikv1.MarketDataTick {
	return []*quikv1.MarketDataTick{{Code: "RIU6", Last: 89000, ReceivedAtUnixMs: time.Now().UnixMilli()}}
}

func startTestServer(t *testing.T, seed ...*quikv1.RobotSpec) (*Server, quikv1.RunnerBridgeClient, *fakeOrders, *fakeStatus) {
	t.Helper()
	st, _ := robots.NewStore(t.TempDir())
	for _, sp := range seed {
		_ = st.Put(sp)
	}
	fo, fs := &fakeOrders{}, &fakeStatus{}
	srv := NewServer(ServerCfg{Store: st, Ticks: fakeTicks{}, Orders: fo, Status: fs,
		Logf: func(string, ...any) {}})
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go func() { _ = srv.serveListener(ctx, lis) }()
	conn, err := grpc.NewClient(lis.Addr().String(),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return srv, quikv1.NewRunnerBridgeClient(conn), fo, fs
}

func TestPlaceOrderReachesSinkAndStatusForwards(t *testing.T) {
	_, cli, fo, fs := startTestServer(t)
	ctx := context.Background()

	ack, err := cli.PlaceRunnerOrder(ctx, &quikv1.PlaceOrder{
		ClientId: "rr:live-fvg-RIU6:1", Code: "RIU6", Side: quikv1.Side_SIDE_BUY,
		Price: 89000, Quantity: 1})
	if err != nil || !ack.GetOk() {
		t.Fatalf("place: err=%v ack=%+v", err, ack)
	}
	if len(fo.placed) != 1 || fo.placed[0].GetCode() != "RIU6" {
		t.Fatalf("sink placed = %+v", fo.placed)
	}

	// non-runner client_id must be refused (human orders never come through here)
	ack2, err := cli.PlaceRunnerOrder(ctx, &quikv1.PlaceOrder{ClientId: "human-1", Code: "RIU6"})
	if err != nil || ack2.GetOk() {
		t.Fatalf("non-prefixed order must be refused, ack=%+v", ack2)
	}

	if _, err = cli.ReportStatus(ctx, &quikv1.RobotStatusReport{
		Robots: []*quikv1.RobotStatus{{RobotId: "live-fvg-RIU6", Running: true}}}); err != nil {
		t.Fatal(err)
	}
	if len(fs.got) != 1 || !fs.got[0].GetRunnerHealthy() {
		t.Fatalf("status not forwarded/marked: %+v", fs.got)
	}
}

func TestControlRelayReplayAndOrderEventFan(t *testing.T) {
	seed := &quikv1.RobotSpec{RobotId: "persisted", StrategyId: "fvg", Symbol: "RIU6"}
	srv, cli, _, _ := startTestServer(t, seed)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	ctrl, err := cli.StreamControl(ctx, &quikv1.ControlHello{RunnerVersion: "test"})
	if err != nil {
		t.Fatal(err)
	}
	// Zero-touch: the persisted spec is replayed as the FIRST control message.
	rc, err := ctrl.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if rc.GetDeploy().GetSpec().GetRobotId() != "persisted" {
		t.Fatalf("replay-on-connect got %+v", rc)
	}

	srv.PushControl(&quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Deploy{
		Deploy: &quikv1.DeployRobot{Spec: &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg"}}}})
	rc2, err := ctrl.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if rc2.GetDeploy().GetSpec().GetRobotId() != "r1" {
		t.Fatalf("control relay got %+v", rc2)
	}

	ev, err := cli.StreamOrderEvents(ctx, &quikv1.EventsFilter{ClientPrefix: "rr:"})
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond) // let the subscription register
	srv.FanOrderEvent(&quikv1.OrderUpdate{ClientId: "human-1", Code: "GZU6"}) // filtered out
	srv.FanOrderEvent(&quikv1.OrderUpdate{ClientId: "rr:r1:2", Code: "RIU6",
		State: quikv1.OrderState_ORDER_STATE_FILLED})
	u, err := ev.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if u.GetClientId() != "rr:r1:2" {
		t.Fatalf("event fan leaked/missed: %+v", u)
	}
}

func TestLastStatusesNewestWinsPerRobotAndOmittedRobotKeepsPrevious(t *testing.T) {
	srv, cli, _, _ := startTestServer(t)
	ctx := context.Background()

	if age := srv.LastReportAgeMs(); age != -1 {
		t.Fatalf("no report yet -> LastReportAgeMs must be -1, got %d", age)
	}

	if _, err := cli.ReportStatus(ctx, &quikv1.RobotStatusReport{Robots: []*quikv1.RobotStatus{
		{RobotId: "r1", Running: true, Position: 1},
		{RobotId: "r2", Running: false, Position: 2},
	}}); err != nil {
		t.Fatal(err)
	}

	got := srv.LastStatuses()
	if len(got) != 2 || got["r1"].GetPosition() != 1 || got["r2"].GetPosition() != 2 {
		t.Fatalf("LastStatuses after first report = %+v", got)
	}

	// Mutating the returned map/messages must not affect internals (proto.Clone).
	got["r1"].Position = 999
	delete(got, "r2")

	// Second report updates r1 (newest wins) and omits r2 entirely — r2 must
	// survive from the previous report; staleness is the status page's job
	// via heartbeat, not this server's.
	if _, err := cli.ReportStatus(ctx, &quikv1.RobotStatusReport{Robots: []*quikv1.RobotStatus{
		{RobotId: "r1", Running: true, Position: 5},
	}}); err != nil {
		t.Fatal(err)
	}

	got2 := srv.LastStatuses()
	if len(got2) != 2 {
		t.Fatalf("expected r1+r2 retained, got %+v", got2)
	}
	if got2["r1"].GetPosition() != 5 {
		t.Fatalf("newest report must win for r1, got %+v", got2["r1"])
	}
	if got2["r2"].GetPosition() != 2 {
		t.Fatalf("omitted robot r2 must keep its previous status, got %+v", got2["r2"])
	}

	if age := srv.LastReportAgeMs(); age < 0 {
		t.Fatalf("after a report, LastReportAgeMs must be >= 0, got %d", age)
	}
}

func TestSendFixStateRelaysToControlStreamAndFailsWhenDetached(t *testing.T) {
	srv, cli, _, _ := startTestServer(t)

	// No runner attached: an align step must see the failure, never a silent drop.
	if err := srv.SendFixState(&quikv1.FixRobotState{RobotId: "r1"}); err == nil {
		t.Fatal("SendFixState without a runner must return an error")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ctrl, err := cli.StreamControl(ctx, &quikv1.ControlHello{RunnerVersion: "test"})
	if err != nil {
		t.Fatal(err)
	}
	// Wait until the server registered the control channel.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		srv.mu.Lock()
		attached := srv.ctrlAttached
		srv.mu.Unlock()
		if attached {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	fix := &quikv1.FixRobotState{RobotId: "r1", SetPosition: 2, SetAvgPrice: 89000,
		ClearWorking: true, Note: "recon"}
	if err := srv.SendFixState(fix); err != nil {
		t.Fatalf("SendFixState with attached runner: %v", err)
	}
	rc, err := ctrl.Recv()
	if err != nil {
		t.Fatal(err)
	}
	got := rc.GetFixState()
	if got.GetRobotId() != "r1" || got.GetSetPosition() != 2 ||
		got.GetSetAvgPrice() != 89000 || !got.GetClearWorking() || got.GetNote() != "recon" {
		t.Fatalf("fix_state relay = %+v", rc)
	}
}

func TestSendSetParamsRelaysToControlStreamAndFailsWhenDetached(t *testing.T) {
	srv, cli, _, _ := startTestServer(t)

	// No runner attached: a GUI param edit must see the failure, never a silent drop.
	if err := srv.SendSetParams("r1", `{"qty":2}`); err == nil {
		t.Fatal("SendSetParams without a runner must return an error")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ctrl, err := cli.StreamControl(ctx, &quikv1.ControlHello{RunnerVersion: "test"})
	if err != nil {
		t.Fatal(err)
	}
	// Wait until the server registered the control channel.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		srv.mu.Lock()
		attached := srv.ctrlAttached
		srv.mu.Unlock()
		if attached {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	if err := srv.SendSetParams("r1", `{"qty":2}`); err != nil {
		t.Fatalf("SendSetParams with attached runner: %v", err)
	}
	rc, err := ctrl.Recv()
	if err != nil {
		t.Fatal(err)
	}
	got := rc.GetSetParams()
	if got.GetRobotId() != "r1" || got.GetParamsJson() != `{"qty":2}` {
		t.Fatalf("set_params relay = %+v", rc)
	}
}

func TestSendDeployRelaysToControlStreamAndFailsWhenDetached(t *testing.T) {
	srv, cli, _, _ := startTestServer(t)

	// No runner attached: a GUI mode flip must see the failure, never a silent drop.
	if err := srv.SendDeploy(&quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg"}); err == nil {
		t.Fatal("SendDeploy without a runner must return an error")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ctrl, err := cli.StreamControl(ctx, &quikv1.ControlHello{RunnerVersion: "test"})
	if err != nil {
		t.Fatal(err)
	}
	// Wait until the server registered the control channel.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		srv.mu.Lock()
		attached := srv.ctrlAttached
		srv.mu.Unlock()
		if attached {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	spec := &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg", Symbol: "RIU6"}
	if err := srv.SendDeploy(spec); err != nil {
		t.Fatalf("SendDeploy with attached runner: %v", err)
	}
	rc, err := ctrl.Recv()
	if err != nil {
		t.Fatal(err)
	}
	got := rc.GetDeploy().GetSpec()
	if got.GetRobotId() != "r1" || got.GetStrategyId() != "fvg" || got.GetSymbol() != "RIU6" {
		t.Fatalf("deploy relay = %+v", rc)
	}
}

func TestRunnerHealthyLifecycle(t *testing.T) {
	srv, cli, _, _ := startTestServer(t)
	if srv.RunnerHealthy() {
		t.Fatal("no runner attached -> not healthy")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if _, err := cli.StreamControl(ctx, &quikv1.ControlHello{}); err != nil {
		t.Fatal(err)
	}
	if _, err := cli.ReportStatus(ctx, &quikv1.RobotStatusReport{}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for !srv.RunnerHealthy() && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if !srv.RunnerHealthy() {
		t.Fatal("attached + reported -> must be healthy")
	}
}
