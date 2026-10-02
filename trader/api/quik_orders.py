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

import structlog

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth
from trader.quik import orders as order_msgs
from trader.quik import terminal
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
from trader.quik.truth import load_robot_ids

log = structlog.get_logger(__name__)

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
    # Инструмент: с ним агент снимает заявку по НОМЕРУ, не заглядывая в свою карту
    # (см. trader/quik/orders.build_cancel_order). Без него снятие работает только
    # пока карта агента цела, то есть пока агент не перезапускали.
    code: str | None = None


class TerminalCancelBody(BaseModel):
    """Снятие по НОМЕРАМ заявок из таблицы терминала. Инструмент не принимаем: он
    берётся из той же таблицы, иначе ошибка в нём выглядела бы успешным снятием."""

    order_nums: list[str]
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
    msg = order_msgs.build_cancel_order(body.client_id, body.order_id or "",
                                        body.code or "")
    srv.enqueue_order(agent, msg)
    return {"ok": True, "agent_id": agent, "client_id": body.client_id}


class SettingsBody(BaseModel):
    """Настройки живой торговли. Поле не прислано — значение остаётся прежним:
    частичная правка не должна обнулять то, о чём вызывающий не думал."""

    instrument_whitelist: list[str] | None = None
    max_contracts_per_order: int | None = None
    max_working_contracts: int | None = None
    daily_order_cap: int | None = None
    price_collar_frac: float | None = None
    trading_enabled: bool | None = None


@router.get("/settings")
async def get_settings(request: Request):
    """ВСЕ операционные настройки живой торговли из ОДНОГО файла.

    Файл: `data/quik_limits.json`. Читается на каждую проверку заявки и
    перечитывается по mtime, поэтому правка действует СРАЗУ, без рестарта STL.

    Чего здесь НЕТ и почему: агентский `agent_config.json` и `shectory_trade_config.lua`
    живут на машине QUIK. Это БЭКСТОП — пуш из STL умеет только ужесточать его, но
    не расширять. Сведя их сюда, мы дали бы расширять предохранитель оттуда же,
    откуда торгуют. Их текущие значения видно в зеркале агента.
    """
    _auth(request)
    from trader.quik import settings_file
    v = settings_file.load(request.app.state.settings)
    return {"settings": v, "path": settings_file.PATH,
            "note": ("агентские пределы (agent_config.json) и список инструментов "
                     "QLua (shectory_trade_config.lua) живут на машине QUIK: "
                     "это бэкстоп, STL умеет только ужесточать его")}


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request):
    """Записать настройки и ТУТ ЖЕ протолкнуть их агенту, без рестарта.

    Проверки перед записью — потому что пустые пределы это «разрешить всё»:
    положительные числа, коллар в разумных границах, непустой белый список.
    """
    _auth(request)
    from trader.quik import settings_file
    cur = settings_file.load(request.app.state.settings)
    new = dict(cur)
    for k in ("max_contracts_per_order", "max_working_contracts", "daily_order_cap"):
        val = getattr(body, k)
        if val is not None:
            if int(val) <= 0:
                raise HTTPException(status_code=422, detail=(
                    f"{k} должен быть больше нуля: ноль и отрицательное запрещают "
                    "торговлю молча, для остановки есть мастер-флаг."))
            new[k] = int(val)
    if body.price_collar_frac is not None:
        if not 0 < float(body.price_collar_frac) <= 0.5:
            raise HTTPException(status_code=422, detail=(
                "price_collar_frac вне (0, 0.5]: ноль запретит любую заявку, "
                "а полтинника достаточно для самого широкого коридора."))
        new["price_collar_frac"] = float(body.price_collar_frac)
    if body.instrument_whitelist is not None:
        wl = [str(c).strip() for c in body.instrument_whitelist if str(c).strip()]
        if not wl:
            raise HTTPException(status_code=422, detail=(
                "Пустой белый список запрещает ВСЮ торговлю молча. "
                "Для остановки есть мастер-флаг trading_enabled."))
        new["instrument_whitelist"] = wl
    if body.trading_enabled is not None:
        new["trading_enabled"] = bool(body.trading_enabled)
    settings_file.save(new)
    # ПРОТАЛКИВАЕМ АГЕНТУ СРАЗУ. Иначе STL и агент разъедутся до ближайшего
    # переподключения, и заявка, законная по STL, умрёт у агента без следа в QUIK.
    pushed = False
    srv = getattr(request.app.state, "quik_server", None)
    if srv is not None:
        try:
            agent = _resolve_agent(request, None)
            srv.enqueue_order(agent, order_msgs.build_set_limits(
                instrument_whitelist=new["instrument_whitelist"],
                max_contracts_per_order=new["max_contracts_per_order"],
                max_working_contracts=new["max_working_contracts"],
                price_collar_frac=new["price_collar_frac"],
                daily_order_cap=new["daily_order_cap"]))
            pushed = True
        except Exception as exc:  # noqa: BLE001 — запись состоялась, пуш догонит
            log.warning("quik.settings.push_failed", error=str(exc))
    log.info("quik.settings.saved", values=new, pushed=pushed)
    return {"ok": True, "settings": new, "pushed_to_agent": pushed}


