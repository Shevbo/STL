"""FastAPI routes for QUIK orders (sprint02 Phase 2). HUMAN-INITIATED only.

Every mutating route:
  * is portal-authenticated (the same require_auth as the rest of STL),
  * re-checks the master flag (quik_trading_enabled) AND every hard limit BEFORE
    the order is built/sent to the agent (defense in depth — the agent re-checks),
  * is operator-initiated: the UI shows a confirm dialog with instrument/side/
    price/qty/notional + maker-commission estimate before calling these.

No strategy, no auto-placement anywhere (Guard 3). Account/secrets by keymaster
name only — never in this module.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth
from trader.quik import orders as order_msgs
from trader.quik.limits import (
    LimitError,
    OrderLimits,
    check_master_flag,
    validate_place,
    validate_start_execution,
)

router = APIRouter(prefix="/api/v1/quik/orders", tags=["quik-orders"])


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _order_store(request: Request):
    return getattr(request.app.state, "quik_order_store", None)


def _server(request: Request):
    return getattr(request.app.state, "quik_server", None)


def _limits(request: Request) -> OrderLimits:
    return OrderLimits.from_settings(request.app.state.settings)


def _resolve_agent(request: Request, agent_id: str | None) -> str:
    """Pick the target agent: the explicit one, else the single LIVE (green) agent.

    The in-memory store accumulates stale entries (a pre-Register subject id, dead
    probes, old sessions). Prefer the single agent whose link is fresh (green) so the
    UI need not pass agent_id when exactly one agent is actually connected.
    """
    store = getattr(request.app.state, "quik_store", None)
    if agent_id:
        return agent_id
    if store is None:
        raise HTTPException(status_code=409, detail="Нет подключённого QUIK агента.")
    green = [r["agent_id"] for r in store.status() if r.get("link") == "green"]
    if len(green) == 1:
        return green[0]
    ids = store.agent_ids()
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise HTTPException(status_code=409, detail="Нет подключённого QUIK агента.")
    detail = (
        "Несколько живых агентов — укажите agent_id."
        if len(green) > 1
        else "Подключено несколько агентов — укажите agent_id."
    )
    raise HTTPException(status_code=400, detail=detail)


def _require_wired(request: Request):
    """Order store + server must exist (QUIK link enabled)."""
    ost = _order_store(request)
    srv = _server(request)
    if ost is None or srv is None:
        raise HTTPException(
            status_code=503,
            detail="QUIK агент не запущен (quik_agent_enabled=false).",
        )
    return ost, srv


# ---- request bodies ----

class PlaceBody(BaseModel):
    client_id: str
    code: str
    side: str          # "buy" | "sell"
    price: float
    quantity: int
    collar: float | None = None  # defaults to the configured hard collar
    agent_id: str | None = None


class CancelBody(BaseModel):
    client_id: str
    order_id: str | None = None
    agent_id: str | None = None


class KillSwitchBody(BaseModel):
    reason: str | None = None
    agent_id: str | None = None


class StartExecBody(BaseModel):
    client_id: str
    code: str
    side: str
    target_quantity: int
    worst_price: float
    allow_cross: bool = False
    agent_id: str | None = None


class StopExecBody(BaseModel):
    client_id: str
    agent_id: str | None = None


# ---- config / status (read) ----

@router.get("/config")
async def orders_config(request: Request):
    """Limits + master-flag for the UI (renders the ticket + disabled state)."""
    _auth(request)
    lim = _limits(request)
    return {
        "trading_enabled": lim.trading_enabled,
        "max_contracts_per_order": lim.max_contracts_per_order,
        "max_working_contracts": lim.max_working_contracts,
        "price_collar_frac": lim.price_collar_frac,
        "instrument_whitelist": list(lim.instrument_whitelist),
        "daily_order_cap": lim.daily_order_cap,
        "agent_wired": _order_store(request) is not None,
    }


@router.get("/working")
async def working_orders(request: Request, agent_id: str | None = None):
    """Working orders (resting + recent) for the UI table."""
    _auth(request)
    ost = _order_store(request)
    if ost is None:
        return {"orders": []}
    return {"orders": ost.working_orders(agent_id)}


@router.get("/executions")
async def executions(request: Request, agent_id: str | None = None):
    """Maker-execution progress rows (1b) for the UI table."""
    _auth(request)
    ost = _order_store(request)
    if ost is None:
        return {"executions": []}
    return {"executions": ost.executions(agent_id)}


# ---- mutating routes (re-check limits + master flag every time) ----

@router.post("/place")
async def place(body: PlaceBody, request: Request):
    """Place ONE limit order. Re-checks the master flag + every hard limit, then
    enqueues PlaceOrder onto the agent's stream. HUMAN-INITIATED (UI confirm)."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)

    if ost.is_blocked(agent):
        raise HTTPException(
            status_code=409,
            detail="Kill-switch активен: новые заявки заблокированы.",
        )
    collar = lim.price_collar_frac if body.collar is None else float(body.collar)
    try:
        validate_place(
            lim,
            code=body.code,
            quantity=body.quantity,
            collar=collar,
            current_working=ost.working_contracts(agent),
            placed_today=ost.placed_today(agent),
        )
    except LimitError as exc:
        # Rejected at STL BEFORE reaching the agent.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    msg = order_msgs.build_place_order(
        client_id=body.client_id, code=body.code, side=body.side,
        price=body.price, quantity=body.quantity, collar=collar,
    )
    ost.register_pending(
        agent, body.client_id, body.code, body.side.lower(),
        body.price, body.quantity,
    )
    ost.record_placement(agent)
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": body.client_id}


