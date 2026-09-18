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
from trader.quik import native_protect
from trader.quik import orders as order_msgs
from trader.quik import smart_orders as so_mod
from trader.quik.alerts import SEVERITY_CRITICAL
from trader.quik.limits import (
    LimitError,
    OrderLimits,
    check_master_flag,
    check_quantity,
    check_whitelist,
    validate_place,
)
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
    tp_offset: float = 0.0         # тейк в пунктах доходного хода после входа (0 = без тейка)
    trail_after: float = 0.0       # подтягивающая в пунктах после входа (0 = без неё)
    tp_trail: float = 0.0          # тейк после входа следящий: откат в пунктах (0 = фиксированный)
    sl_price: float = 0.0          # стоп после входа ЦЕНОЙ уровня (вместо пунктов)
    tp_price: float = 0.0          # тейк после входа ЦЕНОЙ уровня (вместо пунктов)
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
        tp_offset=float(body.tp_offset), trail_after=float(body.trail_after),
        tp_trail=float(body.tp_trail), sl_price=float(body.sl_price),
        tp_price=float(body.tp_price),
        watch_client_id=body.watch_client_id, child_price=float(body.child_price),
        oco_group=body.oco_group, good_till_ms=int(body.good_till_ms),
        note=body.note, created_ms=so_mod.now_ms(),
    )
    # Рыночная цена инструмента даёт валидации точку отсчёта: без неё ЦЕНУ,
    # введённую в поле пунктов, не отличить от больших пунктов (заявка без уровня
    # активации собственного trigger_price не имеет).
    silent = _silent_instrument(request, so.code)
    if silent:
        raise HTTPException(status_code=422, detail=silent)
    err = so.validate(_market_price(request, so.code))
    if err:
        raise HTTPException(status_code=422, detail=err)
    # Отклоняем заведомо невыполнимую заявку ПРИ ВЗВЕДЕНИИ, а не в момент
    # срабатывания: оператор должен увидеть отказ сейчас, а не молчаливый
    # статус error в книге (инцидент — заявка на 50 при лимите на заявку 34).
    lim = OrderLimits.from_settings(request.app.state.settings)
    try:
        check_master_flag(lim)
        check_whitelist(lim, so.code)
        check_quantity(lim, so.qty)
    except LimitError as exc:
        raise HTTPException(
            status_code=422, detail=f"отклонено лимитами: {exc}"
        ) from exc
    # Подтягивающая ставится на УЖЕ ОТКРЫТУЮ позицию, и прежний стоп на ней
    # оставлять нельзя: сработает ближний, а дальний останется взведён и
    # следующим ходом ОТКРОЕТ позицию в обратную сторону. Снимаем до того, как
    # взвести новую — чтобы между двумя действиями не было тика с двумя стопами.
    superseded = so_mod.superseded_stops(book.active(), so)
    for old in superseded:
        old.status = "cancelled"
        old.note = ((old.note + " ") if old.note else "") + \
            f"снят подтягивающей {so.so_id}: два стопа на одной позиции"
        log.info("smart_order.superseded", so_id=old.so_id, by=so.so_id, kind=old.kind)
    book.add(so)
    if superseded:
        book.save()
    log.info("smart_order.created", so_id=so.so_id, kind=so.kind, code=so.code,
             side=so.side, qty=so.qty, trigger=so.trigger_price)
    return {"ok": True, "so_id": so.so_id,
            "superseded": [o.so_id for o in superseded]}


@router.get("")
async def list_orders(request: Request):
    _auth(request)
    book = _book(request)
    from dataclasses import asdict
    sess = getattr(request.app.state, "market_session", None) or {}
    # Вне торгов сторож намеренно не срабатывает — интерфейс обязан это сказать,
    # иначе взведённая заявка выглядит сломанной.
    return {"orders": [asdict(o) for o in book.orders],
            "session": {"open": sess.get("open"), "phase": sess.get("phase", "")}}


