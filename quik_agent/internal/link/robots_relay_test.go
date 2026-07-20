package link

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

type fakeRunner struct {
	got   []*quikv1.RunnerControl
	fills []*quikv1.OrderUpdate
}

func (f *fakeRunner) PushControl(rc *quikv1.RunnerControl)   { f.got = append(f.got, rc) }
func (f *fakeRunner) FanOrderEvent(u *quikv1.OrderUpdate)    { f.fills = append(f.fills, u) }

func TestHandleRobotCommandsPersistAndRelay(t *testing.T) {
	st, _ := robots.NewStore(t.TempDir())
	fr := &fakeRunner{}
	l := New(Options{})
	l.SetRobots(st, fr)

	spec := &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg", Symbol: "RIU6"}
	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_DeployRobot{
		DeployRobot: &quikv1.DeployRobot{Spec: spec}}})
	if st.Get("r1") == nil {
		t.Fatal("deploy must persist the spec")
	}
	if len(fr.got) != 1 || fr.got[0].GetDeploy() == nil {
		t.Fatalf("deploy must relay to runner, got %+v", fr.got)
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_PauseRobot{
		PauseRobot: &quikv1.PauseRobot{RobotId: "r1"}}})
	if !st.Paused("r1") {
		t.Fatal("pause must persist")
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_StartRobot{
		StartRobot: &quikv1.StartRobot{RobotId: "r1"}}})
	if st.Paused("r1") {
		t.Fatal("start must clear paused")
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_SetRobotParams{
		SetRobotParams: &quikv1.SetRobotParams{RobotId: "r1", ParamsJson: `{"qty":1}`}}})
	if st.Get("r1").GetParamsJson() != `{"qty":1}` {
		t.Fatal("set_params must update the persisted spec")
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_FlattenRobot{
		FlattenRobot: &quikv1.FlattenRobot{RobotId: "r1"}}})
	if !st.Paused("r1") {
		t.Fatal("flatten must persist paused")
	}
	if fr.got[len(fr.got)-1].GetFlatten() == nil {
		t.Fatal("flatten must relay to runner")
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_UndeployRobot{
		UndeployRobot: &quikv1.UndeployRobot{RobotId: "r1"}}})
	if st.Get("r1") != nil {
		t.Fatal("undeploy must delete the spec")
	}
	if len(fr.got) != 6 {
		t.Fatalf("all 6 commands must relay, got %d", len(fr.got))
	}
}

func TestSetPositionAndRecordFillGatedOnPaused(t *testing.T) {
	st, _ := robots.NewStore(t.TempDir())
	fr := &fakeRunner{}
	l := New(Options{})
	l.SetRobots(st, fr)
	spec := &quikv1.RobotSpec{RobotId: "r1", StrategyId: "macd_cross", Symbol: "RIU6"}
	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_DeployRobot{
		DeployRobot: &quikv1.DeployRobot{Spec: spec}}})

	setPos := func(conf string) {
		l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_SetRobotPosition{
			SetRobotPosition: &quikv1.SetRobotPosition{RobotId: "r1", Position: 0, ConfirmId: conf}}})
	}
	recFill := func(conf string) {
		l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_RecordRobotFill{
			RecordRobotFill: &quikv1.RecordRobotFill{RobotId: "r1", Side: "sell", Qty: 11,
				Price: 80210, TsUnixMs: 123, ConfirmId: conf}}})
	}

	// Not paused -> both are refused (belief/book must never change on a live robot).
	setPos("r1")
	recFill("r1")
	if len(fr.got) != 1 { // only the Deploy
		t.Fatalf("commands on a running robot must be refused, got %d relays", len(fr.got))
	}
	if len(fr.fills) != 0 {
		t.Fatalf("record-fill on a running robot must be refused, got %d fills", len(fr.fills))
	}

	// Pause, then wrong confirm -> still refused.
	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_PauseRobot{
		PauseRobot: &quikv1.PauseRobot{RobotId: "r1"}}})
	setPos("WRONG")
	recFill("WRONG")
	if n := len(fr.got); n != 2 { // Deploy + Pause only
		t.Fatalf("wrong confirm must be refused, got %d relays", n)
	}
	if len(fr.fills) != 0 {
		t.Fatalf("wrong confirm record-fill must be refused, got %d fills", len(fr.fills))
	}

	// Paused + correct confirm -> both apply.
	setPos("r1")
	if last := fr.got[len(fr.got)-1].GetFixState(); last == nil || last.GetSetPosition() != 0 || !last.GetClearWorking() {
		t.Fatal("set-position must relay a fix_state that zeroes position + clears working")
	}
	recFill("r1")
	if len(fr.fills) != 1 {
		t.Fatalf("record-fill must inject one fill, got %d", len(fr.fills))
	}
	f := fr.fills[0]
	if f.GetSide() != quikv1.Side_SIDE_SELL || f.GetFilled() != 11 || f.GetPrice() != 80210 ||
		f.GetCode() != "RIU6" || f.GetTsUnixMs() != 123 || f.GetState() != quikv1.OrderState_ORDER_STATE_FILLED {
		t.Fatalf("record-fill built a wrong OrderUpdate: %+v", f)
	}
}

func TestHandleRobotMsgNilStoreIsNoop(t *testing.T) {
	l := New(Options{}) // no SetRobots — robot hosting disabled
	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_DeployRobot{
		DeployRobot: &quikv1.DeployRobot{Spec: &quikv1.RobotSpec{RobotId: "x"}}}})
	// must not panic; nothing to assert beyond survival
}

func TestForwardRobotStatusDropsWithoutSession(t *testing.T) {
	l := New(Options{})
	// no stream published — must drop quietly (isolation), not panic
	l.ForwardRobotStatus(&quikv1.RobotStatusReport{
		Robots: []*quikv1.RobotStatus{{RobotId: "r1"}}})
}
