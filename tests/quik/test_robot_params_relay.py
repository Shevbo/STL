"""STL param relay endpoint (Task 8, robot-tagging + GUI control plan).

POST /api/v1/quik/robots/{id}/params is portal-authed and enqueues a
SetRobotParams OrchestratorMessage on the agent's live gRPC session -- the
same enqueue_order path deploy-agent/undeploy-agent/set-params-agent already
use in trader/api/quik_robots.py. No mode route exists here (invariant #2 is
covered on the Go side; this file only asserts the params route's own shape).
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trader.api.quik_robots import router as quik_robots_router
from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.store import QuikAgentStore


class _FakeServer:
    def __init__(self):
        self.enqueued: list[tuple[str, object]] = []

    def enqueue_order(self, agent_id, message):
        self.enqueued.append((agent_id, message))


class _Settings:
    def __init__(self, bridge_secret: str = ""):
        self.shectory_auth_bridge_secret = bridge_secret


def _client(monkeypatch, *, bridge_secret: str = "", dev_bypass: bool = True,
            server: _FakeServer | None = None, store: QuikAgentStore | None = None) -> TestClient:
    if dev_bypass:
        monkeypatch.setenv("SHECTORY_AUTH_DEV_BYPASS", "1")
    else:
        monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(quik_robots_router)
    app.state.settings = _Settings(bridge_secret)
    app.state.quik_server = server
    app.state.quik_store = store if store is not None else QuikAgentStore()
    return TestClient(app)


def test_params_relay_enqueues_set_robot_params(monkeypatch):
    # params-only edit -> light SetRobotParams (no spec change requested).
    srv = _FakeServer()
    client = _client(monkeypatch, server=srv)

    r = client.post(
        "/api/v1/quik/robots/live-fvg-RIU6/params",
        json={"agent_id": "A1", "params_json": json.dumps({"qty": 2})},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "agent_id": "A1", "robot_id": "live-fvg-RIU6",
                    "redeployed": False}

    assert len(srv.enqueued) == 1
    agent_id, msg = srv.enqueued[0]
    assert agent_id == "A1"
    assert msg.WhichOneof("payload") == "set_robot_params"
    assert msg.set_robot_params.robot_id == "live-fvg-RIU6"
    assert json.loads(msg.set_robot_params.params_json) == {"qty": 2}


def test_params_relay_spec_change_needs_mirror(monkeypatch):
    # Requesting a DIFFERENT max_position with no mirror echo must refuse
    # loudly (409), never silently ignore (the old behaviour bit the operator:
    # qty=3 applied, max_position stayed 1, robot could not open at all).
    srv = _FakeServer()
    client = _client(monkeypatch, server=srv)

    r = client.post(
        "/api/v1/quik/robots/live-fvg-RIU6/params",
        json={"agent_id": "A1", "params_json": "{}", "max_position": 3},
    )
    assert r.status_code == 409
    assert srv.enqueued == []


def test_params_relay_max_position_redeploys_from_mirror(monkeypatch):
    # max_position change with a live mirror echo -> full DeployRobot rebuilt
    # from the mirror (strategy/symbol/PAPER strictly from the mirror: this
    # route must never be able to arm/disarm).
    srv = _FakeServer()
    store = QuikAgentStore()
    store.ensure_agent("A1")
    store.set_robot_report("A1", {"robots": [{
        "robot_id": "r1", "strategy_id": "fvg", "symbol": "RIU6",
        "schedule": "09:00-23:55", "max_position": "1", "paper": True,
    }]})
    client = _client(monkeypatch, server=srv, store=store)

    r = client.post(
        "/api/v1/quik/robots/r1/params",
        json={"agent_id": "A1", "params_json": json.dumps({"qty": 3}),
              "max_position": 3},
    )
    assert r.status_code == 200
    assert r.json()["redeployed"] is True

    assert len(srv.enqueued) == 1
    _, msg = srv.enqueued[0]
    assert msg.WhichOneof("payload") == "deploy_robot"
    spec = msg.deploy_robot.spec
    assert spec.robot_id == "r1"
    assert spec.strategy_id == "fvg"
    assert spec.symbol == "RIU6"
    assert spec.max_position_contracts == 3
    assert spec.paper is True          # from the mirror, NOT the client
    assert json.loads(spec.params_json) == {"qty": 3}


def test_params_relay_same_spec_values_stay_light(monkeypatch):
    # Body-shape parity: sending schedule/max_position EQUAL to the mirror's
    # current values is not a spec change -> light SetRobotParams.
    srv = _FakeServer()
    store = QuikAgentStore()
    store.ensure_agent("A1")
    store.set_robot_report("A1", {"robots": [{
        "robot_id": "r1", "strategy_id": "fvg", "symbol": "RIU6",
        "schedule": "09:00-23:55", "max_position": "3", "paper": False,
    }]})
    client = _client(monkeypatch, server=srv, store=store)

    r = client.post(
        "/api/v1/quik/robots/r1/params",
        json={"agent_id": "A1", "params_json": "{}",
              "schedule": "09:00-23:55", "max_position": 3},
    )
    assert r.status_code == 200
    assert r.json()["redeployed"] is False
    assert srv.enqueued[0][1].WhichOneof("payload") == "set_robot_params"


def test_params_relay_resolves_single_live_agent_when_omitted(monkeypatch):
    srv = _FakeServer()
    store = QuikAgentStore()
    store.ensure_agent("only-agent")
    client = _client(monkeypatch, server=srv, store=store)

    r = client.post(
        "/api/v1/quik/robots/r1/params",
        json={"params_json": "{}"},
    )
    assert r.status_code == 200
    assert r.json()["agent_id"] == "only-agent"
    assert srv.enqueued[0][0] == "only-agent"


def test_params_relay_requires_auth(monkeypatch):
    srv = _FakeServer()
    client = _client(monkeypatch, bridge_secret="s3cr3t", dev_bypass=False, server=srv)

    r = client.post("/api/v1/quik/robots/r1/params", json={"params_json": "{}"})
    assert r.status_code == 401
    assert srv.enqueued == []


def test_params_relay_503_when_no_agent_session(monkeypatch):
    client = _client(monkeypatch, server=None)

    r = client.post("/api/v1/quik/robots/r1/params", json={"params_json": "{}"})
    assert r.status_code == 503


def test_params_relay_only_forwards_params_json_field():
    """The proto's SetRobotParams carries params_json only -- schedule and
    max_position are accepted in the request body for shape parity with the
    agent's local editor but do not ride this wire message."""
    msg = pb.OrchestratorMessage(set_robot_params=pb.SetRobotParams(
        robot_id="r1", params_json="{}"))
    fields = [f.name for f, _ in msg.set_robot_params.ListFields()]
    assert fields == ["robot_id", "params_json"]
    assert not hasattr(pb.SetRobotParams(), "schedule")
    assert not hasattr(pb.SetRobotParams(), "max_position")