@router.delete("/{so_id}")
async def cancel_order(so_id: str, request: Request):
    _auth(request)
    book = _book(request)
    so = book.get(so_id)
    if so is None:
        raise HTTPException(status_code=404, detail="Нет такой умной заявки.")
    if so.status not in ("armed", "native"):
        raise HTTPException(status_code=409, detail=f"Заявка уже {so.status}.")
    if so.status == "native":
        # Заявка живёт в терминале: снять её можно только там, иначе книга скажет
        # «отменена», а стоп-заявка останется стеречь позицию.
        _kill_native(request, so)
    so.status = "cancelled"
    so.note = (so.note + " " if so.note else "") + "отменена оператором"
    book.save()
    log.info("smart_order.cancelled", so_id=so_id, kind=so.kind, code=so.code,
             side=so.side, qty=so.qty)
    return {"ok": True, "so_id": so_id}


def _kill_native(request: Request, so: SmartOrder) -> None:
    """Снять нативную стоп-заявку, которой отдали защиту. Номер знает держатель."""
    state = request.app.state
    store = getattr(state, "quik_store", None)
    srv = getattr(state, "quik_server", None)
    book = _book(request)
    holder = so if so.native_stop_num else next(
        (c for c in book.orders if c.parent_id == so.parent_id and c.native_stop_num), None)
    if holder is None or not holder.native_stop_num or srv is None or store is None:
        raise HTTPException(status_code=409,
                            detail="Заявка под охраной терминала, номер стоп-заявки неизвестен: снимите её в QUIK.")
    agent = resolve_agent(store, None)
    srv.enqueue_order(agent, order_msgs.build_kill_stop_order(
        f"so:{holder.so_id}", holder.native_stop_num, so.code))
    for c in book.orders:
        if c.parent_id == so.parent_id and c.status == "native" and c is not so:
            c.status = "cancelled"
            c.note = (c.note + " " if c.note else "") + "снята вместе со связкой в терминале"
    parent = book.get(so.parent_id)
    if parent is not None:
        parent.native_state = "done"
    log.info("smart_order.native_killed", so_id=so.so_id, stop_num=holder.native_stop_num)


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

def _market_price(request: Request, code: str) -> float:
    """Последняя цена инструмента из кадра агента, 0 если её нет."""
    store = getattr(request.app.state, "quik_store", None)
    if store is None:
        return 0.0
    try:
        tick = store.tick(code, resolve_agent(store, None)) or {}
        return float(tick.get("last") or 0)
    except Exception:  # noqa: BLE001 - валидация не должна падать из-за отсутствия кадра
        return 0.0


def _silent_instrument(request: Request, code: str) -> str | None:
    """Инструмент молчит (истёк, снят с торгов, не подключён) - взводить нельзя."""
    store = getattr(request.app.state, "quik_store", None)
    if store is None:
        return None
    try:
        agent = resolve_agent(store, None)
        now = so_mod.now_ms()
        ages: dict[str, int] = {}
        for row in ((store.params(agent) or {}).get("rows") or []):
            c = str(row.get("code") or "")
            ts = int((store.tick(c, agent) or {}).get("received_at_unix_ms") or 0)
            if c and ts:
                ages[c] = now - ts
        if not ages:
            return None          # кадров нет вовсе (агент молчит) - судить не по чему
        return so_mod.silent_code(code, ages)
    except Exception:  # noqa: BLE001 - проверка не должна ронять взведение
        return None


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
# Исполнение дописываем, ПОКА заявка не набрана целиком: раньше первое же
# наблюдение замораживало цифру навсегда, и книга показывала «куплен 1 контракт»
# там, где QUIK налил все 19 (06.08.2026). Дальше суток смотреть незачем —
# неисполненный остаток снимает граница сессии, это ловит _mark_orphans.
_FILL_TRACK_MS = 24 * 3600 * 1000
# Передача защиты под охрану терминала. Столько ждём регистрации стоп-заявки в
# QUIK; не дождались - возвращаем защиту сторожу STL и будим оператора. Ждать
# долго нельзя: всё это время позиция защищена только нашими заявками, а они
# уже помечены как отданные.
_NATIVE_CONFIRM_MS = 20_000
# Флаги стоп-заявки QUIK: бит 0 «активна». Снят - запись отработала или снята
# (28 исполнена, 26/30 снята; серии S1-S2, execution-module.md 3a/3b).
_STOP_ALIVE_BIT = 1


