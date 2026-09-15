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

import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth
from trader.quik import orders as order_msgs
from trader.quik.limits import (
    LimitError,
    OrderLimits,
    check_daily_cap,
    check_master_flag,
    check_quantity,
    check_whitelist,
    validate_place,
    validate_replace,
    validate_start_execution,
)
from trader.quik.store import resolve_agent

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
    return resolve_agent(getattr(request.app.state, "quik_store", None), agent_id)


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


class ReplaceBody(BaseModel):
    client_id: str
    order_id: str | None = None
    new_price: float
    new_quantity: int = 0  # 0 = keep current quantity
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


class PlaceStopBody(BaseModel):
    """Нативная стоп-заявка QUIK (исполнительный модуль, этап 1).

    ``fields`` — поля транзакции конкретного вида (STOP_ORDER_KIND, STOPPRICE, PRICE,
    OFFSET...) КАК ЕСТЬ, текстом: настоящие имена и значения покажет серия S1 на GZ,
    поэтому здесь они не зашиты и не проверяются. Счёт, инструмент, сторону и объём
    агент ставит сам и отклоняет словарь, который пытается их нести."""
    client_id: str = ""  # пусто = STL сгенерирует so:<10hex>
    code: str
    side: str          # "buy" | "sell"
    quantity: int
    fields: dict[str, str] = {}
    agent_id: str | None = None


# client_id стоп-заявки: so:<10 hex>. Уходит в brokerref QUIK, у которого предел 20
# символов — длинный или чужой id терминал обрежет, и ответ транзакции не
# сопоставится с заявкой (формат задан окном real-trade для серии S1, 15.09.2026).
_STOP_CLIENT_ID = re.compile(r"^so:[0-9a-f]{10}$")


def _stop_client_id(given: str) -> str:
    cid = (given or "").strip()
    if not cid:
        return "so:" + secrets.token_hex(5)
    if not _STOP_CLIENT_ID.match(cid):
        raise HTTPException(status_code=422, detail="client_id стоп-заявки: so:<10 hex> (пусто = сгенерирует STL).")
    return cid


class KillStopBody(BaseModel):
    client_id: str
    stop_order_num: str
    code: str
    agent_id: str | None = None


# ---- config / status (read) ----

