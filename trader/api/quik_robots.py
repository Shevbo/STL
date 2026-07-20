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


_NAME_PREFIX = "robotname:"


async def _name_overrides(request: Request) -> dict:
    """robot_id → operator display name, from agent_control('robotname:<id>'). Agent
    robots have no name field (robot_id is the key), so STL keeps a display-name
    overlay so a cuid-id robot can be shown with a friendly name."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return {}
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM agent_control WHERE key LIKE $1", _NAME_PREFIX + "%")
        return {r["key"][len(_NAME_PREFIX):]: r["value"] for r in rows}
    except Exception:
        return {}


def _overlay_names(report: dict | None, names: dict) -> dict | None:
    if report and names:
        for rob in report.get("robots", []):
            nm = names.get(rob.get("robot_id"))
            if nm:
                rob["display_name"] = nm
    return report


class RenameBody(BaseModel):
    name: str = ""


class SetPositionBody(BaseModel):
    agent_id: str | None = None
    position: int
    avg_price: float = 0.0
    confirm_id: str = ""


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
    report = _overlay_names(store.robot_report(agent_id), await _name_overrides(request))
    return report or {"robots": [], "received_at_ms": None}


@router.get("/robots-mirror")
async def robots_mirror(request: Request, agent_id: str | None = None):
    """Same mirror without a mandatory agent id: picks the single live agent
    (store._pick semantics) — lets a per-robot showcase URL omit the agent."""
    _auth(request)
    store = _store(request)
    report = _overlay_names(store.robot_report(agent_id), await _name_overrides(request))
    return report or {"robots": [], "received_at_ms": None}


@router.post("/robots/{robot_id}/rename")
async def rename_robot(robot_id: str, body: RenameBody, request: Request):
    """Set (or clear, when name is empty) the operator display name for an agent robot.
    Stored in STL (agent_control) as a robot_id→name overlay; the robot_id — the agent's
    real key for orders/state — is never changed."""
    _auth(request)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    name = (body.name or "").strip()[:64]
    key = _NAME_PREFIX + robot_id
    if not name:
        await pool.execute("DELETE FROM agent_control WHERE key=$1", key)
        return {"ok": True, "robot_id": robot_id, "name": None}
    await pool.execute(
        "INSERT INTO agent_control(key,value) VALUES($1,$2) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", key, name)
    return {"ok": True, "robot_id": robot_id, "name": name}


@router.post("/robots/{robot_id}/set-position-agent")
async def set_position_agent(robot_id: str, body: SetPositionBody, request: Request):
    """Operator belief-correction from STL: force the runner's believed position/avg for
    an agent robot (e.g. after a manual close the robot never emitted). BELIEF-ONLY — the
    agent relays it as a runner fix_state, never a real order. Gated: confirm_id must echo
    the robot id AND the robot must be PAUSED per the mirror (never rewrite a live trading
    book; the agent re-checks paused + confirm as well)."""
    _auth(request)
    srv = _server(request)
    store = _store(request)
    agent = _resolve_agent(request, body.agent_id)
    if body.confirm_id != robot_id:
        raise HTTPException(status_code=400, detail="Подтверждение не совпадает: введите точный ID робота.")
    report = store.robot_report(body.agent_id) or {}
    cur = next((r for r in report.get("robots", []) if r.get("robot_id") == robot_id), None)
    if cur is None:
        raise HTTPException(status_code=409, detail=(
            "Робот не найден в зеркале агента — обнови страницу / проверь линк."))
    if not cur.get("paused"):
        raise HTTPException(status_code=409, detail=(
            "Робот должен быть на ПАУЗЕ: поставь паузу перед установкой позиции."))
    if body.position != 0 and (body.avg_price or 0) <= 0:
        raise HTTPException(status_code=400, detail="Для ненулевой позиции нужна средняя цена > 0.")
    srv.enqueue_order(agent, pb.OrchestratorMessage(
        set_robot_position=pb.SetRobotPosition(
            robot_id=robot_id,
            position=int(body.position),
            avg_price=float(body.avg_price or 0.0),
            confirm_id=robot_id)))
    return {"ok": True, "agent_id": agent, "robot_id": robot_id, "position": int(body.position)}


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