def _stop_rows_by_tag(store: Any, agent: str) -> dict[str, dict]:
    """Таблица стоп-заявок QUIK по нашему тегу (brokerref). Тег ребёнка-держателя
    наследуется и дочерней заявкой, и сделкой - по нему видно всё."""
    snap = (store.stop_orders(agent) if store is not None else None) or {}
    out: dict[str, dict] = {}
    for row in snap.get("table") or []:
        tag = str(row.get("brokerref") or "")
        if tag.startswith("stl-so-"):
            out[tag[len("stl-so-"):]] = row
    return out


def _handover_to_terminal(book: SmartOrderBook, steps: dict[str, float], ost: Any,
                          srv: Any, agent: str, now: int) -> bool:
    """Отдать защиту открытой позиции терминалу.

    Наш сторож живёт в STL: упал STL или оборвалась связь - позиция без стопа и
    без тейка. Стоп-заявку QUIK держит сам, поэтому как только известна ФАКТИЧЕСКАЯ
    цена входа, ставим нативную запись, а свои защитные заявки переводим в
    «под охраной терминала» (сторож их больше не трогает). Не приняли - вернём.
    """
    dirty = False
    for parent in book.orders:
        if parent.status != "fired" or parent.fired_price <= 0 or parent.native_state:
            continue
        kids = [c for c in book.orders
                if c.parent_id == parent.so_id and c.status == "armed"]
        if not kids:
            continue
        plan = native_protect.build_native_protection(
            parent, parent.fired_price, steps.get(parent.code, 0.0))
        if plan is None:
            continue                      # нативного аналога нет: стережёт STL
        holder = kids[0]
        client_id = f"so:{holder.so_id}"
        try:
            srv.enqueue_order(agent, order_msgs.build_place_stop_order(
                client_id=client_id, code=parent.code, side=plan["side"],
                quantity=plan["quantity"], fields=plan["fields"]))
        except Exception as exc:  # noqa: BLE001 - связь с агентом не должна ронять проход
            log.warning("smart_order.native_send_failed", parent=parent.so_id, error=str(exc))
            continue
        ost.record_placement(agent)
        parent.native_state, parent.native_ms = "sent", now
        for c in kids:
            c.status = "native"
            c.note = (c.note + " " if c.note else "") + \
                f"под охраной терминала (стоп-заявка {holder.so_id})"
        dirty = True
        log.info("smart_order.native_sent", parent=parent.so_id, holder=holder.so_id,
                 kind=plan["fields"].get("STOP_ORDER_KIND"), kinds=plan["kinds"],
                 entry=parent.fired_price, fields=plan["fields"])
    return dirty


def _native_holder(book: SmartOrderBook, parent: SmartOrder) -> SmartOrder | None:
    return next((c for c in book.orders
                 if c.parent_id == parent.so_id and c.status in ("native", "fired", "cancelled")
                 and c.native_state), None)