@router.get("/terminal")
async def terminal_orders(request: Request, agent_id: str | None = None,
                          active_only: bool = False):
    """ЧТО ПРЯМО СЕЙЧАС СТОИТ В QUIK — вся таблица заявок терминала.

    Единственный ответ на вопрос «что в рынке», который не зависит от памяти STL.
    Остальные экраны показывали заявки по своей бухгалтерии: склад заявок (память
    процесса STL), карта агента (память процесса агента) или книга умных заявок. Все
    три не знают о заявке, поставленной руками в терминале, и все три пустеют при
    рестарте — поэтому 01.10.2026 оператор видел в QUIK живые заявки, которых STL
    «не видел ни одной».

    Природа заявки здесь ЯРЛЫК (`origin`: manual | smart | robot | recon | external),
    а не фильтр: заявка стоит в рынке и торгует деньгами — она обязана быть видна,
    кто бы её ни поставил. `stale: true` = зеркала агента нет или оно встало, и тогда
    пустой список означает «НЕ ЗНАЮ», а не «в терминале ничего».
    """
    _auth(request)
    store = getattr(request.app.state, "quik_store", None)
    agent = _resolve_agent(request, agent_id)
    ids = load_robot_ids()
    # ДВЕ ТАБЛИЦЫ, ОДИН ОТВЕТ. Нативные стоп-заявки живут в своей таблице QUIK и
    # едут от агента отдельным сообщением, но для оператора это такая же живая
    # заявка в терминале — и ровно она стережёт позицию. Показывать одну таблицу
    # и звать это «что стоит в QUIK» значит снова обещать больше, чем отдаёшь.
    rows = terminal.rows(store, agent, ids) + terminal.stop_rows(store, agent, ids)
    rows.sort(key=lambda d: (not d["active"], -(d.get("ts_ms") or 0)))
    if active_only:
        rows = [r for r in rows if r["active"]]
    return {"agent_id": agent, "orders": rows, "total": len(rows),
            "active": sum(1 for r in rows if r["active"]),
            "stale": not terminal.fresh(store, agent)}


@router.post("/terminal/cancel")
async def terminal_cancel(body: TerminalCancelBody, request: Request):
    """Снять ЛЮБУЮ заявку из таблицы терминала — по номеру, чьей бы она ни была.

    Работает без чьей-либо памяти: KILL_ORDER в QUIK нужны только номер заявки,
    класс и инструмент, а инструмент берётся из той же таблицы. Поэтому ручка
    снимает и заявку, поставленную руками, и заявку, пережившую перезапуск агента
    (01.10.2026: 24 заявки сетки, которые STL слал снимать, а агент не находил).

    Снятие УМЕНЬШАЕТ экспозицию, поэтому гейт один — мастер-флаг, как у /cancel.
    """
    _auth(request)
    _ost, srv = _require_wired(request)
    lim = _limits(request)
    store = getattr(request.app.state, "quik_store", None)
    agent = _resolve_agent(request, body.agent_id)
    try:
        check_master_flag(lim)
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Инструмент берём ИЗ ТАБЛИЦЫ, а не от вызывающего: KILL_ORDER с чужим
    # инструментом молча не снимет ничего, и это выглядело бы как успех.
    rows = {r["num"]: r for r in terminal.rows(store, agent)}
    # Стоп-заявки в тот же индекс: снимаются ДРУГОЙ командой QUIK (KILL_STOP_ORDER
    # против KILL_ORDER), и перепутав команду, получишь тихий неуспех — заявка
    # останется стеречь, а ручка отчитается об успехе. Вид несёт поле kind.
    for r in terminal.stop_rows(store, agent):
        rows.setdefault(r["num"], r)
    wanted = [n for n in (body.order_nums or []) if n]
    if not wanted:
        raise HTTPException(status_code=422, detail="order_nums пуст")
    sent, skipped = [], []
    for num in wanted:
        row = rows.get(str(num))
        if row is None:
            skipped.append({"num": num, "why": "нет в таблицах заявок терминала"})
            continue
        if not row["active"]:
            skipped.append({"num": num, "why": f"уже не активна ({row['state']})"})
            continue
        if row.get("kind") == "stop":
            srv.enqueue_order(agent, order_msgs.build_kill_stop_order(
                client_id=f"op:kill:{num}", stop_order_num=str(num), code=row["sec"]))
        else:
            srv.enqueue_order(agent, order_msgs.build_cancel_order(
                client_id=f"op:kill:{num}", order_id=str(num), code=row["sec"]))
        sent.append({"num": num, "sec": row["sec"], "side": row["side"],
                     "price": row["price"], "balance": row["balance"],
                     "origin": row["origin"], "kind": row.get("kind", "order")})
    log.info("quik.terminal_cancel", agent=agent, sent=len(sent), skipped=len(skipped))
    return {"ok": True, "agent_id": agent, "sent": sent, "skipped": skipped}


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