@router.get("/config")
async def orders_config(request: Request):
    """Limits + master-flag for the UI (renders the ticket + disabled state).

    Also returns the agent's CURRENTLY effective limits (agent_limits, echoed via
    LimitsState) so the UI can confirm STL's whitelist/caps actually reached the agent
    and flag a divergence instead of discovering it only on a rejected order.
    """
    _auth(request)
    lim = _limits(request)
    qstore = getattr(request.app.state, "quik_store", None)
    agent_limits = qstore.limits_state(None) if qstore is not None else None
    return {
        "trading_enabled": lim.trading_enabled,
        "max_contracts_per_order": lim.max_contracts_per_order,
        "max_working_contracts": lim.max_working_contracts,
        "price_collar_frac": lim.price_collar_frac,
        "instrument_whitelist": list(lim.instrument_whitelist),
        "daily_order_cap": lim.daily_order_cap,
        "agent_wired": _order_store(request) is not None,
        "agent_limits": agent_limits,
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


@router.post("/stop-place")
async def stop_place(body: PlaceStopBody, request: Request):
    """Поставить ОДНУ нативную стоп-заявку QUIK. HUMAN-INITIATED.

    Проверки до агента: kill-switch, мастер-флаг, белый список, объём заявки, дневной
    лимит. Ценового коллара нет — цены едут в ``fields`` необработанными до серии S1;
    агент перепроверяет всё сам (defense in depth). Серия S1 запускается только из окна
    real-trade при операторе и с белым списком = GZ на время серии (раздел 16)."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)

    if ost.is_blocked(agent):
        raise HTTPException(
            status_code=409,
            detail="Kill-switch активен: стоп-заявки заблокированы.",
        )
    if body.side.lower() not in ("buy", "sell"):
        raise HTTPException(status_code=422, detail="side: buy или sell.")
    client_id = _stop_client_id(body.client_id)
    try:
        check_master_flag(lim)
        check_whitelist(lim, body.code)
        check_quantity(lim, body.quantity)
        check_daily_cap(lim, ost.placed_today(agent))
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    msg = order_msgs.build_place_stop_order(
        client_id=client_id, code=body.code, side=body.side,
        quantity=body.quantity, fields=body.fields,
    )
    # Постановка стоп-заявки — транзакция: считается в дневной лимит, как и лимитная.
    ost.record_placement(agent)
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": client_id}


@router.post("/stop-kill")
async def stop_kill(body: KillStopBody, request: Request):
    """Снять нативную стоп-заявку по её номеру QUIK. Снимает экспозицию, поэтому, как и
    отмена заявки, проходит при единственном условии — мастер-флаге."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)
    try:
        check_master_flag(lim)
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    msg = order_msgs.build_kill_stop_order(body.client_id, body.stop_order_num, body.code)
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": body.client_id}


@router.get("/stop-orders")
async def stop_orders(request: Request, agent_id: str | None = None):
    """Зеркало стоп-заявок как прислал агент: таблица stop_orders целиком и кольцо
    событий OnStopOrder. Поля — словари как есть, до серии S1 без интерпретации."""
    _auth(request)
    qstore = getattr(request.app.state, "quik_store", None)
    snap = qstore.stop_orders(agent_id) if qstore is not None else None
    return snap or {"table": [], "table_received_ms": 0, "events": []}


@router.get("/trans-replies")
async def trans_replies(request: Request, agent_id: str | None = None,
                        client_id: str | None = None):
    """Ответы QUIK на транзакции (OnTransReply), свежие сверху, последние 200 на агента.

    Нужен серии S1: постановка стоп-заявки возвращает только client_id, а принял ли
    терминал транзакцию и с каким текстом — видно лишь в ответе. ``client_id`` сужает
    выдачу до одной заявки."""
    _auth(request)
    ost = _order_store(request)
    if ost is None:
        return {"replies": []}
    rows = ost.trans_replies(agent_id)
    if client_id:
        rows = [r for r in rows if r.get("client_id") == client_id]
    return {"replies": rows}


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


@router.post("/replace")
async def replace(body: ReplaceBody, request: Request):
    """Native atomic MOVE: re-price (and optionally re-size) a resting order in ONE
    QUIK MOVE_ORDERS transaction (no cancel+place window). Re-checks the master flag +
    the collar on the new price (vs the resting price) + the per-order qty cap, then
    enqueues ReplaceOrder. HUMAN-INITIATED (UI confirm). A move does NOT consume the
    daily cap (it re-prices an existing order)."""
    _auth(request)
    ost, srv = _require_wired(request)
    lim = _limits(request)
    agent = _resolve_agent(request, body.agent_id)

    if ost.is_blocked(agent):
        raise HTTPException(
            status_code=409,
            detail="Kill-switch активен: перестановка заявок заблокирована.",
        )

    # Reference price + side from the resting order (for the collar check). When the
    # order is not (yet) known locally, reference is 0 -> the agent still re-checks the
    # collar against the live resting price (defense in depth).
    rec = next(
        (o for o in ost.working_orders(agent) if o["client_id"] == body.client_id),
        None,
    )
    reference_price = float(rec["price"]) if rec else 0.0
    side = rec["side"] if rec else ""

    try:
        validate_replace(
            lim,
            new_price=body.new_price,
            new_quantity=body.new_quantity,
            reference_price=reference_price,
            side=side,
        )
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    msg = order_msgs.build_replace_order(
        client_id=body.client_id, order_id=body.order_id or "",
        new_price=body.new_price, new_quantity=body.new_quantity,
    )
    ost.register_replace(agent, body.client_id, body.new_price, body.new_quantity)
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