def _track_native(book: SmartOrderBook, store: Any, agent: str, now: int) -> list[SmartOrder]:
    """Следить за отданными записями: зарегистрирована, отработала, снята, не принята.

    Возвращает родителей, у которых передача СОРВАЛАСЬ: их защиту вернули сторожу
    STL, и оператора надо разбудить - позиция была бы голой, промолчи мы тут."""
    watched = [p for p in book.orders if p.native_state in ("sent", "live")]
    if not watched:
        return []
    rows = _stop_rows_by_tag(store, agent)
    failed: list[SmartOrder] = []
    for parent in watched:
        kids = [c for c in book.orders if c.parent_id == parent.so_id]
        holder = next((c for c in kids if c.status == "native"), None)
        row = rows.get(holder.so_id) if holder is not None else None
        if row is None:
            if parent.native_state == "sent" and now - parent.native_ms > _NATIVE_CONFIRM_MS:
                parent.native_state = "failed"
                for c in kids:
                    if c.status == "native":
                        c.status = "armed"
                        c.note = (c.note + " " if c.note else "") + \
                            "терминал стоп-заявку не принял: защиту снова ведёт STL"
                failed.append(parent)
                log.warning("smart_order.native_rejected", parent=parent.so_id)
            elif parent.native_state == "live":
                # Была в таблице и исчезла: считаем отработавшей, судить о судьбе
                # позиции по отсутствию записи нельзя - это делает сверка позиций.
                parent.native_state = "done"
                for c in kids:
                    if c.status == "native":
                        c.status = "fired"
                        c.note = (c.note + " " if c.note else "") + "исполнена терминалом"
                log.info("smart_order.native_gone", parent=parent.so_id)
            continue
        try:
            flags = int(row.get("flags") or 0)
        except (TypeError, ValueError):
            flags = 0
        if parent.native_state == "sent":
            parent.native_state = "live"
            if holder is not None:
                holder.native_state = "live"
                holder.native_stop_num = str(row.get("order_num") or "")
            log.info("smart_order.native_live", parent=parent.so_id,
                     stop_num=row.get("order_num"), flags=row.get("flags"))
        if not flags & _STOP_ALIVE_BIT:
            parent.native_state = "done"
            done_as = "fired" if flags == 28 else "cancelled"
            for c in kids:
                if c.status == "native":
                    c.status = done_as
                    c.note = (c.note + " " if c.note else "") + (
                        "исполнена терминалом" if done_as == "fired" else "снята в терминале")
            log.info("smart_order.native_done", parent=parent.so_id, flags=flags,
                     linked=row.get("linkedorder"))
    return failed




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
            # Отсрочка и здесь: агент помечает rejected заявку, на которую QUIK не
            # ответил за 20 с, а исполнение приходит позже (14.09 продажа 10 RIU6
            # исполнилась, книга звала перевзвести уже открытую позицию).
            if aged and rec.get("state") in _DEAD_STATES and not rec.get("filled"):
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


def _fill_price(status: dict, order_id: str) -> tuple[float, int]:
    """Средняя цена и объём РЕАЛЬНОГО исполнения заявки по таблице сделок QUIK.
    Лимитная цена дочерней заявки — не цена сделки: она маркетабельная и
    исполняется по встречным заявкам, часто лучше своего лимита."""
    num, vol = 0.0, 0
    for t in ((status.get("quik") or {}).get("trades") or []):
        if str(t.get("order_num") or "") != str(order_id):
            continue
        try:
            q, px = int(t.get("qty") or 0), float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if q > 0 and px > 0:
            num += px * q
            vol += q
    return (num / vol, vol) if vol else (0.0, 0)


def _match_trades(status: dict, so: SmartOrder, window_ms: int = 900_000) -> tuple[float, int]:
    """Запасной путь, когда номера заявки нет: стор заявок живёт В ПАМЯТИ и
    обнуляется рестартом STL, а сработала заявка раньше. Ищем в сделках QUIK
    РУЧНОЙ класс (тег пустой — роботные и recon исключены) по тому же инструменту
    и стороне рядом со временем срабатывания, группируем по номеру заявки и берём
    группу, ближайшую по времени, с подходящим объёмом.

    Окно 15 минут, а не 3: сработавшая заявка может ЖДАТЬ встречный объём.
    06.08.2026 заявка ушла в 06:55, а налилась в 06:59-07:00 на открытии — при
    трёхминутном окне сопоставление не нашло ничего, и книга осталась с одним
    контрактом вместо девятнадцати."""
    groups: dict[str, list] = {}
    for t in ((status.get("quik") or {}).get("trades") or []):
        if (t.get("tag") or "") != "" or t.get("sec") != so.code:
            continue
        if str(t.get("side") or "").lower() != so.side:
            continue
        ts = int(t.get("ts_ms") or 0)
        if not ts or abs(ts - so.fired_ms) > window_ms:
            continue
        groups.setdefault(str(t.get("order_num") or ts), []).append(t)
    best, best_dt = None, None
    for rows in groups.values():
        vol = sum(int(r.get("qty") or 0) for r in rows)
        if vol <= 0 or vol > so.qty:
            continue                      # чужая заявка большего объёма — не наша
        dt = min(abs(int(r.get("ts_ms") or 0) - so.fired_ms) for r in rows)
        if best_dt is None or dt < best_dt:
            best, best_dt = rows, dt
    if not best:
        return 0.0, 0
    vol = sum(int(r.get("qty") or 0) for r in best)
    num = sum(float(r.get("price") or 0) * int(r.get("qty") or 0) for r in best)
    return (num / vol, vol) if vol else (0.0, 0)


