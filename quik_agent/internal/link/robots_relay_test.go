package link

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

type fakeRunner struct{ got []*quikv1.RunnerControl }

func (f *fakeRunner) PushControl(rc *quikv1.RunnerControl) { f.got = append(f.got, rc) }

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
