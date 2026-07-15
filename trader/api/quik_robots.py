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
from trader.quik.store import resolve_agent

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
    return resolve_agent(_store(request), agent_id)


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


@router.post("/robots/{robot_id}/flatten-agent")
async def flatten_agent(robot_id: str, body: AgentIdBody, request: Request):
    """Operator: market-close the robot's whole open position and pause it. The
    runner cancels its working orders, sends ONE marketable close order (rr:-tagged,
    so the fill zeroes its own book), and stays paused until start-agent. Real money
    — the UI gates it behind a typed confirm."""
    _auth(request)
    srv = _server(request)
    agent = _resolve_agent(request, body.agent_id)
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        flatten_robot=pb.FlattenRobot(robot_id=robot_id)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id, "flattened": True}


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


class ParamsRelayBody(BaseModel):
    agent_id: str | None = None
    params_json: str
    schedule: str | None = None
    max_position: int | None = None


@router.post("/robots/{robot_id}/params")
async def relay_robot_params(robot_id: str, body: ParamsRelayBody, request: Request):
    """Relay a params edit from the STL mirror's param editor to the agent's
    live session (portal-authed). Mirrors deploy-agent's enqueue pattern.

    params_json alone -> a light SetRobotParams (applies next bar). When
    max_position or schedule is ALSO given (and differs from the mirror), the
    edit needs the SPEC, so this relays a full DeployRobot re-deploy built
    from the robot's own mirror echo (strategy/symbol/PAPER come from the
    mirror VERBATIM -- paper is never client-settable here: arming stays on
    the agent's local console). Re-deploy is zero-loss: the runner keeps
    bars/position/P&L for a known robot_id.
    """
    _auth(request)
    srv = _server(request)
    store = _store(request)
    agent = _resolve_agent(request, body.agent_id)

    # Current spec echo from the mirror (source of truth for what runs now).
    report = store.robot_report(body.agent_id) or {}
    cur = next((r for r in report.get("robots", [])
                if r.get("robot_id") == robot_id), None)
    cur_maxpos = int(cur.get("max_position", 0) or 0) if cur else None
    cur_sched = (cur or {}).get("schedule") or ""
    want_maxpos = body.max_position if (body.max_position or 0) > 0 else None
    want_sched = body.schedule or None
    spec_change = ((want_maxpos is not None and want_maxpos != cur_maxpos)
                   or (want_sched is not None and want_sched != cur_sched))

    if spec_change:
        if cur is None:
            raise HTTPException(status_code=409, detail=(
                "Робот не найден в зеркале агента — spec-поля (max_position/"
                "расписание) менять нельзя вслепую. Обнови страницу / проверь линк."))
        spec = pb.RobotSpec(
            robot_id=robot_id,
            strategy_id=cur.get("strategy_id") or "",
            params_json=body.params_json,
            symbol=cur.get("symbol") or "",
            schedule=want_sched or cur_sched,
            max_position_contracts=max(1, int(want_maxpos or cur_maxpos or 1)),
            # paper strictly from the mirror: this route must never arm/disarm.
            paper=bool(cur.get("paper", False)),
        )
        srv.enqueue_order(agent, pb.OrchestratorMessage(
            deploy_robot=pb.DeployRobot(spec=spec)))
        return {"ok": True, "agent_id": agent, "robot_id": robot_id,
                "redeployed": True, "max_position": int(spec.max_position_contracts)}

    srv.enqueue_order(agent, pb.OrchestratorMessage(
        set_robot_params=pb.SetRobotParams(
            robot_id=robot_id,
            params_json=body.params_json)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id, "redeployed": False}


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
