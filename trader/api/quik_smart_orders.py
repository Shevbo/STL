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
from typing import Any

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
    note: str = ""


@router.post("")
async def create(body: SmartOrderBody, request: Request):
    _auth(request)
    book = _book(request)
    so = SmartOrder(
        so_id=so_mod.new_id(), kind=body.kind, code=body.code,
        side=body.side.lower(), qty=int(body.qty),
        trigger_price=float(body.trigger_price),
        trail_offset=float(body.trail_offset),
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


async def _watch_once(state: Any) -> None:
    book: SmartOrderBook = state.smart_orders
    active = book.active()
    if not active:
        return
    store = getattr(state, "quik_store", None)
    ost = getattr(state, "quik_order_store", None)
    srv = getattr(state, "quik_server", None)
    if store is None or ost is None or srv is None:
        return
    try:
        agent = resolve_agent(store, None)
    except Exception:
        return  # no/ambiguous agent -> nothing to fire against
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
            except LimitError as exc:
                so.status = "error"
                so.note = f"отклонено лимитами: {exc}"
                log.warning("smart_order.rejected", so_id=so.so_id, error=str(exc))
    # expiry flips status inside evaluate() without producing an action
    if dirty or any(o.status == "expired" for o in active):
        book.save()


async def run_watcher(state: Any) -> None:
    """Background task: evaluate the book every second. Never dies on an error —
    a broken pass is logged and the next tick retries (the trade path stays
    guarded by validate_place either way)."""
    log.info("smart_orders.watcher_started", path=BOOK_PATH)
    while True:
        await asyncio.sleep(_TICK_SEC)
        try:
            await _watch_once(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — watcher must survive
            log.warning("smart_orders.watch_failed", error=str(exc))