def _track_fills(book: SmartOrderBook, ost: Any, store: Any, agent: str) -> bool:
    """Дописать сработавшим заявкам ЦЕНУ СДЕЛКИ. Оператору нужен факт («купил по
    88 340»), а не уровень срабатывания — по уровню нельзя понять, во что обошёлся
    вход. Цена берётся из таблицы сделок QUIK по номеру заявки; если сделок ещё
    нет (заявка только ушла), пробуем на следующем проходе."""
    now = so_mod.now_ms()
    want = [o for o in book.orders
            if o.status in ("fired", "orphaned") and o.fired_client_id
            and o.fired_qty < o.qty and now - o.fired_ms < _FILL_TRACK_MS]
    if not want:
        return False
    by_cid = {d["client_id"]: d for d in ost.working_orders(agent)}
    status = (store.agent_status(agent) if store is not None else None) or {}
    steps = _price_steps(store, agent) if store is not None else {}
    dirty = False
    for so in want:
        rec = by_cid.get(so.fired_client_id) or {}
        oid = rec.get("order_id")
        px, vol = _fill_price(status, oid) if oid else (0.0, 0)
        if px <= 0:
            px, vol = _match_trades(status, so)
        if px > 0:
            px = so_mod.quantize(px, steps.get(so.code, 0.0), so.side)
        if px > 0 and (px, vol) != (so.fired_price, so.fired_qty):
            so.fired_price, so.fired_qty = px, vol
            dirty = True
            log.info("smart_order.fill_price", so_id=so.so_id, price=px, qty=vol)
            # Защитные заявки ставились по цене дочерней (маркетабельной) заявки;
            # теперь известна ФАКТИЧЕСКАЯ цена входа — двигаем уровни на неё, пока
            # заявки ещё взведены.
            if so_mod.rebase_protective(book.orders, so, px):
                log.info("smart_order.protective_rebased", parent=so.so_id, entry=px)
    return dirty


def _snap_entries_to_grid(book: SmartOrderBook, steps: dict[str, float]) -> bool:
    """Цена входа и уровни стопа/тейка — ТОЛЬКО на сетке шага цены.

    Средневзвешенная по сделкам (4 x 87440 + 1 x 87450 = 87442) у RI с шагом 10
    в природе не бывает, а стоп 86942 и тейк 88442 от неё несимметричны: первый
    фактически срабатывает на 86940, второй на 88450. Вход округляем в сторону
    ХУДШЕЙ для позиции цены (покупка вверх, продажа вниз), уровни пересчитываем
    от него. Идемпотентно: догоняет и уже записанные в книгу входы."""
    dirty = False
    for p in book.orders:
        step = steps.get(p.code, 0.0)
        if p.status not in ("fired", "orphaned") or p.fired_price <= 0 or step <= 0:
            continue
        q = so_mod.quantize(p.fired_price, step, p.side)
        if q != p.fired_price:
            p.fired_price = q
            dirty = True
        if so_mod.rebase_protective(book.orders, p, p.fired_price):
            dirty = True
            log.info("smart_order.protective_snapped", parent=p.so_id, entry=p.fired_price)
    return dirty


def _revive_false_orphans(book: SmartOrderBook) -> bool:
    """Сирота с найденным исполнением — не сирота: ребёнок жил и налился."""
    dirty = False
    for so in book.orders:
        if so.status == "orphaned" and so.fired_qty > 0:
            so.status, so.note = "fired", ""
            dirty = True
            log.warning("smart_order.orphan_revived", so_id=so.so_id,
                        price=so.fired_price, qty=so.fired_qty)
    return dirty