@router.post("/cancel")
async def cancel(body: CancelBody, request: Request):
    """Cancel a working order by client_id (and/or QUIK order_id)."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)
    # A cancel REDUCES exposure, so the master flag is the only gate.
    try:
        check_master_flag(lim)
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    msg = order_msgs.build_cancel_order(body.client_id, body.order_id or "")
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": body.client_id}


@router.post("/kill-switch")
async def kill_switch(body: KillSwitchBody, request: Request):
    """Cancel ALL working orders + block new placements until cleared. Always
    allowed (a safety action) regardless of the master flag."""
    _auth(request)
    ost, srv = _require_wired(request)
    agent = _resolve_agent(request, body.agent_id)
    ost.set_blocked(agent, True)
    msg = order_msgs.build_kill_switch(body.reason or "operator kill-switch")
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "blocked": True}


@router.post("/clear-kill-switch")
async def clear_kill_switch(body: KillSwitchBody, request: Request):
    """Explicitly clear the block so placements are allowed again (operator only)."""
    _auth(request)
    ost, _srv = _require_wired(request)
    agent = _resolve_agent(request, body.agent_id)
    ost.set_blocked(agent, False)
    return {"ok": True, "agent_id": agent, "blocked": False}


@router.post("/start-execution")
async def start_execution(body: StartExecBody, request: Request):
    """Start maker-working a human-decided order (1b). Re-checks master flag +
    limits, then enqueues StartExecution."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)
    if ost.is_blocked(agent):
        raise HTTPException(
            status_code=409,
            detail="Kill-switch активен: исполнение заблокировано.",
        )
    try:
        validate_start_execution(
            lim,
            code=body.code,
            target_quantity=body.target_quantity,
            current_working=ost.working_contracts(agent),
            placed_today=ost.placed_today(agent),
        )
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    msg = order_msgs.build_start_execution(
        client_id=body.client_id, code=body.code, side=body.side,
        target_quantity=body.target_quantity, worst_price=body.worst_price,
        allow_cross=body.allow_cross,
    )
    ost.record_placement(agent)
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": body.client_id}


@router.post("/stop-execution")
async def stop_execution(body: StopExecBody, request: Request):
    """Stop a working maker execution (cancels remainder in the agent)."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)
    try:
        check_master_flag(lim)
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    msg = order_msgs.build_stop_execution(body.client_id)
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": body.client_id}
