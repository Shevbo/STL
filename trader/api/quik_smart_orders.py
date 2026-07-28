"""Smart-order API + the 1s watcher task (operator's manual SL/TP/Trail/OnFill).

Thin wiring around trader/quik/smart_orders.py (the pure engine): routes create/
list/cancel entries in the persisted book; the watcher evaluates them against the
live tick stream and fires plain limit orders through the SAME validated human
place path as /api/v1/quik/orders/place (master flag, collar, caps, kill-switch).

Fired child client_ids are "so:<so_id>" — no "rr:" prefix, so at the agent they
are untagged MANUAL-class orders: recon never touches them and no robot ever
sees them. HUMAN-INITIATED by construction: every smart order is created by the
operator; the watcher only executes the operator's standing instruction.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth
from trader.quik import orders as order_msgs
from trader.quik import smart_orders as so_mod
from trader.quik.limits import LimitError, OrderLimits, validate_place
from trader.quik.smart_orders import Cancel, Fire, SmartOrder, SmartOrderBook
from trader.quik.store import resolve_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/quik/smart-orders", tags=["quik-smart-orders"])

BOOK_PATH = "data/smart_orders.json"
_TICK_SEC = 1.0


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _book(request: Request) -> SmartOrderBook:
    book = getattr(request.app.state, "smart_orders", None)
    if book is None:
        raise HTTPException(status_code=503, detail="Smart-orders не инициализированы.")
    return book


class SmartOrderBody(BaseModel):
    kind: str                      # sl | tp | trail_tp | on_fill
    code: str
    side: str                      # buy | sell (сторона ДОЧЕРНЕЙ заявки)
    qty: int
    trigger_price: float = 0.0
    trail_offset: float = 0.0
    watch_client_id: str = ""
    child_price: float = 0.0
    oco_group: str = ""
    good_till_ms: int = 0
    sl_offset: float = 0.0         # защитный стоп в пунктах после входа (0 = без стопа)
    note: str = ""


@router.post("")
async def create(body: SmartOrderBody, request: Request):
    _auth(request)
    book = _book(request)
    so = SmartOrder(
        so_id=so_mod.new_id(), kind=body.kind, code=body.code,
        side=body.side.lower(), qty=int(body.qty),
        trigger_price=float(body.trigger_price),
        trail_offset=float(body.trail_offset), sl_offset=float(body.sl_offset),
        watch_client_id=body.watch_client_id, child_price=float(body.child_price),
        oco_group=body.oco_group, good_till_ms=int(body.good_till_ms),
        note=body.note, created_ms=so_mod.now_ms(),
    )
    err = so.validate()
    if err:
        raise HTTPException(status_code=422, detail=err)
    book.add(so)
    log.info("smart_order.created", so_id=so.so_id, kind=so.kind, code=so.code,
             side=so.side, qty=so.qty, trigger=so.trigger_price)
    return {"ok": True, "so_id": so.so_id}


@router.get("")
async def list_orders(request: Request):
    _auth(request)
    book = _book(request)
    from dataclasses import asdict
    return {"orders": [asdict(o) for o in book.orders]}


@router.delete("/{so_id}")
async def cancel_order(so_id: str, request: Request):
    _auth(request)
    book = _book(request)
    so = book.get(so_id)
    if so is None:
        raise HTTPException(status_code=404, detail="Нет такой умной заявки.")
    if so.status != "armed":
        raise HTTPException(status_code=409, detail=f"Заявка уже {so.status}.")
    so.status = "cancelled"
    so.note = (so.note + " " if so.note else "") + "отменена оператором"
    book.save()
    return {"ok": True, "so_id": so_id}


@router.post("/{so_id}/activate")
async def activate_order(so_id: str, body: dict, request: Request):
    """Ручная активация trail_tp от указанного пика: оператор ставит заявку в режим
    слежения (например, пробой уровня активации был ПРОПУЩЕН из-за простоя STL/watcher).
    Дальше watcher ведёт её как обычно — выкупит на откате trail_offset пунктов от пика.
    Человеко-инициировано: это стоящая инструкция оператора, watcher лишь исполняет."""
    _auth(request)
    book = _book(request)
    so = book.get(so_id)
    if so is None:
        raise HTTPException(status_code=404, detail="Нет такой умной заявки.")
    if so.kind != "trail_tp":
        raise HTTPException(status_code=409, detail="Активация вручную — только для trail_tp.")
    if so.status != "armed":
        raise HTTPException(status_code=409, detail=f"Заявка уже {so.status}.")
    try:
        peak = float((body or {}).get("peak") or 0)
    except (TypeError, ValueError):
        peak = 0.0
    if peak <= 0:
        raise HTTPException(status_code=422, detail="peak (уровень пика/активации) обязателен.")
    so.activated = True
    so.peak = peak
    so.note = (so.note + " " if so.note else "") + f"активирована оператором от {peak:g}"
    book.save()
    log.info("smart_orders.manual_activate", so_id=so_id, peak=peak, side=so.side, code=so.code)
    return {"ok": True, "so_id": so_id, "activated": True, "peak": peak}


# ---- watcher ----

def _price_steps(store: Any, agent: str) -> dict[str, float]:
    """code -> price_step from the QLua params feed (rows shape is the same the
    /api/v1/quik/params route serves). Missing step => 0 => no quantization —
    evaluate() still works, QUIK would reject an off-grid price, so a missing
    step simply must not happen for traded codes (params arrive with the feed)."""
    out: dict[str, float] = {}
    p = store.params(agent) if store else None
    for row in (p or {}).get("rows", []) or []:
        try:
            step = float(row.get("price_step") or 0)
            if step > 0 and row.get("code"):
                out[str(row["code"])] = step
        except (TypeError, ValueError):
            continue
    return out


# Дочерняя заявка живёт в QUIK, а QUIK чистит неисполненные на границе сессии.
# Сработавшая умная заявка, чей ребёнок умер не исполнившись, оставляла оператора
# с ложным чувством защиты: в книге написано «сработала», а в рынке ничего нет.
# Помечаем такие как orphaned — интерфейс предлагает перевзвести. Автоматически
# НЕ перевзводим: цена наутро другая, решение за человеком.
_ORPHAN_GRACE_MS = 5 * 60 * 1000
_DEAD_STATES = ("cancelled", "rejected")
# OrderStore живёт в памяти: рестарт STL стирает записи. Сработавшая ДО старта
# процесса заявка отсутствует в сторе не потому, что умерла — судить о ней нельзя
# (26.07 ложный orphaned звал оператора перевзвести УЖЕ исполненный выкуп 14 конт.).
_PROC_START_MS = so_mod.now_ms()


def _mark_orphans(book: SmartOrderBook, ost: Any, agent: str, now: int) -> bool:
    fired = [o for o in book.orders
             if o.status == "fired" and o.fired_client_id and o.fired_ms]
    if not fired:
        return False
    by_cid = {d["client_id"]: d for d in ost.working_orders(agent)}
    dirty = False
    for so in fired:
        rec = by_cid.get(so.fired_client_id)
        aged = now - so.fired_ms > _ORPHAN_GRACE_MS
        if rec is not None:
            if rec.get("state") in _DEAD_STATES and not rec.get("filled"):
                so.status = "orphaned"
                so.note = f"дочерняя заявка {rec.get('state')} и не исполнилась"
                dirty = True
        elif aged and so.fired_ms >= _PROC_START_MS:
            # Заявки нет в таблице вовсе: сессия закрылась и QUIK её снял, либо
            # агент перезапустился. Ни исполнения, ни заявки — защиты нет.
            so.status = "orphaned"
            so.note = "дочерняя заявка не найдена: снята на границе сессии"
            dirty = True
    return dirty


async def _watch_once(state: Any) -> None:
    book: SmartOrderBook = state.smart_orders
    active = book.active()
    store = getattr(state, "quik_store", None)
    ost = getattr(state, "quik_order_store", None)
    srv = getattr(state, "quik_server", None)
    if store is None or ost is None or srv is None:
        return
    try:
        agent = resolve_agent(store, None)
    except Exception:
        return  # no/ambiguous agent -> nothing to fire against
    # Осиротевших ищем ДАЖЕ когда взведённых нет: сработавшая заявка может
    # потерять ребёнка уже после того, как книга опустела.
    if _mark_orphans(book, ost, agent, so_mod.now_ms()):
        book.save()
    if not active:
        return
    if ost.is_blocked(agent):
        return  # kill-switch: keep orders armed, fire nothing

    lim = OrderLimits.from_settings(state.settings)
    steps = _price_steps(store, agent)
    filled = {d["client_id"] for d in ost.working_orders(agent)
              if d.get("state") == "filled"}
    now = so_mod.now_ms()
    dirty = False

    for code in book.codes():
        t = store.tick(code, agent) or {}
        actions = so_mod.evaluate(
            book.orders, code,
            last=float(t.get("last") or 0), bid=float(t.get("bid") or 0),
            ask=float(t.get("ask") or 0),
            tick_ms=int(t.get("received_at_unix_ms") or (now if t else 0)),
            now_ms=now, filled_client_ids=filled,
            step=steps.get(code, 0.0),
        )
        for act in actions:
            dirty = True
            if isinstance(act, Cancel):
                act.so.status = "cancelled"
                act.so.note = act.reason
                log.info("smart_order.oco_cancelled", so_id=act.so.so_id)
                continue
            assert isinstance(act, Fire)
            so = act.so
            client_id = f"so:{so.so_id}"
            try:
                validate_place(
                    lim, code=so.code, quantity=so.qty,
                    collar=lim.price_collar_frac,
                    current_working=ost.working_contracts(agent),
                    placed_today=ost.placed_today(agent),
                )
                msg = order_msgs.build_place_order(
                    client_id=client_id, code=so.code, side=so.side,
                    price=act.price, quantity=so.qty,
                    collar=lim.price_collar_frac,
                )
                ost.register_pending(agent, client_id, so.code, so.side,
                                     act.price, so.qty)
                ost.record_placement(agent)
                srv.enqueue_order(agent, msg)
                so.status = "fired"
                so.fired_ms = now
                so.fired_client_id = client_id
                log.info("smart_order.fired", so_id=so.so_id, kind=so.kind,
                         code=so.code, side=so.side, qty=so.qty, price=act.price)
                # Защитный стоп после входа (если оператор его заказал): trail и
                # on_fill только ВХОДЯТ и после срабатывания забывают про позицию —
                # без стопа выходить нечем, когда цена пошла против.
                child_sl = so_mod.protective_sl(so, act.price, now)
                if child_sl is not None:
                    book.orders.append(child_sl)
                    log.info("smart_order.protective_sl", parent=so.so_id,
                             so_id=child_sl.so_id, side=child_sl.side,
                             trigger=child_sl.trigger_price, offset=so.sl_offset)
            except LimitError as exc:
                so.status = "error"
                so.note = f"отклонено лимитами: {exc}"
                log.warning("smart_order.rejected", so_id=so.so_id, error=str(exc))
    # expiry flips status inside evaluate() without producing an action
    if dirty or any(o.status == "expired" for o in active):
        book.save()


# ---- авто-догон trail_tp (пробой уровня пропущен, пока STL лежал) ----
# Дневные экстремумы берём из ISS-свечей M10 С МОМЕНТА СОЗДАНИЯ заявки: дневной
# LOW/HIGH из marketdata брать нельзя — экстремум ДО создания заявки активировал
# бы её задним числом и мгновенно выкупил по рынку. Публичный ISS задержан ~15
# минут; живое пересечение ловит обычный 1с-цикл, догон закрывает только простой.
_CATCHUP_SEC = 300
_ISS_CANDLES = ("https://iss.moex.com/iss/engines/futures/markets/forts"
                "/securities/{code}/candles.json")
_MSK = timezone(timedelta(hours=3))


async def _catch_up_trails(state: Any) -> None:
    book: SmartOrderBook = state.smart_orders
    todo = [o for o in book.orders
            if o.status == "armed" and o.kind == "trail_tp"
            and not o.activated and o.trigger_price > 0]
    if not todo:
        return
    dirty = False
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "STL/1.0"}) as cl:
        for so in todo:
            frm = datetime.fromtimestamp(so.created_ms / 1000, _MSK).strftime(
                "%Y-%m-%d %H:%M:%S")
            r = await cl.get(_ISS_CANDLES.format(code=so.code),
                             params={"interval": 10, "from": frm, "iss.meta": "off"})
            c = r.json().get("candles") or {}
            cols, rows = c.get("columns") or [], c.get("data") or []
            if not rows:
                continue
            i_lo, i_hi, i_beg = cols.index("low"), cols.index("high"), cols.index("begin")
            # первая свеча может захватывать время ДО создания — выкидываем её
            rows = [row for row in rows if str(row[i_beg]) >= frm]
            # ponytail: одна страница ISS = 500 свечей M10 (~3.5 торговых суток от
            # создания); более старые заявки догоняются живыми тиками
            if not rows:
                continue
            wmin = min(float(row[i_lo]) for row in rows)
            wmax = max(float(row[i_hi]) for row in rows)
            if so_mod.catch_up_trail(so, wmin, wmax):
                dirty = True
                log.info("smart_order.auto_activated", so_id=so.so_id, code=so.code,
                         side=so.side, peak=so.peak, trigger=so.trigger_price)
    if dirty:
        book.save()


async def run_watcher(state: Any) -> None:
    """Background task: evaluate the book every second. Never dies on an error —
    a broken pass is logged and the next tick retries (the trade path stays
    guarded by validate_place either way)."""
    log.info("smart_orders.watcher_started", path=BOOK_PATH)
    ticks = 0
    while True:
        await asyncio.sleep(_TICK_SEC)
        ticks += 1
        try:
            await _watch_once(state)
            if ticks % int(_CATCHUP_SEC / _TICK_SEC) == 0:
                await _catch_up_trails(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — watcher must survive
            log.warning("smart_orders.watch_failed", error=str(exc))
