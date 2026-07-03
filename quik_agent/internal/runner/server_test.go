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