async def _alert_reject(srv: Any, agent: str, so: SmartOrder, reason: str) -> None:
    """Отказ умной заявки лимитами — оператору немедленно. Молчание тут уже
    стоило невзведённой позиции: заявка лежала со статусом error, человек не знал."""
    fwd = getattr(srv, "alert_forwarder", None)
    if fwd is None:
        return
    await fwd.forward(
        {
            "severity": SEVERITY_CRITICAL,
            "code": f"smart_order_rejected/{so.so_id}",
            "message": f"{so.code} {so.side.upper()} qty={so.qty}: {reason}",
            "raised_at_unix_ms": so_mod.now_ms(),
        },
        agent,
    )


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
    dirty_meta = _mark_orphans(book, ost, agent, so_mod.now_ms())
    dirty_meta = _track_fills(book, ost, store, agent) or dirty_meta
    dirty_meta = _revive_false_orphans(book) or dirty_meta
    steps_all = _price_steps(store, agent)
    dirty_meta = _snap_entries_to_grid(book, steps_all) or dirty_meta
    # Защита открытой позиции переезжает в терминал: он держит стоп-заявку сам и
    # переживает падение STL. Порядок важен - сначала уточнённая цена входа
    # (_track_fills/_snap_entries_to_grid), потом передача от неё.
    dirty_meta = _handover_to_terminal(book, steps_all, ost, srv, agent, so_mod.now_ms()) or dirty_meta
    for parent in _track_native(book, store, agent, so_mod.now_ms()):
        dirty_meta = True
        await _alert_reject(srv, agent, parent,
                            "терминал не принял стоп-заявку защиты: защиту ведёт STL")
    if dirty_meta:
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

    # Торгует ли биржа ПРЯМО СЕЙЧАС — по оракулу расписания, не по свежести кадра.
    session_open = (getattr(state, "market_session", None) or {}).get("open")

    for code in book.codes():
        t = store.tick(code, agent) or {}
        trail_before = [(o.so_id, o.activated, o.peak) for o in book.orders]
        actions = so_mod.evaluate(
            book.orders, code,
            last=float(t.get("last") or 0), bid=float(t.get("bid") or 0),
            ask=float(t.get("ask") or 0),
            tick_ms=int(t.get("received_at_unix_ms") or (now if t else 0)),
            now_ms=now, filled_client_ids=filled,
            step=steps.get(code, 0.0), session_open=session_open,
        )
        # Пик трейла держался только в памяти: рестарт STL терял его и заявка
        # начинала вести откат от новой, случайной точки. Сохраняем сдвиг.
        if trail_before != [(o.so_id, o.activated, o.peak) for o in book.orders]:
            dirty = True
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
                # Защитная пара после входа (если оператор её заказал): trail и
                # on_fill только ВХОДЯТ и после срабатывания забывают про позицию —
                # без стопа выходить нечем, а без тейка некому забрать прибыль.
                # Обе в одной связке OCO: сработала одна — вторая снимается, иначе
                # она открыла бы позицию в обратную сторону.
                for miss in so_mod.level_violations(so, act.price):
                    # Уровень оказался не с той стороны от входа. Оператор решил
                    # (18.09): ничего не выдумывать, сказать человеку сразу.
                    log.warning("smart_order.level_skipped", parent=so.so_id, reason=miss)
                    await _alert_reject(srv, agent, so, miss)
                for child in so_mod.protective_children(so, act.price, now):
                    book.orders.append(child)
                    log.info("smart_order.protective", parent=so.so_id, kind=child.kind,
                             so_id=child.so_id, side=child.side,
                             trigger=child.trigger_price, oco=child.oco_group)
            except LimitError as exc:
                so.status = "error"
                so.note = f"отклонено лимитами: {exc}"
                log.warning("smart_order.rejected", so_id=so.so_id, error=str(exc))
                await _alert_reject(srv, agent, so, str(exc))
    # expiry flips status inside evaluate() without producing an action
    if dirty or any(o.status == "expired" for o in active):
        book.save()


# ---- авто-догон trail_tp (пробой уровня пропущен, пока STL лежал) ----
# Догонялка активации по ISS-свечам (задержка ~15 мин) снесена 15.09.2026: она
# активировала trail_tp задним числом, и заявка тут же купила 5 RIU6 по 87 450
# при минимуме 86 860. Опоздавший вход хуже пропущенного; исполнение переезжает
# на VDS (docs/design/execution-module.md).


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
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — watcher must survive
            log.warning("smart_orders.watch_failed", error=str(exc))
