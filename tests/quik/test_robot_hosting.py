"""Agent-hosted robots: STL-side status mirror + command message building."""

import json

from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.store import QuikAgentStore


def test_robot_report_roundtrip():
    st = QuikAgentStore()
    st.set_robot_report("9618", {"robots": [{"robot_id": "r1", "position": 1}],
                                 "sent_at_unix_ms": 123})
    rep = st.robot_report("9618")
    assert rep["robots"][0]["robot_id"] == "r1"
    assert "received_at_ms" in rep
    assert st.robot_report("nope") is None


def test_robot_report_does_not_leak_between_agents():
    st = QuikAgentStore()
    st.set_robot_report("a1", {"robots": [{"robot_id": "r1"}]})
    st.set_robot_report("a2", {"robots": [{"robot_id": "r2"}]})
    assert st.robot_report("a1")["robots"][0]["robot_id"] == "r1"
    assert st.robot_report("a2")["robots"][0]["robot_id"] == "r2"


def test_deploy_robot_message_shape():
    """The exact OrchestratorMessage the deploy-agent route enqueues."""
    spec = pb.RobotSpec(
        robot_id="live-fvg-RIU6", strategy_id="fvg",
        params_json=json.dumps({"symbol": "RIU6", "qty": 1}),
        symbol="RIU6", schedule="09:00-23:55",
        max_position_contracts=1, paper=False,
    )
    msg = pb.OrchestratorMessage(deploy_robot=pb.DeployRobot(spec=spec))
    assert msg.WhichOneof("payload") == "deploy_robot"
    got = msg.deploy_robot.spec
    assert got.robot_id == "live-fvg-RIU6"
    assert got.max_position_contracts == 1
    assert json.loads(got.params_json)["qty"] == 1


def test_control_messages_roundtrip_serialization():
    for msg, field in [
        (pb.OrchestratorMessage(undeploy_robot=pb.UndeployRobot(robot_id="r1")), "undeploy_robot"),
        (pb.OrchestratorMessage(pause_robot=pb.PauseRobot(robot_id="r1")), "pause_robot"),
        (pb.OrchestratorMessage(start_robot=pb.StartRobot(robot_id="r1")), "start_robot"),
        (pb.OrchestratorMessage(set_robot_params=pb.SetRobotParams(
            robot_id="r1", params_json="{}")), "set_robot_params"),
    ]:
        raw = msg.SerializeToString()
        back = pb.OrchestratorMessage()
        back.ParseFromString(raw)
        assert back.WhichOneof("payload") == field


def test_agent_status_report_wire_roundtrip():
    rep = pb.RobotStatusReport(
        robots=[pb.RobotStatus(robot_id="r1", running=True, position=-1,
                               avg_price=88000.0, realized_pnl=150.0,
                               recent_fills=[pb.RobotFill(order_id="o1", symbol="RIU6",
                                                          side=pb.SIDE_SELL, qty=1,
                                                          price=88000.0, status="filled")])],
        sent_at_unix_ms=1, runner_healthy=True)
    frame = pb.AgentMessage(robot_status_report=rep)
    back = pb.AgentMessage()
    back.ParseFromString(frame.SerializeToString())
    assert back.WhichOneof("payload") == "robot_status_report"
    st = back.robot_status_report.robots[0]
    assert st.position == -1 and st.recent_fills[0].status == "filled"
