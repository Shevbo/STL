"""FastAPI routes for AGENT-HOSTED robots (QUIK-side execution).

STL is the control-plane + read-only monitor: these routes enqueue Deploy/
Undeploy/Pause/Start/Kill commands onto the agent's stream and read back the
last RobotStatusReport mirror. The agent's LOCAL persisted state is the runtime
source of truth — a command is optional and idempotent; the agent trades on
regardless of STL availability. Real orders still require the agent's own
master flag (dual-flag, never pushed from here).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth
from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb

router = APIRouter(prefix="/api/v1/quik", tags=["quik-robots"])


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _server(request: Request):
    srv = getattr(request.app.state, "quik_server", None)
    if srv is None:
        raise HTTPException(status_code=503,
                            detail="QUIK агент не запущен (quik_agent_enabled=false).")
    return srv


def _store(request: Request):
    store = getattr(request.app.state, "quik_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="QUIK store не инициализирован.")
    return store


def _resolve_agent(request: Request, agent_id: str | None) -> str:
    store = _store(request)
    if agent_id:
        return agent_id
    green = [r["agent_id"] for r in store.status() if r.get("link") == "green"]
    if len(green) == 1:
        return green[0]
    ids = store.agent_ids()
    if len(ids) == 1:
        return ids[0]
    raise HTTPException(status_code=400, detail="Укажите agent_id (агентов не ровно один).")


class DeployAgentBody(BaseModel):
    agent_id: str | None = None
    strategy_id: str                  # library id, e.g. "fvg"
    params: dict                      # strategy params incl. symbol/qty
    symbol: str
    schedule: str = "09:00-23:55"
    max_position: int = 1
    paper: bool = False


class AgentIdBody(BaseModel):
    agent_id: str | None = None


@router.post("/robots/{robot_id}/deploy-agent")
async def deploy_agent(robot_id: str, body: DeployAgentBody, request: Request):
    """Hand the robot's working logic to the QUIK-side agent. The agent persists
    the spec locally (auto-resume on reboot) and the bundled runner executes it."""
    _auth(request)
    srv = _server(request)
    agent = _resolve_agent(request, body.agent_id)
    spec = pb.RobotSpec(
        robot_id=robot_id,
        strategy_id=body.strategy_id,
        params_json=json.dumps(body.params, ensure_ascii=False),
        symbol=body.symbol,
        schedule=body.schedule,
        max_position_contracts=max(1, int(body.max_position)),
        paper=bool(body.paper),
    )
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        deploy_robot=pb.DeployRobot(spec=spec)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id, "paper": body.paper}


@router.post("/robots/{robot_id}/undeploy-agent")
async def undeploy_agent(robot_id: str, body: AgentIdBody, request: Request):
    _auth(request)
    srv = _server(request)
    agent = _resolve_agent(request, body.agent_id)
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        undeploy_robot=pb.UndeployRobot(robot_id=robot_id)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id}


@router.post("/robots/{robot_id}/pause-agent")
async def pause_agent(robot_id: str, body: AgentIdBody, request: Request):
    _auth(request)
    srv = _server(request)
    agent = _resolve_agent(request, body.agent_id)
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        pause_robot=pb.PauseRobot(robot_id=robot_id)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id}


@router.post("/robots/{robot_id}/start-agent")
async def start_agent(robot_id: str, body: AgentIdBody, request: Request):
    _auth(request)
    srv = _server(request)
    agent = _resolve_agent(request, body.agent_id)
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        start_robot=pb.StartRobot(robot_id=robot_id)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id}


@router.post("/robots/{robot_id}/set-params-agent")
async def set_params_agent(robot_id: str, body: DeployAgentBody, request: Request):
    """Push new strategy params to an already-deployed agent robot."""
    _auth(request)
    srv = _server(request)
    agent = _resolve_agent(request, body.agent_id)
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        set_robot_params=pb.SetRobotParams(
            robot_id=robot_id,
            params_json=json.dumps(body.params, ensure_ascii=False))))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id}


@router.get("/agent/{agent_id}/robots")
async def agent_robots(agent_id: str, request: Request):
    """Last RobotStatusReport mirror from the agent (position, fills, P&L,
    heartbeat per robot + runner health)."""
    _auth(request)
    store = _store(request)
    report = store.robot_report(agent_id)
    return report or {"robots": [], "received_at_ms": None}


@router.get("/robots-mirror")
async def robots_mirror(request: Request, agent_id: str | None = None):
    """Same mirror without a mandatory agent id: picks the single live agent
    (store._pick semantics) — lets a per-robot showcase URL omit the agent."""
    _auth(request)
    store = _store(request)
    report = store.robot_report(agent_id)
    return report or {"robots": [], "received_at_ms": None}


@router.get("/agent-local-status")
async def agent_local_status(request: Request, agent_id: str | None = None):
    """Mirror of the agent's local-showcase status snapshot, served verbatim.

    Opaque JSON — STL does not interpret its shape, just relays what the agent
    last published (plus when STL received it). agent_id optional: picks the
    single live agent (store._pick semantics), same as robots-mirror."""
    _auth(request)
    store = _store(request)
    status = store.agent_status(agent_id)
    return status or {"status": None, "_received_at_ms": None}
