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
from trader.quik import terminal
from trader.quik import blind_quarantine as bq
from trader.quik import exec_profiles
from trader.quik import so_journal
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


def _created_detail(so: SmartOrder) -> str:
    """Человеческая расшифровка заявки для журнала: без неё строка «создана»
    не говорит, ЧТО именно оператор поставил."""
    what = {"sl": "стоп", "tp": "тейк", "trail_tp": "следящий тейк",
            "trail_sl": "подтягивающая", "on_fill": "по исполнению"}.get(so.kind, so.kind)
    side = "покупка" if so.side == "buy" else "продажа"
    parts = [f"{what}: {side} {so.qty} {so.code}"]
    if so.trigger_price:
        parts.append(f"уровень {so.trigger_price:g}")
    if so.trail_offset:
        parts.append(f"откат {so.trail_offset:g} п.")
    for label, value, unit in (("стоп после входа", so.sl_offset, "п."),
                               ("тейк после входа", so.tp_offset, "п."),
                               ("подтягивающая после входа", so.trail_after, "п."),
                               ("откат следящего тейка", so.tp_trail, "п."),
                               ("стоп уровнем", so.sl_price, ""),
                               ("тейк уровнем", so.tp_price, "")):
        if value:
            parts.append(f"{label} {value:g} {unit}".strip())
    return ", ".join(parts)


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
    # Гарантированный выход стопа: сколько стоим у планки, сколько идём за ценой
    # и с каким шагом переставляем. Дефолты 10/10/2 с — выход за 20 секунд.
    # Профиль исполнения (вход и выход): aggressive | active | normal.
    # Пустой = штатный. Секунды ниже, если заданы, перекрывают профиль.
    esc_profile: str = ""
    esc_hold_sec: int = 0
    esc_chase_sec: int = 0
    esc_chase_every_sec: int = 0
    # Коридор: верхняя граница двумя точками (время в мс, цена), нижняя параллельна
    # ей и проходит через c_low в момент c_t1_ms; выход за стенку на c_stop_pts —
    # закрытие; c_flips_max = сколько переворотов разрешено (0 — без предела).
    c_t1_ms: int = 0
    c_p1: float = 0.0
    c_t2_ms: int = 0
    c_p2: float = 0.0
    c_low: float = 0.0
    c_low2: float = 0.0            # треугольник: нижняя граница в момент c_t2_ms
    # Сетка «радиация»: шаг в пунктах, сколько уровней вниз (покупки) и вверх
    # (продажи), объём на уровень, стоп за последним уровнем. Цена постановки
    # берётся из рынка — её не спрашиваем, чтобы сетка не разъехалась с рынком
    # между вводом и отправкой формы.
    g_step: float = 0.0
    g_buys: int = 0
    g_sells: int = 0
    g_lot: int = 0
    g_stop_pts: float = 0.0
    # Защита сетки. Умолчания из бэктеста (решение оператора 02.10.2026):
    # три исполненных уровня ИЛИ уход цены от базы на 0.25%. 0 выключает условие.
    g_trig_fills: int = 3
    g_trig_move_pct: float = 0.25
    # Перевзведение после выхода по защите, минуты. 0 = выход окончателен.
    g_rearm_min: float = 0.0
    # Сколько касаний триггерной цены С ОДНОЙ СТОРОНЫ нужно, чтобы защита
    # сработала. 1 = на первом же; больше — одиночный выброс сетку не снимет.
    g_trig_touches: int = 1
    # Цель прибыли сетки в рублях: достигнута — уровни сняты, позиция закрыта
    # рыночной, сетка закончена (перевзведение на неё не распространяется).
    # 0 = без цели.
    g_tp_rub: float = 0.0
    # Сколько ближайших уровней с каждой стороны стоит в QUIK; остальные ждут в
    # STL. 0 = выставлять все.
    g_window: int = 5
    c_stop_pts: float = 0.0
    c_flips_max: int = 0
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
        esc_profile=str(body.esc_profile or ""),
        esc_hold_sec=int(body.esc_hold_sec), esc_chase_sec=int(body.esc_chase_sec),
        esc_chase_every_sec=int(body.esc_chase_every_sec),
        c_t1_ms=int(body.c_t1_ms), c_p1=float(body.c_p1),
        c_t2_ms=int(body.c_t2_ms), c_p2=float(body.c_p2),
        c_low=float(body.c_low), c_low2=float(body.c_low2),
        c_stop_pts=float(body.c_stop_pts),
        g_trig_fills=int(body.g_trig_fills), g_trig_move_pct=float(body.g_trig_move_pct),
        g_rearm_min=float(body.g_rearm_min), g_trig_touches=int(body.g_trig_touches),
        g_tp_rub=float(body.g_tp_rub), g_cash_on=True, g_window=int(body.g_window),
        g_step=float(body.g_step), g_buys=int(body.g_buys), g_sells=int(body.g_sells),
        g_lot=int(body.g_lot), g_stop_pts=float(body.g_stop_pts),
        c_flips_max=int(body.c_flips_max), c_qty=int(body.qty),
        note=body.note, created_ms=so_mod.now_ms(),
    )
    # Рыночная цена инструмента даёт валидации точку отсчёта: без неё ЦЕНУ,
    # введённую в поле пунктов, не отличить от больших пунктов (заявка без уровня
    # активации собственного trigger_price не имеет).
    if so.kind == "grid" and so.g_base <= 0:
        # База сетки — ЦЕНА РЫНКА в момент постановки, а не поле формы: между
        # вводом и отправкой цена уходит, и сетка встала бы вокруг устаревшей
        # точки. Нет цены — нет сетки, гадать тут нечем.
        so.g_base = _market_price(request, so.code)
    silent = _silent_instrument(request, so.code)
    if silent:
        raise HTTPException(status_code=422, detail=silent)
    err = so.validate(_market_price(request, so.code))
    if not err and so.kind == "grid":
        err = so_mod.grid_step_error(so.g_step, _exchange_step(request, so.code))
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
        so_journal.record("cancelled", old, so_journal.OPERATOR,
                          f"снят подтягивающей {so.so_id}: два стопа на одной позиции")
    book.add(so)
    if superseded:
        book.save()
    log.info("smart_order.created", so_id=so.so_id, kind=so.kind, code=so.code,
             side=so.side, qty=so.qty, trigger=so.trigger_price)
    so_journal.record("created", so, so_journal.OPERATOR,
                      _created_detail(so), now_ms=so.created_ms)
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
    now = so_mod.now_ms()
    sched = getattr(request.app.state, "market_schedule", None)
    out = []
    for o in book.orders:
        d = asdict(o)
        if o.kind in ("corridor", "triangle"):
            # ТЕКУЩИЕ стенки считает движок, а не панель. Иначе геометрию
            # пришлось бы повторять на фронте, и две реализации одной прямой
            # разъехались бы — вопрос в том, когда, а не случится ли это.
            low, top = so_mod.corridor_bounds(o, now, sched)
            d["c_now"] = {"low": round(low, 4), "top": round(top, 4),
                          "width": round(top - low, 4), "ts_ms": now}
        if o.kind == "grid":
            # НАБОР ДЛЯ ЗАЩИТЫ СЧИТАЕТ ДВИЖОК, а не панель (просьба ui-ux 03.10.2026,
            # и она верна): правило точки отсчёта повторять на экране значило бы
            # завести вторую реализацию, которая разошлась бы с первой. None —
            # защита по уровням выключена. g_guard_from — точка, от которой идёт счёт.
            d["g_guard_levels"] = so_mod.grid_guard_levels(o)
            d["g_guard_from"] = so_mod.grid_guard_base_now(o)
        out.append(d)
    return {"orders": out,
            "session": {"open": sess.get("open"), "phase": sess.get("phase", "")}}


@router.get("/exec-profiles")
async def exec_profiles_list(request: Request):
    """Профили исполнения: три штатных плюс правки оператора с диска."""
    _auth(request)
    return {"profiles": exec_profiles.load(), "default": exec_profiles.DEFAULT_PROFILE}


class ExecProfilesBody(BaseModel):
    profiles: dict


@router.put("/exec-profiles")
async def exec_profiles_save(body: ExecProfilesBody, request: Request):
    """Сохранить профили. Штатные три не удаляются: заявка, сославшаяся на
    исчезнувший профиль, осталась бы без доведения молча."""
    _auth(request)
    merged = exec_profiles.load()
    for name, cfg in (body.profiles or {}).items():
        if isinstance(cfg, dict):
            merged[name] = cfg
    for name in exec_profiles.DEFAULTS:
        merged.setdefault(name, exec_profiles.DEFAULTS[name])
    exec_profiles.save(merged)
    saved = exec_profiles.load()
    log.info("exec_profiles.saved", names=sorted(saved))
    return {"ok": True, "profiles": saved}


@router.delete("/{so_id}")
async def cancel_order(so_id: str, request: Request):
    _auth(request)
    book = _book(request)
    so = book.get(so_id)
    if so is None:
        raise HTTPException(status_code=404, detail="Нет такой умной заявки.")
    if so.status not in ("armed", "native"):
        raise HTTPException(status_code=409, detail=f"Заявка уже {so.status}.")
    # Снимаем нативную запись ПО ФАКТУ таблицы терминала, а не по статусу книги.
    # 29.09.2026: STL решил, что терминал стоп-заявку не принял (подтверждение не
    # пришло за 20 с — в 06:45 торги ещё не шли), и поставил статус armed. Терминал
    # её ПРИНЯЛ. Дальше одна заявка охранялась дважды: стоп сработал в терминале на
    # 40 контрактов, а сторож STL, не знавший о нём, продал ещё 40 — позиция
    # оператора перевернулась с +40 в −40 без его решения. При отмене та же слепота
    # оставляла живую стоп-заявку в QUIK: книга писала «отменена», терминал —
    # «активна». Статус книги — мнение, таблица терминала — факт.
    if so.status == "native":
        _kill_native(request, so)
    else:
        _kill_native_by_table(request, so)
    # Коридор, треугольник и сетка ДЕРЖАТ заявки в стакане QUIK — снять умную
    # заявку, не сняв их, значит оставить в рынке то, что оператор отменил
    # (инцидент 01.10.2026: две заявки коридора пережили его отмену).
    _withdraw_resting(request, so)
    so.status = "cancelled"
    so.note = (so.note + " " if so.note else "") + "отменена оператором"
    book.save()
    so_journal.record("cancelled", so, so_journal.OPERATOR, "снята оператором")
    log.info("smart_order.cancelled", so_id=so_id, kind=so.kind, code=so.code,
             side=so.side, qty=so.qty)
    return {"ok": True, "so_id": so_id}


def _cancel_resting(srv: Any, store: Any, agent: str, so: SmartOrder,
                    cids: set[str], work: dict[str, dict]) -> tuple[int, list[dict]]:
    """Снять ВСЁ, что эта умная заявка держит в стакане. ЕДИНСТВЕННЫЙ путь снятия.

    Два прохода, и второй важнее первого:

    1. по своим записям (client_id) — работает, пока целы склад заявок STL и карта
       агента, то есть пока не перезапускали ни один из двух процессов;
    2. по ТАБЛИЦЕ ЗАЯВОК ТЕРМИНАЛА — работает всегда, потому что таблица живёт в
       QUIK. Снятие по номеру заявки и инструменту не требует ничьей памяти.

    Второй проход написан после 01.10.2026: перезапуск агента обнулил его карту, 24
    заявки сетки стали неснимаемыми, STL слал отмену, агент не находил заявку, книга
    писала «снято», а заявки продолжали ТОРГОВАТЬ и набрали оператору лишние
    контракты. Любой путь, объявляющий умную заявку снятой, обязан идти ЧЕРЕЗ ЭТУ
    ФУНКЦИЮ: раньше их было два (отмена оператором и стоп за краем сетки), и второй
    снимал только известное складу, после чего ставил g_done и забывал остальное
    навсегда.

    Возвращает (сколько отправлено снятий, строки терминала, о которых записей не было).
    """
    killed, sent, extra = 0, set(), []
    for cid in sorted(cids):
        rec = work.get(cid) or {}
        # Снимаем и ту, которой ещё нет в складе: номер мог не прийти, но заявка уже
        # в пути. Пустой order_id агент разрешит по client_id сам.
        if rec.get("state") in ("cancelled", "filled", "rejected"):
            continue
        num = str(rec.get("order_id") or "")
        srv.enqueue_order(agent, order_msgs.build_cancel_order(
            client_id=cid, order_id=num, code=so.code))
        if num:
            sent.add(num)
        killed += 1
    for row in terminal.by_smart_order(store, agent).get(so.so_id, []):
        if row["num"] in sent:
            continue
        # CLIENT_ID ЗДЕСЬ НЕ ДОЛЖЕН НИ С ЧЕМ СОВПАДАТЬ. Сначала стояло
        # f"so:{so.so_id}" — а это client_id ВЫСТРЕЛИВШЕЙ заявки этой же умной
        # заявки (см. :2374). Агент при неизвестном номере ищет по карте client_id
        # (resolveForCancel: сначала byOrder, потом byClient) и снял бы ВЫСТРЕЛ
        # вместо той строки, о которой речь: нужная заявка осталась бы торговать, а
        # посторонняя умерла. Номер заявки в имени делает совпадение невозможным.
        srv.enqueue_order(agent, order_msgs.build_cancel_order(
            client_id=f"op:kill:{row['num']}", order_id=row["num"], code=row["sec"]))
        sent.add(row["num"])
        killed += 1
        extra.append(row)
    return killed, extra


def _withdraw_resting(request: Request, so: SmartOrder) -> int:
    """Снять из QUIK заявки, которые эта умная заявка ДЕРЖИТ В СТАКАНЕ.

    ИНЦИДЕНТ 01.10.2026. Оператор нажал «изменить» на коридоре, интерфейс снял
    умную заявку — а две её заявки остались ЖИТЬ В ТЕРМИНАЛЕ: покупка 1 по 85450 и
    продажа 1 по 86210 при рынке 85700. Книга писала «отменена», QUIK держал обе.
    Снял руками по номерам, налиться не успело.

    Корень мой: 30.09 я научил коридор, треугольник и сетку СТОЯТЬ В СТАКАНЕ
    заранее, а путь отмены не расширил. Он снимал только НАТИВНЫЕ СТОП-ЗАЯВКИ
    (_kill_native/_kill_native_by_table), потому что до 30.09 ничего другого
    умная заявка в терминале и не держала. Новая возможность молча обошла старую
    уборку — и отмена перестала быть отменой.

    Ключи `flip:` и `cross:` в c_live/g_live это БУХГАЛТЕРИЯ состояния, а не
    client_id: снимать по ним нечего, и трогать их нельзя.
    """
    state = request.app.state
    srv = getattr(state, "quik_server", None)
    ost = getattr(state, "quik_order_store", None)
    store = getattr(state, "quik_store", None)
    if srv is None or ost is None or store is None:
        return 0
    try:
        agent = resolve_agent(store, None)
    except Exception:  # noqa: BLE001 — нет агента, снимать нечем
        return 0
    # ОТБИРАЕМ ПО ЗНАЧЕНИЮ, А НЕ ПО ИМЕНИ КЛЮЧА. Сначала я исключал служебные
    # ключи по префиксам (flip:, cross:) — и пропустил moved:<стенка>, где лежит
    # ВРЕМЯ последней перестановки, то есть число. Оно попало в список «заявок»,
    # и sorted() сравнил число со строкой: 01.10.2026 отмена коридора оператором
    # упала с TypeError, заявка осталась жить в QUIK, а человек думал, что снял.
    # Признак заявки — её идентификатор, то есть СТРОКА; перечислять служебные
    # префиксы значит обещать помнить про каждый новый, а этого обещания я уже
    # не сдержал.
    live_cids = {
        cid for book_field in (so.c_live or {}, so.g_live or {})
        for cid in book_field.values()
        if isinstance(cid, str) and cid
    }
    work = {d.get("client_id"): d for d in ost.working_orders(agent)}
    killed, extra = _cancel_resting(srv, store, agent, so, live_cids, work)
    for row in extra:
        so_journal.record("resting_withdrawn", so, so_journal.WATCHER,
                          f"снята заявка {row['num']} из ТАБЛИЦЫ ТЕРМИНАЛА "
                          f"({row['side']} {row['balance']} по {row['price']:g}): "
                          "своих записей о ней не было")
    if not killed:
        return 0
    if killed:
        so_journal.record("resting_withdrawn", so, so_journal.OPERATOR,
                          f"снято заявок из стакана: {killed} ({', '.join(sorted(live_cids))})")
        log.info("smart_order.resting_withdrawn", so_id=so.so_id, killed=killed)
    so.c_live, so.g_live = {}, {}
    return killed


def _kill_native_by_table(request: Request, so: SmartOrder) -> None:
    """Снять нативную запись, о которой книга не знает: ищем её в таблице
    стоп-заявок терминала по нашему же тегу. Нет записи — тихо выходим: это
    обычный случай заявки, которую терминалу не отдавали."""
    state = request.app.state
    store = getattr(state, "quik_store", None)
    srv = getattr(state, "quik_server", None)
    if store is None or srv is None:
        return
    try:
        agent = resolve_agent(store, None)
    except Exception:  # noqa: BLE001 — нет агента, снимать нечем
        return
    book = _book(request)
    ids = {so.so_id} | {c.so_id for c in book.orders if c.parent_id == so.so_id}
    if so.parent_id:
        ids.add(so.parent_id)
    rows = _stop_rows_live(store, agent)
    for sid in ids:
        row = rows.get(sid)
        num = _stop_num(row or {})
        if not num:
            continue
        srv.enqueue_order(agent, order_msgs.build_kill_stop_order(
            f"so:{sid}", num, so.code))
        so.note = (so.note + " " if so.note else "") +             f"снята и в терминале (стоп-заявка {num}, книга её не числила)"
        so_journal.record("native_killed", so, so_journal.OPERATOR,
                          f"снята забытая книгой стоп-заявка {num}")
        log.warning("smart_order.native_killed_by_table", so_id=so.so_id,
                    found_under=sid, stop_num=num)


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
    parent = book.get(so.parent_id) if so.parent_id else so
    if parent is not None:
        parent.native_state = "done"
    log.info("smart_order.native_killed", so_id=so.so_id, stop_num=holder.native_stop_num)


class ProfileBody(BaseModel):
    esc_profile: str


class ExitOnlyBody(BaseModel):
    on: bool = True


@router.post("/{so_id}/exit-only")
async def set_exit_only(so_id: str, body: ExitOnlyBody, request: Request):
    """Режим «ТОЛЬКО НА ВЫХОД» для умной заявки: закрыть свою позицию БЕЗ УБЫТКА
    и больше ничего не открывать. Снимается тем же переключателем.

    Требование оператора 02.10.2026. У роботов такой режим есть с 28.07.2026 и
    служит тому же: вывести из боя, не обрывая сделку (экспирация, развод
    встречных заявок, подготовка к остановке). Отмена заявки для этого не годится
    — она снимает заявки из стакана ВМЕСТЕ с открытой позицией, и выходить будет
    некому.

    Отличие от роботов: там выход идёт по сигналу стратегии и цена не проверяется,
    здесь выход идёт уровнями, поэтому цена проверяется — закрываем только не хуже
    средней входа. КОМИССИЯ В ЭТОМ СРАВНЕНИИ НЕ УЧТЕНА: равенство средней означает
    ноль ДО сборов. Нужен запас — заведём отступ в пунктах отдельно.
    """
    _auth(request)
    book = _book(request)
    so = book.get(so_id)
    if so is None:
        raise HTTPException(status_code=404, detail="Нет такой умной заявки.")
    if so.kind not in ("grid", "corridor", "triangle"):
        raise HTTPException(status_code=422, detail=(
            f"«Только на выход» имеет смысл лишь у заявок со своей позицией "
            f"(радиация, коридор, треугольник), а {so.so_id} это {so.kind}: "
            "защитная заявка и так только закрывает."))
    lifted = so.exit_only and not bool(body.on)
    so.exit_only = bool(body.on)
    if lifted and so.kind == "grid":
        # Решение оператора выше защиты: она считает набор заново от этой точки,
        # а не возвращает режим через проход (см. grid_guard_rebase).
        so_mod.grid_guard_rebase(so, _market_price(request, so.code))
    pos = so.g_pos if so.kind == "grid" else so.c_pos
    avg = so.g_avg if so.kind == "grid" else so.c_avg
    book.save()
    so_journal.record(
        "exit_only", so, so_journal.OPERATOR,
        (f"включён режим только на выход: закрываем {pos:+d} по цене не хуже "
         f"{avg:g}, новых не открываем" if so.exit_only and pos
         else "включён режим только на выход: позиции нет, открывать не будем"
         if so.exit_only else "режим только на выход снят оператором: заявка работает как "
         "обычно, защита считает набор заново от позиции " + f"{pos:+d}"
         if lifted and so.kind == "grid"
         else "режим только на выход снят: заявка работает как обычно"))
    log.info("smart_order.exit_only", so_id=so_id, kind=so.kind, on=so.exit_only,
             pos=pos, avg=avg)
    return {"ok": True, "so_id": so_id, "exit_only": so.exit_only,
            "position": pos, "avg": avg}


@router.post("/{so_id}/profile")
async def set_profile(so_id: str, body: ProfileBody, request: Request):
    """Сменить профиль исполнения у ВЗВЕДЁННОЙ заявки.

    Без этой ручки профиль можно было задать только при постановке, а сменить —
    лишь пересоздав заявку. У следящей это стирает пик и факт активации, то есть
    ради настройки исполнения пришлось бы терять состояние слежения, набранное
    за часы.
    """
    _auth(request)
    book = _book(request)
    so = book.get(so_id)
    if so is None:
        raise HTTPException(status_code=404, detail="Нет такой умной заявки.")
    if so.status not in ("armed", "native"):
        raise HTTPException(status_code=409, detail=f"Заявка уже {so.status}.")
    name = (body.esc_profile or "").strip()
    known = exec_profiles.load()
    if name and name not in known:
        raise HTTPException(status_code=422,
                            detail=f"Нет профиля «{name}». Есть: {', '.join(sorted(known))}.")
    was = so.esc_profile or "(штатный)"
    so.esc_profile = name
    # Явные секунды перекрывали бы профиль — снимаем их, иначе смена профиля
    # ничего бы не изменила, а человек считал бы, что изменила.
    so.esc_hold_sec = so.esc_chase_sec = so.esc_chase_every_sec = 0
    book.save()
    prof = exec_profiles.resolve(so.esc_profile)
    so_journal.record("profile", so, so_journal.OPERATOR,
                      f"профиль исполнения: {was} -> {prof['name']} ({prof['title']})")
    log.info("smart_order.profile_changed", so_id=so_id, was=was, now=prof["name"])
    return {"ok": True, "so_id": so_id, "profile": prof}


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
    so_journal.record("activated", so, so_journal.OPERATOR,
                      f"активирована вручную, пик {peak:g}")
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


def _exchange_step(request: Request, code: str) -> float:
    """Шаг цены инструмента из фида параметров агента; 0 = неизвестен."""
    store = getattr(request.app.state, "quik_store", None)
    if store is None:
        return 0.0
    try:
        return _price_steps(store, resolve_agent(store, None)).get(code, 0.0)
    except Exception:  # noqa: BLE001 - проверка не должна ронять взведение
        return 0.0


def _silent_instrument(request: Request, code: str) -> str | None:
    """Инструмент молчит (истёк, снят с торгов, не подключён) - взводить нельзя."""
    store = getattr(request.app.state, "quik_store", None)
    if store is None:
        return None
    try:
        agent = resolve_agent(store, None)
        ages = store.tick_ages_ms(so_mod.now_ms(), agent)
        if not ages:
            return None          # кадров нет вовсе (агент молчит) - судить не по чему
        return so_mod.silent_code(code, ages)
    except Exception:  # noqa: BLE001 - проверка не должна ронять взведение
        return None


def _price_limits(store: Any, agent: str) -> dict[str, tuple[float, float]]:
    """code -> (нижняя планка, верхняя планка) цены дня из параметров QUIK.

    Биржа отвергает заявку за планкой, а MOEX двигает планки по своему
    расписанию в зависимости от волатильности. Для сетки это половина работы:
    уровень за планкой нельзя выставить, его ДЕРЖАТ у себя и ждут расширения
    (оператор, 30.09.2026).

    Нули = границы неизвестны (скрипт старше 2026.09.30 их не отдаёт). Тогда НЕ
    ограничиваем: молчащий параметр не имеет права останавливать торговлю.
    """
    out: dict[str, tuple[float, float]] = {}
    p = store.params(agent) if store else None
    for row in (p or {}).get("rows", []) or []:
        try:
            lo = float(row.get("price_min") or 0)
            hi = float(row.get("price_max") or 0)
        except (TypeError, ValueError):
            continue
        if row.get("code") and (lo > 0 or hi > 0):
            out[str(row["code"])] = (lo, hi)
    return out


def price_within_limits(price: float, limits: tuple[float, float] | None) -> bool:
    """Пройдёт ли цена планки. Неизвестные границы — пропускаем: см. _price_limits."""
    if not limits or price <= 0:
        return True
    lo, hi = limits
    if lo > 0 and price < lo:
        return False
    return not (hi > 0 and price > hi)


def _point_coefs(store: Any, agent: str) -> dict[str, float]:
    """code -> рублей за пункт из фида параметров (coef агента или step_cost/шаг).

    Нет инструмента в фиде — нет и коэффициента, и всё, что считает деньги, обязано
    молчать, а не подставлять единицу: пункт не рубль (карточка робота уже
    показывала −11 ₽ вместо −5585 ₽ на чужом коэффициенте)."""
    from trader.quik.algo_ledger import point_values
    params = getattr(store, "params", None)
    try:
        return point_values(params(agent) if params else None)
    except Exception:  # noqa: BLE001 — нет фида = нет денег, а не падение сторожа
        return {}


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
# Через сколько повторить попытку отдать защиту терминалу после его отказа.
# Пять минут: достаточно редко, чтобы не долбить транзакциями, и достаточно
# часто, чтобы заявка, отвергнутая до открытия торгов, попала под охрану
# терминала в первые же минуты сессии.
_NATIVE_RETRY_MS = 5 * 60 * 1000
# СОСТОЯНИЕ ЗАЯВКИ ПРОВЕРЯЕМ БЕЛЫМ СПИСКОМ, А НЕ ЧЁРНЫМ.
#
# 02.10.2026 сетка «радиация» не выставила на открытии НИ ОДНОГО уровня и молчала,
# пока оператор не спросил. Причина: ночью, после закрытия вечерней сессии, брокер
# отбил постановки («[GW][3] Сейчас эта сессия не идёт»), записи уровней ушли в
# состояние `expired` — «QUIK заявку не зарегистрировал, в работе её нет» (см.
# trader/quik/orders.PENDING_RECONCILE_MS). А чёрный список мёртвых состояний знал
# только cancelled и rejected, поэтому проверка «уровень стоит в стакане» видела
# `expired` с остатком 1 и отвечала ДА. Сетка считала, что все 25 уровней стоят, и
# не ставила ничего — восемь часов, с 00:05 до открытия.
#
# Чёрный список забывает новое состояние молча, и это второй раз: сначала он забыл
# служебный ключ в live, теперь состояние заявки. Поэтому вопрос «работает ли
# заявка» задаём перечислением РАБОЧИХ состояний — их ровно три, и они те же, что
# в складе заявок (orders._WORKING_STATES).
_WORKING_STATES = ("pending", "active", "partial")
_DEAD_STATES = ("cancelled", "rejected", "expired")


_FIN_KEEP = 400      # сколько засчитанных заявок помнить; дальше стираются самые старые


def _mark_counted(live: dict, num: str) -> None:
    """Запомнить: налив заявки с этим НОМЕРОМ уже засчитан в позицию.

    ТАБЛИЦА ТЕРМИНАЛА ОТСТАЁТ ОТ СКЛАДА НА СЕКУНДЫ, и строка уже исполненной заявки
    какое-то время выглядит живой. 03.10.2026 на GZZ6 это посчитало ОДИН налив
    ДВАЖДЫ: заявка уровня исполнилась в 18:55:45 и была учтена складом, а таблица
    ещё показывала её активной — сторож «подхватил» её как свежую стоящую, и когда
    строка обновилась на «исполнена», засчитал тот же налив второй раз. Позиция
    сетки ушла на 7 контрактов (+3 в книге против +10 по сделкам).

    «Склад знает заявку закрытой» тут НЕ ТО ЖЕ, что «налив засчитан»: после рестарта
    склад знает заявку, а связь сетки с ней уже снята, и посчитать её может только
    таблица (см. тест «ровно один путь учёта»). Поэтому помечаем именно факт учёта.
    """
    if not num:
        return
    live[f"fin:{num}"] = 1
    fin = [k for k in live if k.startswith("fin:")]
    for k in fin[:max(0, len(fin) - _FIN_KEEP)]:
        live.pop(k, None)


def _is_working(rec: dict | None) -> bool:
    """Заявка РАБОТАЕТ: QUIK её принял и она ещё не кончилась."""
    return bool(rec) and str(rec.get("state") or "") in _WORKING_STATES
# OrderStore живёт в памяти: рестарт STL стирает записи. Сработавшая ДО старта
# процесса заявка отсутствует в сторе не потому, что умерла — судить о ней нельзя
# (26.07 ложный orphaned звал оператора перевзвести УЖЕ исполненный выкуп 14 конт.).
_PROC_START_MS = so_mod.now_ms()
# Исполнение дописываем, ПОКА заявка не набрана целиком: раньше первое же
# наблюдение замораживало цифру навсегда, и книга показывала «куплен 1 контракт»
# там, где QUIK налил все 19 (06.08.2026). Дальше суток смотреть незачем —
# неисполненный остаток снимает граница сессии, это ловит _mark_orphans.
_FILL_TRACK_MS = 24 * 3600 * 1000
# СКЛАД ЗАЯВОК ЖИВЁТ В ПАМЯТИ И ПУСТ ПОСЛЕ РЕСТАРТА. Заявка, стоящая в QUIK с
# до-рестартного времени, для STL не существует, пока агент не пришлёт по ней
# обновление, — а мирно стоящая заявка обновлений не порождает ВООБЩЕ.
#
# 01.10.2026 это стоило оператору шести контрактов вместо одного: после моего
# рестарта в 12:43 сторож не увидел живую стенку коридора и поставил поверх неё
# новую (so:4e99b8c5ef:low:48830 и :low:5891 — обе налились по 2), а сверху
# выстрелил сам. Один коридор на объём 1 купил 6. В тот же день перезапуск агента
# обнулил ЕГО карту заявок, и 24 заявки сетки стали вдобавок НЕСНИМАЕМЫМИ.
#
# Здесь стояла задержка `_ORDER_STORE_WARMUP_MS = 90_000`: минуту с половиной
# после старта процесса новых заявок не ставим. Она УДАЛЕНА, и её собственный
# комментарий объяснял почему — замок закрывал окно после рестарта, но не случай,
# когда агент молчал дольше прогрева. Это лечение часов, а не причины: заявке в
# QUIK пережить 90 с ничего не стоит, она живёт сутками.
#
# Причина в том, ЧЬЕГО ОТВЕТА мы спрашивали: своего склада в памяти вместо
# терминала. Теперь решение о постановке принимает `trader/quik/terminal.py` по
# таблице заявок QUIK — она живёт в терминале и переживает рестарт и STL, и
# агента, — и там же различаются «таблица пуста» и «таблицы нет».
# Передача защиты под охрану терминала. Столько ждём регистрации стоп-заявки в
# QUIK; не дождались - возвращаем защиту сторожу STL и будим оператора. Ждать
# долго нельзя: всё это время позиция защищена только нашими заявками, а они
# уже помечены как отданные.
_NATIVE_CONFIRM_MS = 20_000
# Флаги стоп-заявки QUIK: бит 0 «активна». Снят - запись отработала или снята
# (28 исполнена, 26/30 снята; серии S1-S2, execution-module.md 3a/3b).
_STOP_ALIVE_BIT = 1
_STOP_FLAG_EXECUTED = 28
# Сколько ждём сделки дочерней заявки, прежде чем признать, что ногу связки
# определить не удалось. Сделки приезжают через секунду-две после исполнения.
_NATIVE_FILL_WAIT_MS = 60_000


def _stop_row_live(row: dict) -> bool:
    """Жива ли стоп-заявка ПРЯМО СЕЙЧАС.

    Таблица QUIK хранит ВСЕ стоп-заявки торгового дня, включая сработавшие и
    снятые, поэтому «строка есть» не значит «стережёт». 29.09.2026 сверка на
    этом сразу же дала ложную тревогу по двум мёртвым записям: одна исполнилась
    утром, вторую сняли руками часом раньше. Признаки смерти:
      withdraw_datetime_ms  — снята (её же ставит наш kill);
      activation_date_time_ms / linkedorder — сработала и породила заявку.
    """
    def _num(key: str) -> int:
        try:
            return int(str(row.get(key) or "0") or 0)
        except (TypeError, ValueError):
            return 0
    return not (_num("withdraw_datetime_ms") or _num("activation_date_time_ms")
                or _num("linkedorder"))


def _stop_num(row: dict) -> str:
    """Номер стоп-заявки. QUIK отдаёт его как order_num/ordernum — поля
    stop_order_num в таблице НЕТ, и чтение несуществующего ключа стоило сверке
    строки «стоп-заявка ? ЖИВА» в первый же час работы."""
    return str(row.get("order_num") or row.get("ordernum") or "")


def _stop_rows_by_tag(store: Any, agent: str) -> dict[str, dict]:
    """ВСЕ стоп-заявки QUIK за день по нашему тегу (brokerref). Тег
    ребёнка-держателя наследуется и дочерней заявкой, и сделкой - по нему видно
    всё. Сработавшая строка нужна здесь именно потому, что она мертва: по её
    linkedorder разбирается, какая нога связки исполнилась."""
    snap = (store.stop_orders(agent) if store is not None else None) or {}
    out: dict[str, dict] = {}
    for row in snap.get("table") or []:
        tag = str(row.get("brokerref") or "")
        if not tag.startswith("stl-so-"):
            continue
        sid = tag[len("stl-so-"):]
        prev = out.get(sid)
        # ПРИ ОДНОМ ТЕГЕ НА НЕСКОЛЬКО СТРОК ПОБЕЖДАЕТ ЖИВАЯ, А НЕ ПОСЛЕДНЯЯ.
        #
        # Словарь по тегу сворачивал строки «последняя побеждает», и 02.10.2026
        # это похоронило живую защиту. В QUIK оказались две строки с одним тегом
        # stl-so-ae6731eec7 (дубль, см. cc8862c); я снял одну, у снятой flags стал
        # 30 — бит живости снят, — и словарь оставил ИМЕННО ЕЁ. Код прочитал
        # «стоп-заявка снята в терминале» (журнал 08:02:47, flags=30), пометил обе
        # ноги cancelled, а выжившая 310530617 продолжила стеречь уже никем не
        # управляемой. Сверка этого не увидела, потому что читает тот же словарь.
        #
        # Живая строка — та, что ПРЯМО СЕЙЧАС стережёт, и именно она отвечает на
        # вопрос «что в терминале». Мёртвая остаётся только когда живых нет: по её
        # linkedorder разбирается, какая нога связки исполнилась.
        if prev is None or (_stop_row_live(row) and not _stop_row_live(prev)):
            out[sid] = row
    return out


def _stop_rows_live(store: Any, agent: str) -> dict[str, dict]:
    """Только те записи, что СТЕРЕГУТ прямо сейчас. Вопрос «кто охраняет» и
    вопрос «чем закрылась связка» читают одну таблицу, но разные её части:
    смешав их, сверка в первый же час подняла тревогу по двум мёртвым записям, а
    сторож замолчал бы поверх них."""
    return {sid: row for sid, row in _stop_rows_by_tag(store, agent).items()
            if _stop_row_live(row)}


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
        for c in kids:
            so_journal.record("native_sent", c, so_journal.WATCHER,
                              f"защита отдана терминалу (связка {holder.so_id}, "
                              f"вход {parent.fired_price:g})", now_ms=now)
        log.info("smart_order.native_sent", parent=parent.so_id, holder=holder.so_id,
                 kind=plan["fields"].get("STOP_ORDER_KIND"), kinds=plan["kinds"],
                 entry=parent.fired_price, fields=plan["fields"])
    return dirty


def _open_positions(store: Any, agent: str) -> dict[str, int]:
    """Открытые позиции счёта: инструмент -> нетто (знак = сторона)."""
    status = (store.agent_status(agent) if store is not None else None) or {}
    out: dict[str, int] = {}
    for p in ((status.get("health") or {}).get("positions") or []):
        try:
            code, net = str(p.get("sec") or ""), int(p.get("net") or 0)
        except (TypeError, ValueError):
            continue
        if code and net:
            out[code] = net
    return out


def _handover_standalone(book: SmartOrderBook, steps: dict[str, float], store: Any,
                         ost: Any, srv: Any, agent: str, now: int) -> bool:
    """Отдать терминалу одиночный стоп или тейк, которым оператор закрыл позицию.

    Такая заявка живёт в книге сама по себе (родителя нет), и до 18.09 её вёл
    только сторож STL: падение STL оставляло позицию без защиты.
    """
    dirty = False
    positions = _open_positions(store, agent)
    for so in book.orders:
        if so.status != "armed" or so.parent_id:
            continue
        # ОТКАЗ ТЕРМИНАЛА НЕ НАВСЕГДА. Раньше любой непустой native_state
        # закрывал заявке дорогу к терминалу до конца её жизни: один мнимый отказ
        # (29.09.2026 подтверждение не пришло за 20 с, потому что в 06:45 торги
        # ещё не шли) — и защита навсегда оставалась только у STL, то есть
        # умирала вместе с падением STL. Пробуем снова, но не чаще, чем раз в
        # _NATIVE_RETRY_MS, иначе отвергающий терминал получал бы транзакцию
        # каждые пять секунд.
        if so.native_state and not (
                so.native_state == "failed"
                and now - (so.native_ms or 0) >= _NATIVE_RETRY_MS):
            continue
        plan = native_protect.build_native_standalone(
            so, steps.get(so.code, 0.0), positions.get(so.code, 0))
        if plan is None:
            continue
        try:
            srv.enqueue_order(agent, order_msgs.build_place_stop_order(
                client_id=f"so:{so.so_id}", code=so.code, side=plan["side"],
                quantity=plan["quantity"], fields=plan["fields"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("smart_order.native_send_failed", so_id=so.so_id, error=str(exc))
            continue
        ost.record_placement(agent)
        so.native_state, so.native_ms, so.status = "sent", now, "native"
        so.note = (so.note + " " if so.note else "") + "под охраной терминала"
        dirty = True
        so_journal.record("native_sent", so, so_journal.WATCHER,
                          "заявка отдана под охрану терминала", now_ms=now)
        log.info("smart_order.native_sent_standalone", so_id=so.so_id, kind=so.kind,
                 code=so.code, side=so.side, qty=so.qty, fields=plan["fields"])
    return dirty


def _native_fill(store: Any, agent: str, row: dict) -> tuple[float, int, int]:
    """Цена, объём и время сделки, которой закрылась нативная стоп-заявка.

    Берём ДОЧЕРНЮЮ заявку записи (linkedorder) и её сделки: это единственное
    доказательство исполнения. Нет сделок - нет утверждения об исполнении."""
    oid = str(row.get("linkedorder") or "")
    if not oid or oid == "0" or store is None:
        return 0.0, 0, 0
    status = store.agent_status(agent) or {}
    px, vol = _fill_price(status, oid)
    ts = 0
    for t in ((status.get("quik") or {}).get("trades") or []):
        if str(t.get("order_num") or "") == oid:
            ts = max(ts, int(t.get("ts_ms") or 0))
    return px, vol, ts


_NATIVE_GONE_PAD_MS = 5_000   # запас: запись могла исчезнуть раньше, чем мы посмотрели


def _trades_between(status: dict, code: str, sides: set[str],
                    t0: int, t1: int) -> tuple[float, int]:
    """Средняя цена и объём сделок РУЧНОГО класса по инструменту в окне времени.

    Доказательство того, что исчезнувшая стоп-заявка всё-таки исполнилась. Свой
    класс — это пустой brokerref (рука оператора) и "stl-so-*" (ребёнок умной
    заявки, так его метит агент). Роботные сделки несут ID робота и сюда не идут:
    робот торгует свою позицию, к стоп-заявке оператора она отношения не имеет."""
    num, vol = 0.0, 0
    first = {s[:1].lower() for s in sides if s}
    for t in ((status.get("quik") or {}).get("trades") or []):
        tag = str(t.get("tag") or "")
        if t.get("sec") != code or (tag and not tag.startswith("stl-so-")):
            continue
        if first and str(t.get("side") or "").lower()[:1] not in first:
            continue
        ts = int(t.get("ts_ms") or 0)
        if not ts or ts < t0 or ts > t1:
            continue
        try:
            q, px = int(t.get("qty") or 0), float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if q > 0 and px > 0:
            num += px * q
            vol += q
    return (num / vol, vol) if vol else (0.0, 0)


def _fired_leg(kids: list[SmartOrder], price: float) -> SmartOrder | None:
    """Какая нога связки сработала: та, чей уровень ближе к цене сделки.

    Одна запись QUIK держит и стоп, и тейк. 22.09.2026 книга помечала
    ИСПОЛНЕННЫМИ обе, и карточка стопа на 85 420 говорила «сработала», хотя
    позицию закрыл тейк на 86 250 - оператор читал это как выбитый стоп."""
    cand = [c for c in kids if c.trigger_price > 0]
    if not cand or price <= 0:
        return None
    return min(cand, key=lambda c: abs(c.trigger_price - price))


def _native_holder(book: SmartOrderBook, parent: SmartOrder) -> SmartOrder | None:
    return next((c for c in book.orders
                 if c.parent_id == parent.so_id and c.status in ("native", "fired", "cancelled")
                 and c.native_state), None)


def _track_native(book: SmartOrderBook, store: Any, agent: str, now: int) -> list[SmartOrder]:
    """Следить за отданными записями: зарегистрирована, отработала, снята, не принята.

    Возвращает родителей, у которых передача СОРВАЛАСЬ: их защиту вернули сторожу
    STL, и оператора надо разбудить - позиция была бы голой, промолчи мы тут."""
    # Только ВЛАДЕЛЬЦЫ записи: у связки это родитель, у одиночной заявки она сама.
    # Ребёнок-держатель тоже носит native_state (в нём лежит номер стоп-заявки), и
    # без этого условия он разбирался бы вторым, отдельным «родителем».
    watched = [p for p in book.orders
               if p.native_state in ("sent", "live") and not p.parent_id]
    rows = _stop_rows_by_tag(store, agent)
    live_rows = _stop_rows_live(store, agent)
    # ЗАПИСЬ ПОЯВИЛАСЬ ПОСЛЕ ОТКАЗА. Подтверждение регистрации приходит за секунды
    # на торгах и дольше до них: 29.09.2026 отправленная в 06:45 стоп-заявка не
    # подтвердилась за 20 с, STL объявил «терминал не принял» и взял охрану себе,
    # а терминал её принял. Двойная охрана стоила оператору 40 лишних проданных
    # контрактов. Поэтому «не принял» — не приговор: увидели запись — отдаём охрану
    # обратно терминалу, иначе сторожей снова двое.
    for p in book.orders:
        if p.native_state != "failed" or p.parent_id:
            continue
        ids = {p.so_id} | {c.so_id for c in book.orders if c.parent_id == p.so_id}
        row = next((live_rows[i] for i in ids if i in live_rows), None)
        if row is None:
            continue
        p.native_state, p.native_seen_ms = "live", now
        for c in ([p] if not any(c.parent_id == p.so_id for c in book.orders)
                  else [c for c in book.orders if c.parent_id == p.so_id]):
            if c.status == "armed":
                c.status = "native"
                c.native_stop_num = _stop_num(row)
                c.note = (c.note + " " if c.note else "") +                     "терминал всё-таки принял: охрана возвращена терминалу"
        so_journal.record("native_live", p, so_journal.TERMINAL,
                          "запись нашлась после отказа: охрана снова у терминала",
                          now_ms=now)
        log.warning("smart_order.native_late_confirm", so_id=p.so_id,
                    stop_num=_stop_num(row))
    if not watched:
        return []
    failed: list[SmartOrder] = []
    orphaned: list[SmartOrder] = []
    for parent in watched:
        if not parent.parent_id and parent.status in ("native", "armed") and not any(
                c.parent_id == parent.so_id for c in book.orders):
            kids, holder = [parent], parent   # одиночная заявка: сама себе держатель
        else:
            kids = [c for c in book.orders if c.parent_id == parent.so_id]
            holder = next((c for c in kids if c.status == "native"), None)
        row = rows.get(holder.so_id) if holder is not None else None
        if row is None:
            # ДЕРЖАТЕЛЬ ИДЁТ ЗА ЗАПИСЬЮ, А НЕ ЗА ПОРЯДКОМ СПИСКА.
            #
            # Связка OCO охраняется ОДНОЙ стоп-заявкой QUIK, и тег у неё — одной из
            # ног. Держатель же выбирался первым подходящим из kids, то есть по
            # порядку в книге. Не совпало — rows.get(holder.so_id) отдаёт None, и
            # ветка ниже заключает «запись исчезла», возвращает охрану STL, а
            # следующий проход передачи ставит в терминал ВТОРУЮ стоп-заявку.
            #
            # 02.10.2026 ровно это и случилось при рестарте в 08:00: в 08:01:15
            # журнал написал «стоп-заявка снялась по сроку, сделок нет», в 08:01:16
            # «защита отдана терминалу», и в QUIK оказались ДВЕ записи с одним тегом
            # stl-so-ae6731eec7, одинаковые до копейки: qty 1, срабатывание 86500,
            # заявка по 85080. Сработали бы обе — продали бы 2 контракта вместо 1.
            #
            # Третий случай одной болезни за двое суток: книга ищет по СВОЕМУ id, а
            # терминал хранит тег ОДНОЙ ноги. Первые два — ложная тревога аудита и
            # вот этот дубль. Поэтому ищем запись по ЛЮБОЙ ноге связки и держателем
            # назначаем ту, чей тег на записи.
            for _leg in kids:
                _r = rows.get(_leg.so_id)
                if _r is not None:
                    row, holder = _r, _leg
                    break
        if row is None:
            if parent.native_state == "sent" and now - parent.native_ms > _NATIVE_CONFIRM_MS:
                parent.native_state = "failed"
                for c in kids:
                    if c.status == "native":
                        c.status = "armed"
                        c.note = (c.note + " " if c.note else "") + \
                            "терминал стоп-заявку не принял: защиту снова ведёт STL"
                failed.append(parent)
                so_journal.record("native_rejected", parent, so_journal.TERMINAL,
                                  "терминал не принял стоп-заявку: защиту ведёт STL",
                                  now_ms=now)
                log.warning("smart_order.native_rejected", parent=parent.so_id)
            elif parent.native_state == "live":
                # Запись была и исчезла. Причин ровно две, и они противоположны по
                # смыслу: либо стоп-заявка сработала, либо её сняло СРОКОМ - у
                # стоп-заявки QUIK срок жизни торговый день (EXPIRY_DATE TODAY), и
                # утром вчерашняя запись исчезает сама. 23.09.2026 так молча пропала
                # следящая продажа 30 контрактов: книга похоронила её в orphaned,
                # охрану себе STL не вернул, и заявки не стало нигде.
                # Судим по ФАКТУ - по сделкам того же инструмента и стороны в окне
                # между последним подтверждением записи и её пропажей.
                natives = [c for c in kids if c.status == "native"]
                status = (store.agent_status(agent) or {}) if store is not None else {}
                seen = parent.native_seen_ms or parent.native_ms
                px, vol = _trades_between(
                    status, parent.code, {c.side for c in natives},
                    seen - _NATIVE_GONE_PAD_MS, now)
                if vol > 0:
                    # Сделка есть - исполнение. Чья нога, решает цена; не решилась -
                    # честное «не установлено». Но переставлять заявку в этой ветке
                    # нельзя никогда: дубль на живом счёте дороже неопределённости.
                    parent.native_state = "done"
                    winner = _fired_leg(natives, px)
                    for c in natives:
                        if c is winner:
                            c.status = "fired"
                            c.fired_price, c.fired_qty, c.fired_ms = px, vol, now
                            c.note = (c.note + " " if c.note else "") +                                 f"запись исчезла, сработала по сделке: {px:g} x {vol}"
                        else:
                            c.status = "cancelled" if winner is not None else "orphaned"
                            c.note = (c.note + " " if c.note else "") + (
                                f"снята вместе со связкой: сделка по {px:g}"
                                if winner is not None else
                                f"запись исчезла, сделка по инструменту {px:g} x {vol}: "
                                "какая нога сработала - не установлено, проверьте позицию")
                    orphaned.append(parent)
                    for c in natives:
                        so_journal.record(
                            "fired" if c.status == "fired" else "orphaned", c,
                            so_journal.TERMINAL,
                            f"запись исчезла, сделка по инструменту {px:g} x {vol}",
                            now_ms=now)
                    log.warning("smart_order.native_gone_filled", parent=parent.so_id,
                                price=px, qty=vol,
                                leg=winner.so_id if winner is not None else None)
                else:
                    # Сделок нет - заявка снялась, а не сработала. Возвращаем охрану
                    # себе: armed и чистый native_state, и ближайший проход передачи
                    # поставит запись в терминал заново, уже на новый торговый день.
                    parent.native_state = ""
                    parent.native_seen_ms = 0
                    for c in natives:
                        c.status = "armed"
                        c.native_state, c.native_stop_num, c.native_seen_ms = "", "", 0
                        c.note = (c.note + " " if c.note else "") +                             "запись снялась в терминале (срок стоп-заявки - торговый день), "                             "сделок по ней нет: заявка снова взведена, её ведёт STL"
                    failed.append(parent)
                    for c in natives:
                        so_journal.record("native_expired", c, so_journal.TERMINAL,
                                          "стоп-заявка снялась по сроку (торговый день), "
                                          "сделок нет: заявку снова ведёт STL", now_ms=now)
                    log.warning("smart_order.native_expired", parent=parent.so_id,
                                since_seen_ms=now - seen, legs=len(natives))
            continue
        try:
            flags = int(row.get("flags") or 0)
        except (TypeError, ValueError):
            flags = 0
        parent.native_seen_ms = now
        if parent.native_state == "sent":
            parent.native_state = "live"
            if holder is not None:
                holder.native_state = "live"
                holder.native_stop_num = str(row.get("order_num") or "")
            so_journal.record("native_live", parent, so_journal.TERMINAL,
                              f"стоп-заявка зарегистрирована, номер {row.get('order_num')}",
                              now_ms=now)
            log.info("smart_order.native_live", parent=parent.so_id,
                     stop_num=row.get("order_num"), flags=row.get("flags"))
        if not flags & _STOP_ALIVE_BIT:
            natives = [c for c in kids if c.status == "native"]
            if flags != _STOP_FLAG_EXECUTED:
                parent.native_state = "done"
                for c in natives:
                    c.status = "cancelled"
                    c.note = (c.note + " " if c.note else "") + "снята в терминале"
                    so_journal.record("native_cancelled", c, so_journal.TERMINAL,
                                      "стоп-заявка снята в терминале", now_ms=now)
                log.info("smart_order.native_cancelled", parent=parent.so_id, flags=flags)
                continue
            # Исполнена. ОДНА запись QUIK держит обе ноги связки, поэтому «сработала»
            # имеет право стоять только у той, чей уровень совпал с ценой сделки;
            # вторая снята вместе с ней. Цена берётся из сделок дочерней заявки.
            px, vol, ts = _native_fill(store, agent, row)
            winner = _fired_leg(natives, px)
            if winner is None:
                if now - parent.native_ms < _NATIVE_FILL_WAIT_MS:
                    continue          # сделки ещё не доехали, ждём следующий проход
                parent.native_state = "done"
                for c in natives:
                    c.status = "orphaned"
                    c.note = (c.note + " " if c.note else "") + \
                        "связка исполнена терминалом, какая нога сработала - не установлено"
                orphaned.append(parent)
                for c in natives:
                    so_journal.record("orphaned", c, so_journal.TERMINAL,
                                      "связка исполнена терминалом, нога не установлена",
                                      now_ms=now)
                log.warning("smart_order.native_done_unattributed", parent=parent.so_id,
                            stop_num=row.get("order_num"), linked=row.get("linkedorder"))
                continue
            parent.native_state = "done"
            winner.status = "fired"
            winner.fired_price, winner.fired_qty = px, vol or winner.qty
            winner.fired_ms = ts or now
            winner.note = (winner.note + " " if winner.note else "") + \
                f"сработала в терминале: {px:g} x {vol or winner.qty}"
            what = {"sl": "стоп", "tp": "тейк", "trail_tp": "следящий тейк"}.get(winner.kind, winner.kind)
            for c in natives:
                if c is winner:
                    continue
                c.status = "cancelled"
                c.note = (c.note + " " if c.note else "") + \
                    f"снята вместе со связкой: в терминале сработал {what} по {px:g}"
            so_journal.record("fired", winner, so_journal.TERMINAL,
                              f"сработала в терминале: {px:g} x {vol or winner.qty}",
                              now_ms=ts or now)
            log.info("smart_order.native_fired", parent=parent.so_id, leg=winner.so_id,
                     kind=winner.kind, price=px, qty=vol, linked=row.get("linkedorder"))
    failed.extend(orphaned)
    return failed




# Защитные типы: их смысл — закрыть УЖЕ ОТКРЫТУЮ позицию. Коридор, треугольник,
# сетка и вход по факту сделки сюда не входят: у них позиции может не быть по
# замыслу.
_PROTECTIVE_KINDS = frozenset({"sl", "tp", "trail_tp", "trail_sl"})


def _nothing_to_protect(so: SmartOrder, position: int) -> bool:
    """Нет ли в рынке позиции, которую эта заявка закрывает.

    Заявка на ПОКУПКУ закрывает шорт, на ПРОДАЖУ — лонг. Если позиции нужного
    знака нет, закрывать нечего: сработав, такая заявка не защитит, а ОТКРОЕТ
    позицию, которой никто не просил.
    """
    if position == 0:
        return True
    closing = "sell" if position > 0 else "buy"
    return so.side != closing


def _retire_unprotecting(book: SmartOrderBook, store: Any, agent: str,
                         now: int) -> bool:
    """Снять взведённую защитную заявку, которой больше нечего охранять.

    ИНЦИДЕНТ 01.10.2026. Два стопа оператора на покупку 40 и 13 по уровню 85850
    простояли взведёнными 34 минуты после того, как охраняемый шорт на 53
    контракта исчез (30.09 в 16:32). До уровня оставалось 230 пунктов, а 29.09
    RIZ6 проходил 690 пунктов за минуту: сработав, они открыли бы ЛОНГ на 53
    контракта с нуля, без решения человека.

    Механизм, который это допустил, знал о проблеме и молчал. Передача защиты
    терминалу (`build_native_standalone`) при нулевой позиции возвращает None —
    то есть система ВИДИТ, что охранять нечего, — но заявка от этого лишь
    остаётся в статусе `armed`, а `armed` означает «стережёт сторож STL». Отказ
    отдать защиту терминалу не отменял самой защиты. Рестарт STL довершал дело:
    он заново принимал запись книги и пробовал регистрацию, терминал отказывал,
    и запись окончательно становилась взведённым курком.

    Не отменяем, а помечаем `orphaned` — как и остальные осиротевшие: цена уже
    другая, перевзводить за человека нельзя. Нативную ногу здесь не трогаем: мы
    списываем только `armed`, а `armed` и означает, что терминал заявку НЕ
    держит. Расхождение книги с таблицей терминала — отдельная забота сверки
    (`_audit_book_vs_terminal`), и подменять её здесь нельзя: 29.09.2026 книга
    уже один раз судила о терминале по своему мнению, и это стоило 40 контрактов.

    ДВЕ ОСТОРОЖНОСТИ, БЕЗ КОТОРЫХ ЭТО ОПАСНЕЕ БОЛЕЗНИ:

    1. ТОЛЬКО ПО СВЕЖЕМУ СНИМКУ. Пустой снимок позиций значит «не знаю», а не
       «позиций нет». Судить по слепоте — значит снять ЖИВУЮ защиту у открытой
       позиции, а это потеря дороже той, от которой защищаемся.
    2. С ВЫДЕРЖКОЙ. Между филлами позиция мелькает нулём, а переворот проходит
       через ноль по построению. Списываем только то, что стоит без охраняемой
       позиции дольше выдержки.
    """
    status = (store.agent_status(agent) if store is not None else None) or {}
    health = status.get("health")
    if not isinstance(health, dict) or health.get("positions") is None:
        return False                      # снимка нет: молчим, это не «ноль»
    positions = _open_positions(store, agent)
    dirty = False
    for so in book.orders:
        if so.status != "armed" or so.parent_id or so.kind not in _PROTECTIVE_KINDS:
            continue
        # Вход с блоками после сделки — не защита: позиции у него и не должно быть.
        if (so.sl_offset or so.tp_offset or so.trail_after or so.tp_trail
                or so.sl_price or so.tp_price):
            continue
        if not _nothing_to_protect(so, positions.get(so.code, 0)):
            if so.flat_since_ms or not so.guarded_seen:
                so.flat_since_ms = 0      # позиция вернулась: счётчик сбрасываем
                so.guarded_seen = True    # и теперь знаем: охранять БЫЛО что
                dirty = True
            continue
        # НИ РАЗУ НЕ ВИДЕЛИ ОХРАНЯЕМОЙ ПОЗИЦИИ — это ВХОД, а не осиротевшая
        # защита, и трогать его нельзя. Голый tp/sl/trail_tp без блоков после
        # сделки выглядит неотличимо от защиты, пережившей свою позицию;
        # разница одна — у защиты позиция БЫЛА. Без этой проверки списание
        # молча снимало бы взведённые входы оператора (ревью 01.10.2026).
        if not so.guarded_seen:
            continue
        if not so.flat_since_ms:
            so.flat_since_ms = now
            dirty = True
            continue
        if now - so.flat_since_ms < _ORPHAN_GRACE_MS:
            continue
        so.status = "orphaned"
        so.note = ((so.note + " " if so.note else "")
                   + "охранять нечего: позиции, которую она закрывает, в рынке нет")
        dirty = True
        so_journal.record(
            "orphaned", so, so_journal.WATCHER,
            f"позиции по {so.code} нет {(now - so.flat_since_ms) // 60000} мин: "
            f"{so.side} {so.qty} не защитит, а ОТКРЫЛА бы позицию. Снята со взвода.",
            now_ms=now)
        log.warning("smart_order.nothing_to_protect", so_id=so.so_id, kind=so.kind,
                    code=so.code, side=so.side, qty=so.qty)
    return dirty


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
    # ОСНОВНАЯ УМЕРЛА БЕЗ ФИЛЛА — спящие дочерние снимаются вместе с ней. Иначе
    # они дождались бы своего часа и открыли позицию под защиту, которой нет
    # (01.10.2026: отвергнутая коллaром продажа 10 и купившая 10 дочерняя).
    for so in book.orders:
        if so.status not in ("orphaned", "cancelled", "error", "expired"):
            continue
        if so.fired_qty > 0:
            continue                       # филл был: дочерние законны
        for kid in book.orders:
            if kid.parent_id == so.so_id and kid.status == so_mod.WAITING:
                kid.status = "cancelled"
                kid.note = (f"основная заявка {so.so_id} завершилась без филла "
                            f"({so.status}): защищать нечего")
                dirty = True
                so_journal.record("cancelled", kid, so_journal.WATCHER, kid.note, now_ms=now)
                log.info("smart_order.child_dropped", child=kid.so_id, parent=so.so_id,
                         parent_status=so.status)
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
            # ФИЛЛ ПОДТВЕРЖДЁН ФАКТОМ — только теперь дочерние защитные заявки
            # имеют смысл и взводятся. До этого они спят: 01.10.2026 основную
            # заявку на продажу 10 отверг коллар, филла не было ни одного, а
            # взведённая сразу дочерняя купила 10 контрактов. Уровни к этому
            # моменту уже пересчитаны на ФАКТИЧЕСКУЮ цену входа (строкой выше),
            # поэтому взводим после rebase, а не до.
            for kid in book.orders:
                if kid.parent_id == so.so_id and kid.status == so_mod.WAITING:
                    kid.status = "armed"
                    dirty = True
                    so_journal.record("armed", kid, so_journal.WATCHER,
                                      f"филл основной подтверждён ({vol} по {px:g}): "
                                      f"дочерняя защита взведена, уровень "
                                      f"{kid.trigger_price:g}", now_ms=so_mod.now_ms())
                    log.info("smart_order.child_armed", child=kid.so_id,
                             parent=so.so_id, entry=px, trigger=kid.trigger_price)
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


# ── Регулярная сверка книги с терминалом ──────────────────────────────────────
# Что стережёт QUIK и что думает STL — две разные картины, и до 29.09.2026 их
# никто не сравнивал. Рекон агента сверяет с QUIK позиции, заявки и сделки
# РОБОТОВ; умные заявки оператора и таблица стоп-заявок в него не входят вовсе.
# Цена пробела известна: книга писала «отменена», терминал держал заявку живой и
# готовой продать 40 контрактов, а раньше в тот же день двойная охрана уже продала
# 40 лишних. Сверка молчит, пока картины сходятся, и говорит один раз на каждое
# новое расхождение — повторять каждые пять секунд значит приучить не читать.
_AUDIT_SEEN: set[str] = set()


def _audit_book_vs_terminal(book: SmartOrderBook, rows: dict[str, dict]) -> list[str]:
    """Расхождения между книгой умных заявок и таблицей стоп-заявок QUIK.

    Обе стороны важны и означают разное:
      • запись в терминале есть, а книга её не стережёт (снята, исполнена, нет
        вовсе) — заявка выстрелит сама по себе, и это ровно случай 29.09;
      • книга числит заявку под охраной терминала, а записи нет — позиция голая,
        сторожа нет ни там, ни здесь.
    """
    by_id = {o.so_id: o for o in book.orders}
    out: list[str] = []
    for sid, row in rows.items():
        so = by_id.get(sid)
        num = _stop_num(row) or "?"
        if so is None:
            out.append(f"в терминале живёт стоп-заявка {num} ({row.get('sec_code') or '?'}), "
                       "а в книге такой заявки нет вовсе")
        elif so.status not in ("native", "armed"):
            out.append(f"книга считает заявку {sid} «{so.status}», "
                       f"а в терминале стоп-заявка {num} ЖИВА")
    for o in book.orders:
        if o.status != "native" or o.so_id in rows:
            continue
        if any(c.parent_id == o.so_id and c.so_id in rows for c in book.orders):
            continue
        # СВЯЗКА OCO ОХРАНЯЕТСЯ ОДНОЙ СТОП-ЗАЯВКОЙ QUIK, и тег у неё — одной из ног.
        #
        # Ложная тревога 01.10.2026 сразу после рестарта: «c21c114afa числится под
        # охраной терминала, а записи в таблице стоп-заявок нет: позиция без
        # сторожа». На деле стоп и следящий тейк от входа 34593ce9df это связка
        # (oco_group br:34593ce9df), и терминал держит её ОДНОЙ строкой
        # 1012532699 под тегом stl-so-ae6731eec7 — что прямо написано в примечании
        # самого тейка. Проверка знала про дочерние заявки и не знала про сиблингов,
        # поэтому вторая нога всегда выглядела беззащитной.
        #
        # Цена такой ошибки — не ноль: ложная тревога про ОТСУТСТВИЕ защиты учит
        # не верить тревогам, а 01.10 ложный SMS-алерт по умной заявке уже был.
        if o.oco_group and any(c.oco_group == o.oco_group and c.so_id in rows
                               for c in book.orders):
            continue
        out.append(f"заявка {o.so_id} числится под охраной терминала, "
                   "а записи в таблице стоп-заявок нет: позиция без сторожа")
    return out


async def _report_audit(srv: Any, agent: str, book: SmartOrderBook,
                        rows: dict[str, dict]) -> None:
    for msg in _audit_book_vs_terminal(book, rows):
        if msg in _AUDIT_SEEN:
            continue
        _AUDIT_SEEN.add(msg)
        log.error("smart_order.audit_mismatch", detail=msg)
        fwd = getattr(srv, "alert_forwarder", None)
        if fwd is None:
            continue
        await fwd.forward({"severity": SEVERITY_CRITICAL,
                           "code": "smart_order_audit",
                           "message": f"Сверка умных заявок с QUIK: {msg}",
                           "raised_at_unix_ms": so_mod.now_ms()}, agent)



def _escalate_protection(book: SmartOrderBook, store: Any, ost: Any, srv: Any,
                         lim: Any, agent: str, steps: dict[str, float],
                         now: int) -> bool:
    """Довести защитную заявку до исполнения за три фазы, а не ждать у моря погоды.

    Стоп выставляет ЛИМИТНУЮ заявку, и на быстром движении она не наливается:
    29.09.2026 родной стоп оператора на 70 RIZ6 сработал в 14:50:06 с лимитом в
    30 пунктов от уровня, рынок за минуту прошёл 690 пунктов, заявка умерла с
    нулём исполнения, позиция осталась открытой. Заказ оператора в тот же день:
    стоп обязан быть гарантированным — постоять у планки, потом идти за ценой,
    потом бить по рынку.

    Фазы считаются от fired_ms: hold (стоим) -> chase (переставляем каждые
    every) -> market (один удар по границе коллара). Тейков и входов не
    касается: опоздавший тейк — упущенная прибыль, опоздавший стоп — открытый
    убыток, и торопить их надо по-разному.
    """
    by_cid = {d["client_id"]: d for d in ost.working_orders(agent)}
    dirty = False
    for so in book.orders:
        # ГАРАНТИЯ ИСПОЛНЕНИЯ — У ВСЕХ ТИПОВ (решение оператора 29.09.2026).
        # Сначала эскалация стояла только на защитном стопе: опоздавший тейк это
        # упущенная прибыль, а опоздавший стоп — открытый убыток. Оператор
        # рассудил иначе и он прав: заявка, которая сработала и не исполнилась,
        # врёт человеку одинаково независимо от типа. Он видит «сработала», а в
        # рынке ничего не изменилось — и узнаёт об этом в худший момент.
        # Секунды фаз у каждой заявки свои, так что осторожность настраивается,
        # а не зашита в тип.
        if not so.fired_client_id:
            continue
        if so.kind not in ("corridor", "triangle") and so.status != "fired":
            continue
        rec = by_cid.get(so.fired_client_id)
        if rec is None or rec.get("state") in _DEAD_STATES:
            continue                       # снята/отвергнута — это к _mark_orphans
        if int(rec.get("remaining") or 0) <= 0:
            continue                       # налилась: гнать больше некуда
        order_id = str(rec.get("order_id") or "")
        if not order_id:
            continue                       # QUIK ещё не ответил номером
        age = now - (so.fired_ms or now)
        prof = exec_profiles.resolve(so.esc_profile)
        # Явные секунды заявки перекрывают профиль: разовая заявка не должна
        # требовать правки общей настройки. Ноль в поле = «брать из профиля»,
        # иначе профиль нельзя было бы применить вовсе.
        hold = max(0, int(so.esc_hold_sec or prof["hold_sec"])) * 1000
        chase = max(0, int(so.esc_chase_sec or prof["chase_sec"])) * 1000
        every = max(1, int(so.esc_chase_every_sec or prof["chase_every_sec"] or 1)) * 1000
        if not prof.get("market", True) and not (hold or chase):
            continue          # профиль «нормальный»: доведения нет, заявка стоит лимитом
        if age < hold:
            continue                       # фаза 1: стоим у планки
        t = store.tick(so.code, agent) or {}
        last = float(t.get("last") or 0)
        bid, ask = float(t.get("bid") or 0), float(t.get("ask") or 0)
        step = steps.get(so.code, 0.0)
        if age < hold + chase:             # фаза 2: идём за ценой
            if now - (so.esc_last_ms or 0) < every:
                continue
            px = so_mod.marketable_price(so.side, bid, ask, last, step)
            phase = "преследование"
        elif not so.esc_market and prof.get("market", True):  # фаза 3: РЫНОЧНОЙ, один раз
            # Переставить лимит в рыночную нельзя — MOVE_ORDERS меняет цену, а не
            # тип. Поэтому снимаем остаток и шлём отдельную рыночную: это
            # единственный способ выйти при любом движении. Коллар к ней не
            # применяется, цены у неё нет.
            rest = int(rec.get("remaining") or 0)
            mkt_cid = f"{so.fired_client_id}:mkt"
            try:
                srv.enqueue_order(agent, order_msgs.build_cancel_order(
                    client_id=so.fired_client_id, order_id=order_id, code=so.code))
                validate_place(lim, code=so.code, quantity=rest, collar=0.0,
                               current_working=ost.working_contracts(agent),
                               placed_today=ost.placed_today(agent))
                ost.register_pending(agent, mkt_cid, so.code, so.side, 0.0, rest)
                ost.record_placement(agent)
                srv.enqueue_order(agent, order_msgs.build_place_order(
                    client_id=mkt_cid, code=so.code, side=so.side, price=0.0,
                    quantity=rest, collar=0.0, market=True))
            except LimitError as exc:
                log.error("smart_order.market_exit_refused", so_id=so.so_id, error=str(exc))
                so_journal.record("error", so, so_journal.LIMITS,
                                  f"рыночный выход отклонён лимитами: {exc}", now_ms=now)
                so.esc_market = True       # долбить лимиты каждые 5 с бессмысленно
                dirty = True
                continue
            except Exception as exc:  # noqa: BLE001 — связь не должна ронять проход
                log.warning("smart_order.escalate_failed", so_id=so.so_id, error=str(exc))
                continue
            so.esc_market = True
            so.esc_last_ms = now
            so.fired_client_id = mkt_cid   # дальше следим за рыночной
            dirty = True
            so_journal.record("escalated", so, so_journal.WATCHER,
                              f"по рынку: лимит не налился за {age // 1000} с, "
                              f"остаток {rest} выводим рыночной заявкой", now_ms=now)
            log.warning("smart_order.escalated_market", so_id=so.so_id,
                        qty=rest, age_ms=age)
            continue
        else:
            continue
        if px <= 0:
            continue                       # без котировки не двигаем: цена вслепую хуже
        try:
            srv.enqueue_order(agent, order_msgs.build_replace_order(
                client_id=so.fired_client_id, order_id=order_id, new_price=px))
        except Exception as exc:  # noqa: BLE001 — связь не должна ронять проход
            log.warning("smart_order.escalate_failed", so_id=so.so_id, error=str(exc))
            continue
        so.esc_last_ms = now
        dirty = True
        so_journal.record("escalated", so, so_journal.WATCHER,
                          f"{phase}: заявка не налилась за {age // 1000} с, "
                          f"переставлена на {px:g} (осталось {rec.get('remaining')})",
                          now_ms=now)
        log.warning("smart_order.escalated", so_id=so.so_id, phase=phase,
                    price=px, remaining=rec.get("remaining"), age_ms=age)
    return dirty


def _escalate_native_child(book: SmartOrderBook, store: Any, srv: Any, lim: Any,
                           agent: str, rows: dict[str, dict], now: int) -> bool:
    """Добить заявку, которую породила СРАБОТАВШАЯ нативная стоп-заявка.

    Дыра, найденная вопросом оператора 29.09.2026: отдавая защиту терминалу, мы
    получали живучесть (стоп переживает падение STL) и ТЕРЯЛИ гарантию
    исполнения. Нативный стоп QUIK выставляет лимит с запасом в два шага цены —
    на быстром движении его не наливают, и ровно это случилось с его же родным
    стопом на 70 контрактов: сработал в 14:50:06 и умер с нулём исполнения, пока
    рынок шёл 690 пунктов за минуту. Сторож при этом молчал: заявка «под охраной
    терминала», значит не его забота.

    Теперь его: как только запись активировалась и её заявка висит неисполненной
    дольше профиля, остаток снимается и добивается рыночной. Без этого «передать
    терминалу» означало бы «отказаться от гарантии», а выбирать между живучестью
    и исполнением оператор не должен.
    """
    quik = (store.agent_status(agent) or {}).get("quik") or {} if store is not None else {}
    orders = {str(o.get("num") or ""): o for o in (quik.get("orders") or [])}
    dirty = False
    for so in book.orders:
        if so.status not in ("native", "armed") or so.esc_market:
            continue
        sid = so.so_id if so.so_id in rows else next(
            (c.so_id for c in book.orders
             if c.parent_id == so.so_id and c.so_id in rows), "")
        row = rows.get(sid) if sid else None
        if row is None:
            continue
        acted = int(str(row.get("activation_date_time_ms") or "0") or 0)
        child = str(row.get("linkedorder") or "")
        if not acted or not child or child == "0":
            continue                      # ещё не срабатывала — стережёт, и хорошо
        rec = orders.get(child) or {}
        rest = int(rec.get("balance") or 0)
        if rest <= 0 or rec.get("active") is False and rest <= 0:
            continue                      # налилась
        prof = exec_profiles.resolve(so.esc_profile)
        if not prof.get("market", True):
            continue                      # профиль «нормальный»: не вмешиваемся
        wait = (int(so.esc_hold_sec or prof["hold_sec"])
                + int(so.esc_chase_sec or prof["chase_sec"])) * 1000
        if now - acted < wait:
            continue
        try:
            srv.enqueue_order(agent, order_msgs.build_cancel_order(
                client_id=f"so:{so.so_id}:nat", order_id=child, code=so.code))
            validate_place(lim, code=so.code, quantity=rest, collar=0.0,
                           current_working=0, placed_today=0)
            srv.enqueue_order(agent, order_msgs.build_place_order(
                client_id=f"so:{so.so_id}:nat", code=so.code, side=so.side,
                price=0.0, quantity=rest, collar=0.0, market=True))
        except LimitError as exc:
            log.error("smart_order.native_market_refused", so_id=so.so_id, error=str(exc))
            so_journal.record("error", so, so_journal.LIMITS,
                              f"рыночное добивание нативного стопа отклонено: {exc}",
                              now_ms=now)
            so.esc_market = True
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("smart_order.native_escalate_failed", so_id=so.so_id, error=str(exc))
            continue
        so.esc_market = True
        so.status = "fired"
        so.fired_ms = so.fired_ms or acted
        dirty = True
        so_journal.record("escalated", so, so_journal.WATCHER,
                          f"стоп терминала сработал {int((now - acted) / 1000)}с назад и "
                          f"не налился: остаток {rest} выводим рыночной заявкой", now_ms=now)
        log.warning("smart_order.native_child_escalated", so_id=so.so_id,
                    stop_num=_stop_num(row), child=child, qty=rest)
    return dirty



# ── СЕТКА «РАДИАЦИЯ»: заявки живут В СТАКАНЕ, а не в сторожe ─────────────────
# Остальные умные заявки сторож стреляет сам, когда цена дошла до уровня. Сетке
# так нельзя: оператор торгует ликвидность, и заявка, выставленная в момент
# касания, приходит в очередь последней. Поэтому все уровни сразу уходят в QUIK
# лимитными заявками и стоят в стакане; сторож лишь ВОССТАНАВЛИВАЕТ исполненный
# уровень встречной заявкой и следит за стопом.
_GRID_CID = "so:{so_id}:g{level}"


def _grid_cid(so_id: str, level: int) -> str:
    return _GRID_CID.format(so_id=so_id, level=f"{level:+d}".replace("+", "p").replace("-", "m"))


def _grid_position_delta(so: SmartOrder, side_was: str, got: int, price: float) -> None:
    """Позиция, средняя и денежный поток сетки — без гашения уровней.

    Отдельно от _grid_count_fill, потому что ЧАСТИЧНОЕ исполнение стоящей заявки
    двигает позицию сразу, а гасит уровень только полное (или снятие остатка).
    """
    if got <= 0:
        return
    # ПОТОК ДО ПОЗИЦИИ: база заводится из состояния ДО этого филла, иначе филл
    # посчитался бы дважды — в средней и в потоке.
    if so_mod.ensure_cash_basis(so) and price > 0:
        so.g_cash_pts += price * got if side_was == "sell" else -price * got
    so.g_pos, so.g_avg = so_mod.blend_avg(
        so.g_pos, so.g_avg, got, price, side_was == "buy")


def _grid_count_partial(so: SmartOrder, live: dict, key: str, level: int,
                        side_was: str, filled_now: int, now: int,
                        price: float = 0.0) -> bool:
    """Частичное исполнение СТОЯЩЕЙ заявки уровня — в позицию сразу.

    02.10.2026, GZZ6: заявка уровня «продать 5 по 9914» налилась на 4 и стояла с
    остатком 1. Сторож видел её «стоящей» и пропускал, а позицию трогал только при
    полном исполнении. Сетку сняли раньше — 4 контракта так и не попали в её
    позицию: в книге +10, по сделкам +6. На ложную позицию опираются стоп, защита
    и выход «только на выход».

    `key` — ключ учтённого объёма этой заявки в live (`pf:<client_id>` или
    `pf:<номер>` у подхваченной). Возвращает, изменилось ли что-нибудь.
    """
    done = int(live.get(key) or 0)
    if filled_now <= done:
        return False
    _grid_position_delta(so, side_was, filled_now - done, price)
    live[key] = filled_now
    so_journal.record("grid_fill", so, so_journal.WATCHER,
                      f"уровень {level:+d} ({so_mod.grid_price(so, level):g}) налился "
                      f"частично: {side_was} {filled_now - done} (всего {filled_now}); "
                      f"позиция {so.g_pos:+d}; уровень стоит с остатком", now_ms=now)
    return True


def _grid_count_fill(so: SmartOrder, live: dict, level: int, side_was: str,
                     got: int, now: int, how: str = "", price: float = 0.0,
                     already: int = 0, num: str = "") -> None:
    """Провести филл уровня сетки: позиция, гашение уровня, пробуждение соседей.

    ЕДИНСТВЕННЫЙ путь учёта филла в сетке. Таких путей теперь два источника — запись
    склада заявок и исчезновение строки из таблицы терминала (подхваченная после
    рестарта заявка своего client_id не имеет, см. terminal.by_num), и считать они
    обязаны ОДИНАКОВО: от позиции сетки зависят и объём, и стоп.

    Механика «радиации» со слов оператора: тейка нет, есть уровни. Исполненный
    уровень ИСЧЕЗАЕТ и возвращается только после филла СОСЕДНЕГО — любого, хоть
    ниже, хоть выше.
    """
    # В позицию — только то, что ещё не учтено частичными исполнениями (`already`).
    _grid_position_delta(so, side_was, got - already, price)
    _mark_counted(live, num)
    so_mod.grid_note_fill(live, level, side_was)      # для запрета повтора той же стороны
    so.g_fills_done += 1                          # для защиты «N уровней подряд»
    live[f"flip:{level}"] = True                  # этот уровень погас
    woke = [n for n in (level - 1, level + 1)
            if live.pop(f"flip:{n}", None)]       # соседи ожили
    so_journal.record("grid_fill", so, so_journal.WATCHER,
                      f"уровень {level:+d} ({so_mod.grid_price(so, level):g}) "
                      f"исполнен {side_was} {got}; позиция {so.g_pos:+d}"
                      + (f" по средней {so.g_avg:g}" if so.g_avg else "")
                      + "; уровень погас"
                      + (f", вернулись соседние {woke}" if woke else "")
                      + how, now_ms=now)


def _grid_sync(book: SmartOrderBook, store: Any, ost: Any, srv: Any, lim: Any,
               agent: str, steps: dict[str, float],
               price_limits: dict[str, tuple[float, float]], now: int,
               session_open: bool | None = None) -> bool:
    """Держать сетку выставленной: доставить недостающие уровни, перевернуть
    исполненные, снять всё при стопе."""
    dirty = False
    work = {d.get("client_id"): d for d in ost.working_orders(agent)}
    coefs = _point_coefs(store, agent)
    # ЧТО СТОИТ В ТЕРМИНАЛЕ — один запрос на проход. None = зеркала агента нет или
    # оно встало: это «НЕ ЗНАЮ», а не «ничего не стоит», и ставить при нём нельзя.
    # Пустой список — законный ответ «в терминале этой заявки нет».
    _term_ok = terminal.fresh(store, agent, now)
    term_all = terminal.by_smart_order(store, agent) if _term_ok else None
    # ВСЕ строки по номеру, не только живые: по ним догоняется филл ПОДХВАЧЕННОЙ
    # заявки — своего client_id у неё нет, и склад о её исполнении не узнает.
    term_num = terminal.by_num(store, agent) if _term_ok else {}
    # ЗАКРЫТИЕ ПО СТОПУ ЖДЁТ ПОДТВЕРЖДЕНИЯ. «Отправили рыночную» и «позиция
    # закрыта» — разные события: 29.09.2026 рыночная заявка нативного стопа на 70
    # контрактов сработала и умерла с НУЛЁМ исполнения, пока рынок шёл 690 пунктов
    # за минуту. Поэтому сетка в статусе closing ещё жива, и позиция обнуляется
    # только по ФАКТУ исполнения, а не по факту отправки.
    for so in book.orders:
        if so.kind != "grid" or so.status != "closing":
            continue
        if not so.g_close_cid:
            # ЗАКРЫТИЕ НЕ УШЛО (отбили лимиты) — пробуем снова, пока позиция жива.
            # Сетка уже g_done, уровни не ставятся; здесь только выход.
            if not so.g_pos:
                so.status = "cancelled"
                dirty = True
                continue
            close_side = "sell" if so.g_pos > 0 else "buy"
            close_qty = abs(so.g_pos)
            cid = f"so:{so.so_id}:stopclose:{now % 100000}"
            try:
                validate_place(lim, code=so.code, quantity=close_qty, collar=0.0,
                               current_working=ost.working_contracts(agent),
                               placed_today=ost.placed_today(agent), reducing=True)
                ost.register_pending(agent, cid, so.code, close_side, 0.0, close_qty)
                ost.record_placement(agent)
                srv.enqueue_order(agent, order_msgs.build_place_order(
                    client_id=cid, code=so.code, side=close_side, price=0.0,
                    quantity=close_qty, collar=0.0, market=True))
                so.g_close_cid = cid
                dirty = True
                so_journal.record("grid_stop", so, so_journal.WATCHER,
                                  f"повторная попытка закрытия: {close_side} "
                                  f"{close_qty} рыночной ({cid})", now_ms=now)
            except LimitError:
                pass          # причина уже записана, молчим до успеха
            continue
        rec = work.get(so.g_close_cid) or {}
        got = int(rec.get("filled") or 0)
        rest = int(rec.get("remaining") or 0)
        if got:
            # Знак филла из записи заявки, а не из нашего намерения.
            side_was = str(rec.get("side") or "").lower()
            if side_was not in ("buy", "sell"):
                side_was = "sell" if so.g_pos > 0 else "buy"
            so.g_pos += got * (1 if side_was == "buy" else -1)
            dirty = True
        if not rec:
            continue                       # заявка ещё не доехала до склада
        if rest > 0 and _is_working(rec):
            continue                       # ещё наливается
        if so.g_pos == 0:
            so.status = "cancelled"
            so.note = (so.note + " " if so.note else "") + "позиция закрыта"
            so_journal.record("grid_stop", so, so_journal.WATCHER,
                              f"закрытие по стопу исполнено: {got} по "
                              f"{float(rec.get('price') or 0):g}; позиция 0, "
                              "сетка завершена", now_ms=now)
            dirty = True
            continue
        # ЗАЯВКА КОНЧИЛАСЬ, А ПОЗИЦИЯ НЕТ. Именно этот случай 29.09 стоил дорого.
        so.status = "error"
        so.note = (so.note + " " if so.note else "") + (
            f"закрытие по стопу НЕ ДОВЕДЕНО: осталось {so.g_pos:+d} БЕЗ ЗАЩИТЫ")
        so_journal.record("error", so, so_journal.WATCHER,
                          f"закрытие по стопу кончилось с остатком: исполнено {got}, "
                          f"позиция {so.g_pos:+d} ОСТАЛАСЬ БЕЗ ЗАЩИТЫ — закройте "
                          "вручную", now_ms=now)
        log.error("smart_order.grid_close_incomplete", so_id=so.so_id, pos=so.g_pos,
                  filled=got)
        dirty = True
    for so in book.orders:
        if so.kind != "grid" or so.status not in ("armed", "native") or so.g_done:
            continue
        term_live = None if term_all is None else term_all.get(so.so_id, [])
        step = steps.get(so.code, 0.0)
        limits = price_limits.get(so.code)
        tick = store.tick(so.code, agent) or {}
        price = float(tick.get("last") or 0)
        if price <= 0:
            # БЕЗ КОТИРОВКИ СЕТКА НЕ СТРЕЛЯЕТ. Проверка «уровень по ту сторону
            # рынка» гейтится ценой, и при price<=0 она пропускалась целиком —
            # то есть вслепую выставлялись ВСЕ уровни разом, включая те, что
            # исполнятся мгновенно. Стенки коридора этот запрет получили сразу
            # (инцидент 30.09.2026, 43 контракта), а сетка осталась без него:
            # там был добавлен только пропуск уровня, но не отказ вслепую.
            # Кадр приходит каждые полсекунды, ждать нечего.
            continue
        live = dict(so.g_live or {})

        # ПЕРЕВЗВЕДЕНИЕ ПОСЛЕ ЧИСТОГО ВЫХОДА, две половины: назначить срок и
        # дождаться его. Проверяется ПЕРВЫМ, иначе защита на том же проходе снова
        # увидит старый счётчик филлов и выключит только что взведённую сетку.
        if so.exit_only and so.g_trig_ms and so.g_rearm_min > 0:
            if so.g_pos == 0 and not so.g_rearm_at_ms:
                # Вышли в ноль по защите — назначаем возвращение.
                so.g_rearm_at_ms = now + int(so.g_rearm_min * 60_000)
                dirty = True
                so_journal.record(
                    "guard", so, so_journal.WATCHER,
                    f"выход по защите завершён, позиция 0; перевзведение через "
                    f"{so.g_rearm_min:g} мин", now_ms=now)
            elif so.g_rearm_at_ms and now >= so.g_rearm_at_ms:
                base = so_mod.grid_rearm_base(so, price)
                if base > 0:
                    was = so.g_base
                    so.g_base = base
                    so.exit_only = False
                    so.g_trig_ms = 0
                    so.g_fills_done = 0
                    so.g_touch_up = 0
                    so.g_touch_down = 0
                    so.g_zone_side = ""
                    so.g_rearm_at_ms = 0
                    so.g_rearms += 1
                    so.g_avg = 0.0
                    # Бухгалтерию уровней обнуляем целиком: погасшие уровни и
                    # подхваты относились к ПРОШЛОЙ базе и к новой отношения не
                    # имеют. Оставить их значило бы не выставить часть сетки молча.
                    live = {}
                    dirty = True
                    so_journal.record(
                        "guard", so, so_journal.WATCHER,
                        f"ПЕРЕВЗВЕДЕНА (раз {so.g_rearms}): база {was:g} -> {base:g} "
                        f"по текущей цене, счётчики обнулены, набор разрешён",
                        now_ms=now)
                    log.warning("smart_order.grid_rearmed", so_id=so.so_id,
                                base_was=was, base=base, times=so.g_rearms)

        # ЗАЩИТА СЕТКИ: сама переводит её в режим «только на выход».
        #
        # Проверяется ДО стопа, но ПОСЛЕ учёта филлов: стоп закрывает позицию
        # рыночной и завершает сетку, а защита лишь прекращает НАБОР и выпускает
        # по безубытку. Включать защиту после стопа было бы поздно, а до учёта
        # филлов — на устаревшем счётчике.
        #
        # Стоящие заявки снимаем сразу: это всё входные уровни, и оставить их
        # значило бы продолжать набор, против которого защита и заведена. Выход
        # поставит следующий проход, уже по правилам «только на выход».
        if not so.exit_only:
            why = so_mod.grid_guard_hit(so, price)
            if why:
                so.exit_only = True
                so.g_trig_ms = now
                cids = {c for c in live.values() if isinstance(c, str) and c}
                killed, _extra = _cancel_resting(srv, store, agent, so, cids, work)
                for k in [k for k in live if not k.startswith(("flip:", "adopt:"))]:
                    live.pop(k, None)
                dirty = True
                so_journal.record(
                    "guard", so, so_journal.WATCHER,
                    f"ЗАЩИТА СЕТКИ: {why}. Набор прекращён, снято заявок {killed}, "
                    f"позиция {so.g_pos:+d} по средней {so.g_avg:g} выводится "
                    "только без убытка", now_ms=now)
                log.warning("smart_order.grid_guard", so_id=so.so_id, why=why,
                            pos=so.g_pos, avg=so.g_avg, killed=killed)

        # ЦЕЛЬ ПРИБЫЛИ: забрать деньги и закончить. Тот же путь, что у стопа —
        # снять всё и закрыть позицию рыночной, — потому что механика выхода
        # одна и та же, отличается только повод. Свой путь закрытия здесь значил
        # бы второе место, где можно забыть заявку в терминале или отказ лимита.
        tp_why = ""
        if so_mod.ensure_cash_basis(so):
            tp_why = so_mod.grid_tp_hit(so, price, coefs.get(so.code, 0.0))
            if tp_why:
                so_journal.record("grid_tp", so, so_journal.WATCHER,
                                  f"ЦЕЛЬ ПРИБЫЛИ: {tp_why}", now_ms=now)
                log.warning("smart_order.grid_tp", so_id=so.so_id, why=tp_why)

        # СТОП ЗА КРАЕМ СЕТКИ: снимаем всё и заканчиваем. Проверяется первым —
        # доставлять уровни туда, откуда рынок уже ушёл, значит ловить нож.
        if price > 0 and (tp_why or so_mod.grid_stop_hit(so, price)):
            why = "цель прибыли" if tp_why else "стоп за краем сетки"
            at = "прибыль у цели" if tp_why else "за последним уровнем"
            # СНЯТИЕ — ЧЕРЕЗ ОБЩИЙ ПУТЬ. Здесь стоял свой проход по своим записям,
            # и он снимал только то, что знал склад: после рестарта — ничего, а
            # следующей строкой выставлялся g_done, то есть сетка объявлялась снятой
            # и забывала живые заявки НАВСЕГДА. Признак заявки — строковое значение
            # (в live лежит и бухгалтерия: flip:, adopt:, blind:, moved:).
            stop_cids = {c for c in live.values() if isinstance(c, str) and c}
            killed, extra = _cancel_resting(srv, store, agent, so, stop_cids, work)
            for row in extra:
                so_journal.record("resting_withdrawn", so, so_journal.WATCHER,
                                  f"{why}: снята заявка {row['num']} из ТАБЛИЦЫ "
                                  f"ТЕРМИНАЛА ({row['side']} {row['balance']} по "
                                  f"{row['price']:g}): своих записей о ней не было",
                                  now_ms=now)
            so.g_live, so.g_done = {}, True
            # ПОЗИЦИЯ ЗАКРЫВАЕТСЯ, А НЕ ОСТАЁТСЯ НА ОПЕРАТОРЕ. Решение оператора
            # 01.10.2026 по разбору бэктестов: стоп срабатывает в половине дней, и
            # почти все убыточные дни — с выходом за последний уровень. Прежнее
            # поведение оставляло в такой день 10-15 лотов БЕЗ ЗАЩИТЫ ровно тогда,
            # когда рынок идёт в одну сторону.
            #
            # Закрываем РОВНО g_pos и только его: нетто счёта включает ручную
            # торговлю оператора (01.10 нетто RIZ6 −7 при позиции сетки −3), и
            # закрыть «по счёту» значило бы закрыть чужое.
            if so.g_pos:
                close_side = "sell" if so.g_pos > 0 else "buy"
                close_qty = abs(so.g_pos)
                cid = f"so:{so.so_id}:stopclose:{now % 100000}"
                try:
                    validate_place(lim, code=so.code, quantity=close_qty, collar=0.0,
                                   current_working=ost.working_contracts(agent),
                                   placed_today=ost.placed_today(agent),
                                   reducing=True)
                    ost.register_pending(agent, cid, so.code, close_side, 0.0, close_qty)
                    ost.record_placement(agent)
                    srv.enqueue_order(agent, order_msgs.build_place_order(
                        client_id=cid, code=so.code, side=close_side, price=0.0,
                        quantity=close_qty, collar=0.0, market=True))
                except LimitError as exc:
                    # ОТКАЗ ЗАКРЫТИЯ КРИЧИТ И ПРОБУЕТ СНОВА.
                    #
                    # Раньше заявка уходила в status=error и БОЛЬШЕ НИЧЕГО НЕ
                    # ДЕЛАЛА: g_done уже выставлен, а проход пропускает законченные.
                    # 02.10.2026 так и вышло — дневной кап отбил закрытие, сетка
                    # написала «позиция +30 БЕЗ ЗАЩИТЫ» и замолчала навсегда, а
                    # рынок ушёл на −9 423 ₽. Крик без повтора это не защита.
                    #
                    # Теперь статус остаётся "closing" без client_id: следующий
                    # проход попробует закрыть снова, и так до успеха. Причина
                    # отказа пишется ОДИН раз, чтобы не залить журнал.
                    so.status = "closing"
                    so.g_close_cid = ""
                    if "ЗАКРЫТИЕ ОТКЛОНЕНО" not in (so.note or ""):
                        so.note = (so.note + " " if so.note else "") + (
                            f"{why}: ЗАКРЫТИЕ ОТКЛОНЕНО ({exc}), позиция "
                            f"{so.g_pos:+d} БЕЗ ЗАЩИТЫ, пробую снова")
                        so_journal.record("error", so, so_journal.LIMITS,
                                          f"{why}: закрытие позиции "
                                          f"{so.g_pos:+d} отклонено лимитами: {exc}. "
                                          "ПОЗИЦИЯ БЕЗ ЗАЩИТЫ, повторяю каждый проход",
                                          now_ms=now)
                        log.error("smart_order.grid_stop_close_refused",
                                  so_id=so.so_id, pos=so.g_pos, error=str(exc))
                    return True
                so.g_close_cid = cid
                so.status = "closing"
                so.note = (so.note + " " if so.note else "") + (
                    f"{why}: закрываю {close_side} {close_qty} рыночной")
                so_journal.record(
                    "grid_stop", so, so_journal.WATCHER,
                    f"цена {price:g} {at}: сетка снята, позиция "
                    f"{so.g_pos:+d} ЗАКРЫВАЕТСЯ рыночной {close_side} {close_qty} "
                    f"({cid})", now_ms=now)
                log.warning("smart_order.grid_stopped", so_id=so.so_id, price=price,
                            pos=so.g_pos, close=cid)
                return True
            so.status = "cancelled"
            so.note = (so.note + " " if so.note else "") + f"{why}: снята"
            so_journal.record("grid_stop", so, so_journal.WATCHER,
                              f"цена {price:g} {at}: сетка снята, "
                              f"позиции не было", now_ms=now)
            log.warning("smart_order.grid_stopped", so_id=so.so_id, price=price, pos=0)
            return True

        # ТОЛЬКО НА ВЫХОД = ОДНА ЗАЯВКА НА ВСЮ ТЕКУЩУЮ ПОЗИЦИЮ ПО ЕЁ СРЕДНЕЙ.
        #
        # Оператор 02.10.2026: «только на выход — это выход только из текущей
        # позиции без убытка, а не по накопительному результату». Первая версия
        # выходила УРОВНЯМИ сетки не хуже средней — и в тот же день на RIZ6 при
        # позиции +9 по 84930 выход стоял на 85020/85110/85200, то есть ждал прибыли
        # сверх безубытка. Ещё раньше уровни ставились без счёта суммы: на GZZ6 при
        # +5 стояло 110 контрактов продаж (шорт −105 на росте).
        #
        # Теперь: все уровни сетки снимаются, стоит ровно одна заявка — закрывающая
        # сторона, объём |g_pos|, цена безубытка текущей позиции (breakeven_price).
        # Узнаём её по номеру в таблице терминала и метке «:bx» — склад заявок
        # пустеет при рестарте, а таблица живёт в QUIK. Исполнение считается тоже
        # по таблице (qty − balance), сколько бы раз ни перезапускались STL и агент.
        # За ценой не гоняемся: стоящая заявка остаётся, пока её объём равен
        # позиции и цена не хуже безубытка.
        if so.exit_only and term_all is not None:
            mine = list(term_all.get(so.so_id, []))
            bx_cid = str(live.get("bx") or "")
            rec_bx = (work.get(bx_cid) or {}) if bx_cid else {}
            bx_num = str(live.get("bx_num") or "") or str(rec_bx.get("order_id") or "")
            if not bx_num and not bx_cid:
                # Подхват после рестарта: своих записей нет, заявка есть в таблице.
                adopt = [r for r in mine if str(r.get("tag") or "").endswith(":bx")]
                if adopt:
                    bx_num = adopt[0]["num"]
            if bx_num and live.get("bx_num") != bx_num:
                live["bx_num"] = bx_num
                dirty = True
            row_bx = term_num.get(bx_num) if bx_num else None
            # Исполнение выхода — по таблице, приращением к уже учтённому.
            if row_bx is not None:
                filled_now = int(row_bx.get("qty") or 0) - int(row_bx.get("balance") or 0)
                delta = filled_now - int(live.get("bx_filled") or 0)
                if delta > 0:
                    side_was = str(row_bx.get("side") or "").lower()
                    px_was = float(row_bx.get("price") or 0)
                    # ponytail: цена филла = цена заявки; у немедленного выхода
                    # реальная цена лучше, позиция от этого не зависит.
                    if so_mod.ensure_cash_basis(so) and px_was > 0:
                        so.g_cash_pts += px_was * delta if side_was == "sell" else -px_was * delta
                    so.g_pos, so.g_avg = so_mod.blend_avg(
                        so.g_pos, so.g_avg, delta, px_was, side_was == "buy")
                    live["bx_filled"] = filled_now
                    dirty = True
                    so_journal.record(
                        "grid_fill", so, so_journal.WATCHER,
                        f"выход по безубытку исполнен: {side_was} {delta} по {px_was:g}; "
                        f"позиция {so.g_pos:+d}", now_ms=now)
            # ЖИЗНЬ ЗАЯВКИ ВЫХОДА. Первая версия считала выход отсутствующим, пока
            # его нет в таблице терминала, — а таблица отстаёт на секунды. Сторож
            # ходит чаще, и 02.10.2026 за эти секунды поставил СЕМЬ продаж 9 при
            # позиции +9 (часть брокер отбил нехваткой средств). Правило теперь:
            # заявка жива, пока о ней не известно, что она мертва. Таблица знает
            # строку — верим таблице; не знает — верим складу заявок; не знает
            # никто — мертва (рестарт без строки в QUIK).
            if row_bx is not None:
                bx_alive = bool(row_bx.get("active"))
                bx_dead = not bx_alive
                bx_state = str(row_bx.get("state") or "")
            elif rec_bx:
                bx_alive = _is_working(rec_bx) or rec_bx.get("state") == "filled"
                bx_dead = not bx_alive
                bx_state = str(rec_bx.get("state") or "")
            else:
                bx_alive = False
                bx_dead = bool(bx_cid or bx_num)
                bx_state = "неизвестна"
            if bx_dead:
                for k in ("bx", "bx_num", "bx_filled"):
                    live.pop(k, None)
                if bx_state in ("rejected", "expired"):
                    # Отказ или «нет ответа» — повтор не раньше чем через минуту:
                    # поток одинаковых отказов включает защиту брокера от
                    # зацикливания (15 минут молчания), и каждая транзакция стоит денег.
                    live["bx_retry_at"] = now + 60_000
                    so_journal.record(
                        "error", so, so_journal.WATCHER,
                        f"только на выход: заявка выхода {bx_state} "
                        f"({str(rec_bx.get('text') or '')[:120]}); повтор через минуту",
                        now_ms=now)
                bx_num, row_bx = "", None
                dirty = True
            _xs0, _xq0 = so_mod.exit_side_qty(so.g_pos)
            be = so_mod.breakeven_price(so.g_pos, so.g_avg, step, price,
                                        lim.price_collar_frac)
            bx_ok = bool(row_bx is not None and row_bx.get("active") and _xq0
                         and row_bx.get("side") == _xs0
                         and int(row_bx.get("balance") or 0) == _xq0
                         and so_mod.exit_without_loss(so.g_pos, so.g_avg,
                                                      float(row_bx.get("price") or 0)))
            # Всё, кроме годной заявки выхода, снимается (одно снятие на строку раз в
            # 30 с). Негодная заявка выхода (объём или цена разошлись с позицией)
            # тоже снимается, но остаётся «живой», пока таблица не скажет обратное:
            # новый выход до её смерти означал бы два выхода сразу.
            for r in mine:
                if bx_ok and r["num"] == bx_num:
                    continue
                k = f"xkill:{r['num']}"
                if now - int(live.get(k) or 0) < 30_000:
                    continue
                live[k] = now
                srv.enqueue_order(agent, order_msgs.build_cancel_order(
                    client_id=f"op:kill:{r['num']}", order_id=r["num"], code=r["sec"]))
                dirty = True
                log.info("smart_order.exit_only_withdrawn", so_id=so.so_id,
                         num=r["num"], price=r.get("price"))
            _alive = {r["num"] for r in mine}
            for _k in [k for k in live if k.startswith("xkill:") and k[6:] not in _alive]:
                live.pop(_k, None)
            # Поставить выход: позиция есть, ЖИВОЙ заявки выхода нет (ни в таблице,
            # ни в пути), строки сетки сняты (иначе на миг стояло бы больше
            # позиции), пауза после отказа прошла, биржа торгует.
            others = [r for r in mine if r["num"] != bx_num]
            if (_xq0 and not bx_alive and not others and be > 0
                    and now >= int(live.get("bx_retry_at") or 0)
                    and session_open is True):
                cid = f"so:{so.so_id}:bx:{now % 100000}"
                try:
                    validate_place(lim, code=so.code, quantity=_xq0,
                                   collar=lim.price_collar_frac,
                                   current_working=ost.working_contracts(agent),
                                   placed_today=ost.placed_today(agent), reducing=True)
                    ost.register_pending(agent, cid, so.code, _xs0, be, _xq0)
                    ost.record_placement(agent)
                    srv.enqueue_order(agent, order_msgs.build_place_order(
                        client_id=cid, code=so.code, side=_xs0, price=be,
                        quantity=_xq0, collar=lim.price_collar_frac))
                    live.update({"bx": cid, "bx_filled": 0})
                    live.pop("bx_num", None)
                    dirty = True
                    so_journal.record(
                        "exit_only", so, so_journal.WATCHER,
                        f"только на выход: {_xs0} {_xq0} по {be:g} — безубыток текущей "
                        f"позиции {so.g_pos:+d} (средняя {so.g_avg:g}), уровни сняты",
                        now_ms=now)
                except LimitError as exc:
                    live["bx_retry_at"] = now + 60_000
                    if live.get("bx_refused") != str(exc):
                        live["bx_refused"] = str(exc)
                        so_journal.record("error", so, so_journal.LIMITS,
                                          f"только на выход: выход {_xs0} {_xq0} по {be:g} "
                                          f"отклонён лимитами: {exc}", now_ms=now)
                    dirty = True

        # ОКНО: в QUIK только ближайшие уровни, дальние ждут в STL (см. g_window).
        # Стоящее за пределом «держать» снимается по номеру из таблицы терминала —
        # склад после рестарта пуст, а подхваченные строки записей не имеют.
        # СТОЯЩАЯ ЗАЯВКА, НАРУШАЮЩАЯ ЗАПРЕТ ПОВТОРА, СНИМАЕТСЯ: после рестарта и после
        # выката правила она могла остаться в стакане (одно снятие на строку раз в 30 с).
        if (term_all is not None and so.g_step > 0 and not so.exit_only
                and any(k.startswith("ls:") for k in live)):
            for r in term_all.get(so.so_id, []):
                lvl = round((float(r.get("price") or 0) - so.g_base) / so.g_step)
                if r.get("side") in ("buy", "sell") and so_mod.grid_repeat_blocked(
                        live, lvl, r["side"]):
                    k = f"xkill:{r['num']}"
                    if now - int(live.get(k) or 0) < 30_000:
                        continue
                    live[k] = now
                    srv.enqueue_order(agent, order_msgs.build_cancel_order(
                        client_id=f"op:kill:{r['num']}", order_id=r["num"], code=r["sec"]))
                    dirty = True
                    so_journal.record(
                        "resting_withdrawn", so, so_journal.WATCHER,
                        f"снята заявка {r['num']} уровня {lvl:+d} ({r['side']} по "
                        f"{r.get('price')}): он уже исполнился этой же стороной, встречной "
                        "сделки в сетке не было", now_ms=now)
        win_place, win_keep = so_mod.grid_window(so, live, price)
        if (so.g_window > 0 and price > 0 and term_all is not None and so.g_step > 0
                and not so.exit_only):
            for r in term_all.get(so.so_id, []):
                lvl = round((float(r.get("price") or 0) - so.g_base) / so.g_step)
                if lvl in win_keep:
                    continue
                k = f"xkill:{r['num']}"
                if now - int(live.get(k) or 0) < 30_000:
                    continue
                live[k] = now
                srv.enqueue_order(agent, order_msgs.build_cancel_order(
                    client_id=f"op:kill:{r['num']}", order_id=r["num"], code=r["sec"]))
                dirty = True
                log.info("smart_order.grid_window_withdrawn", so_id=so.so_id,
                         num=r["num"], level=lvl, price=r.get("price"))

        for level in so_mod.grid_levels(so):
            key = str(level)
            cid = live.get(key) or ""
            rec = work.get(cid) if cid else None
            if _is_working(rec) and int(rec.get("remaining") or 0) > 0:
                # Стоит в стакане. Налилась частично — в позицию сразу (см.
                # _grid_count_partial), уровень не гасим: остаток ещё работает.
                if int(rec.get("filled") or 0) > 0:
                    sw = str(rec.get("side") or "").lower()
                    if sw in ("buy", "sell") and _grid_count_partial(
                            so, live, f"pf:{cid}", level, sw, int(rec["filled"]), now,
                            price=float(rec.get("price") or 0)):
                        dirty = True
                continue
            if rec is not None and int(rec.get("filled") or 0) > 0:
                # ИСПОЛНИЛАСЬ. В «радиации» НЕТ ПОНЯТИЯ ТЕЙКА, есть уровни
                # (формулировка оператора 01.10.2026). Исполненный уровень
                # ИСЧЕЗАЕТ и возвращается только после филла СОСЕДНЕГО уровня —
                # любого, хоть ниже, хоть выше. Прежняя конструкция ставила
                # встречную заявку НА ТУ ЖЕ ЦЕНУ: круг с нулевой прибылью и
                # двойной комиссией, уровень −1 так отработал трижды по 86080.
                # СТОРОНУ БЕРЁМ ИЗ ФАКТА ИСПОЛНЕНИЯ, а не выводим заново.
                # 01.10.2026 я пересчитывал её через grid_side_for по ТЕКУЩЕЙ
                # цене — а цена с момента постановки уезжает, и сторона
                # переворачивается вместе с ней. Журнал записал «уровень +0
                # исполнен buy 1; позиция +1», тогда как сделка была ПРОДАЖА:
                # позиция сетки получила неверный знак, а от неё считаются и
                # объём, и стоп. Что исполнилось — знает запись заявки, и только
                # она; выводить это из рынка значит гадать о прошлом по
                # настоящему.
                side_was = str(rec.get("side") or "").lower()
                if side_was not in ("buy", "sell"):
                    side_was = so_mod.grid_side_for(so, level, price)
                    log.warning("smart_order.grid_fill_side_unknown", so_id=so.so_id,
                                level=level, guessed=side_was)
                _grid_count_fill(so, live, level, side_was, int(rec["filled"]), now,
                                 price=float(rec.get("price") or 0),
                                 already=int(live.pop(f"pf:{cid}", 0) or 0),
                                 num=str(rec.get("order_id") or ""))
                dirty = True
            # ВСТАВЛЕНО ДО ГЕЙТА «погасший уровень не выставляем» НАМЕРЕННО. Сначала
            # эта ветка стояла после него — и гейт успевал пропустить уровень, для
            # которого филл обнаруживался строкой ниже: уровень гас, а заявка на него
            # всё равно уходила. Та же ошибка, что 01.10.2026 заставила уровень
            # продать два контракта вместо одного: запись в локальную копию состояния
            # после того, как её уже прочитали (docs — «устаревшее состояние в своём
            # же проходе»).
            # ФИЛЛ ПОДХВАЧЕННОЙ ЗАЯВКИ — ДОГНАТЬ ПО ТАБЛИЦЕ.
            #
            # Подхваченной после рестарта заявке STL не возвращает client_id: в
            # brokerref QUIK 20 символов, он туда не влезает. Значит склад заявок о
            # её исполнении не узнает НИКОГДА, и без этой ветки уровень после филла
            # просто переставлялся на ту же цену: круг с нулевой прибылью и двойной
            # комиссией, который механика «радиации» прямо запрещает, плюс g_pos
            # оставался ложным, а от него считаются и объём, и стоп. Сегодня так
            # повели бы себя все 25 живых заявок сетки после рестарта STL.
            #
            # Исполнившаяся строка из таблицы не исчезает — она становится
            # НЕАКТИВНОЙ, и qty - balance говорит, сколько налилось.
            adopted = live.get(f"adopt:{level}")
            if isinstance(adopted, dict) and live.get(f"fin:{adopted.get('num')}"):
                # Подхваченная строка оказалась тенью заявки, налив которой уже
                # засчитан (см. _mark_counted): снимаем подхват, не считая второй раз.
                live.pop(f"adopt:{level}", None)
                dirty = True
                so_journal.record(
                    "adopted", so, so_journal.WATCHER,
                    f"подхват уровня {level:+d} снят: налив заявки {adopted['num']} уже "
                    "засчитан, строка таблицы отстаёт", now_ms=now)
                adopted = None
            if isinstance(adopted, dict) and adopted.get("num"):
                row = term_num.get(str(adopted["num"]))
                if (row is not None and row["active"] and row["filled"] > 0
                        and row["side"] in ("buy", "sell")):
                    if _grid_count_partial(so, live, f"pf:{row['num']}", level,
                                           row["side"], row["filled"], now,
                                           price=row["price"]):
                        dirty = True
                if row is not None and not row["active"]:
                    live.pop(f"adopt:{level}", None)
                    dirty = True
                    if row["filled"] > 0:
                        side_was = (row["side"] if row["side"] in ("buy", "sell")
                                    else so_mod.grid_side_for(so, level, price))
                        _grid_count_fill(so, live, level, side_was, row["filled"], now,
                                         f" (подхваченная заявка {row['num']}, "
                                         "узнали из таблицы терминала)",
                                         price=row["price"],
                                         already=int(live.pop(f"pf:{row['num']}", 0) or 0),
                                         num=str(row["num"]))
                    else:
                        so_journal.record(
                            "adopted", so, so_journal.WATCHER,
                            f"подхваченная заявка {row['num']} уровня {level:+d} снята "
                            "без исполнения — уровень снова свободен", now_ms=now)
                elif row is None and term_live is not None:
                    # Строки нет в таблице ВОВСЕ: за капом истории или день сменился.
                    # Молча забыть нельзя — сделаем это громко, уровень освободим.
                    live.pop(f"adopt:{level}", None)
                    dirty = True
                    so_journal.record(
                        "adopted", so, so_journal.WATCHER,
                        f"подхваченная заявка {adopted['num']} уровня {level:+d} "
                        "пропала из таблицы терминала: исполнение по ней НЕ учтено, "
                        "сверьте позицию", now_ms=now)
                    log.warning("smart_order.adopted_row_vanished", so_id=so.so_id,
                                level=level, num=adopted["num"])
            # Погасший уровень не выставляем: он ждёт филла соседа, а не повтора
            # входа по своей же цене.
            if not so_mod.grid_places_here(live, level):
                cid_old = live.pop(key, "")
                rec_old = work.get(cid_old) or {}
                if rec_old.get("order_id"):
                    srv.enqueue_order(agent, order_msgs.build_cancel_order(
                        client_id=cid_old, order_id=str(rec_old["order_id"]), code=so.code))
                    dirty = True
                continue
            side = so_mod.grid_side_for(so, level, price)
            if so_mod.grid_repeat_blocked(live, level, side):
                # Уровень только что исполнился ЭТОЙ ЖЕ стороной, и встречной сделки в
                # сетке с тех пор не было: повтор копил бы одну сторону (см. grid_repeat_blocked).
                if live.get(f"rep:{level}") != 1:
                    live[f"rep:{level}"] = 1
                    dirty = True
                    so_journal.record(
                        "held", so, so_journal.WATCHER,
                        f"уровень {level:+d} не выставлен: он уже исполнился этой же "
                        f"стороной ({side}), а встречной сделки в сетке не было — повтор "
                        "копил бы позицию в одну сторону", now_ms=now)
                continue
            live.pop(f"rep:{level}", None)
            px = so_mod.quantize(so_mod.grid_price(so, level), step, side)
            # Уровень по ту сторону рынка не выставляем по той же причине, что и
            # стенку коридора (инцидент 30.09.2026): лимит, пересекающий рынок,
            # исполняется мгновенно, и сетка вместо ожидания начинает лить.
            if price > 0 and ((side == "sell" and px <= price)
                              or (side == "buy" and px >= price)):
                continue
            # ПЕРЕД ПОСТАНОВКОЙ СПРАШИВАЕМ ТЕРМИНАЛ, А НЕ СВОЙ СКЛАД.
            #
            # Здесь стояла задержка на 90 с после старта процесса — и это было
            # лечение часов, а не причины. Причина: `work` (склад заявок STL) живёт
            # в ПАМЯТИ и пустеет при рестарте, поэтому уровень, живой в QUIK,
            # выглядел неподставленным и ставился ВТОРЫМ. 01.10.2026 так удвоилась
            # сетка из 24 заявок, и оператор получил лишние контракты. Задержка
            # лишь отодвигала тот же выстрел на минуту с половиной, а заявка в
            # QUIK живёт сутками: пережить 90 с ей ничего не стоит.
            #
            # Таблица заявок терминала живёт в QUIK и переживает рестарт и STL, и
            # агента. Нет живой строки на этой цене — ставим; есть — уровень уже
            # стоит, и второй не нужен. Зеркала нет вовсе — это «НЕ ЗНАЮ», и
            # ставить вслепую нельзя (отличать от пустой таблицы обязательно).
            # ТОЛЬКО НА ВЫХОД: ставим лишь то, что ЗАКРЫВАЕТ позицию, и только по
            # цене БЕЗ УБЫТКА. Требование оператора 02.10.2026 дословно: «кнопка
            # „только на выход“ — это значит выход без убытка».
            #
            # Три отказа подряд, и каждый по своей причине:
            #   позиции нет        — закрывать нечего, а открывать запрещено;
            #   уровень доливает   — он увеличил бы позицию, а не сократил;
            #   цена хуже средней  — закрытие здесь дало бы убыток.
            # Молчать нельзя ни в одном: оператор включил режим и ждёт выхода, и
            # «ничего не происходит» он обязан уметь объяснить сам, по журналу.
            if level not in win_place:
                continue              # дальний уровень ждёт в STL, пока рынок не подойдёт
            if so.exit_only:
                # Уровни в этом режиме не ставятся: выход — одна заявка по средней
                # (см. блок «только на выход» перед циклом).
                continue
            # БИРЖА НЕ ТОРГУЕТ — НЕ СТАВИМ. Гейт стоит на ПОСТАНОВКЕ и только на
            # ней: учёт филлов и снятие экспозиции запрещать нельзя никогда.
            #
            # 02.10.2026 этого гейта не было, и ночью после закрытия вечерней сессии
            # сетка молотила постановками в закрытую биржу: восемь отказов брокера
            # «[GW][3] Сейчас эта сессия не идёт», защита от зацикливания остановила
            # источник, записи уровней ушли в expired — и сетка простояла до 07:00.
            # Брокер берёт деньги за транзакции сверх лимита частоты, так что это не
            # только простой.
            #
            # АУКЦИОН ОТКРЫТИЯ ТОРГАМИ НЕ ЯВЛЯЕТСЯ (market_session._TRADING_TYPES), и
            # это ровно то, что нужно: в аукционе биржа принимает НЕ ВСЁ (оператор,
            # 02.10: «на этапе аукциона заявки принимаются только лонговые»), поэтому
            # половина сетки была бы отбита. Не ставим в аукционе ничего — и не надо
            # разбираться, что именно он примет. Разрешать здесь аукцион нельзя.
            #
            # `None` = расписания нет: это «НЕ ЗНАЮ», и оно тоже запрещает.
            if session_open is not True:
                if live.get(f"closed:{level}") != 1:
                    live[f"closed:{level}"] = 1
                    dirty = True
                    so_journal.record(
                        "held", so, so_journal.WATCHER,
                        f"уровень {level:+d} не выставлен: биржа не торгует "
                        f"(расписание: {'неизвестно' if session_open is None else 'закрыто'})",
                        now_ms=now)
                continue
            live.pop(f"closed:{level}", None)
            if term_live is None:
                if live.get(f"blind:{level}") != 1:
                    live[f"blind:{level}"] = 1
                    dirty = True
                    so_journal.record(
                        "held", so, so_journal.WATCHER,
                        f"уровень {level:+d} не выставлен: таблицы заявок терминала "
                        "нет (зеркало агента молчит). Что стоит в QUIK — неизвестно, "
                        "ставить вслепую нельзя.", now_ms=now)
                continue
            live.pop(f"blind:{level}", None)
            # ponytail: опознание уровня по ЦЕНЕ. client_id в brokerref QUIK не
            # влезает (20 символов, см. terminal.so_id_of), поэтому подхваченной
            # после рестарта заявке STL не возвращает её client_id — и филл такой
            # заявки учитывается не записью склада, а исчезновением строки из
            # таблицы. Полный учёт филлов по журналу сделок — отдельная работа.
            # СТОРОНУ НЕ ПЕРЕДАЁМ — см. terminal.find_level: сторона уровня следует
            # рынку, и при его смещении сверка по стороне разрешила бы поставить
            # вторую заявку на ту же цену, в противоположную сторону.
            standing = terminal.find_level(
                [r for r in term_live if not live.get(f"fin:{r['num']}")], px, step)
            if standing is not None:
                # ЗНАЧЕНИЕ — СЛОВАРЬ, а не строка: _withdraw_resting считает любую
                # строку в live идентификатором заявки (и уже один раз поперхнулся
                # числом в moved:). Номер здесь нужен, чтобы догнать филл этой заявки
                # по таблице, — словарь и несёт его, не притворяясь client_id.
                prev = live.get(f"adopt:{level}")
                if not isinstance(prev, dict) or prev.get("num") != standing["num"]:
                    live[f"adopt:{level}"] = {"num": standing["num"]}
                    dirty = True
                    so_journal.record(
                        "adopted", so, so_journal.WATCHER,
                        f"уровень {level:+d} ({px:g}) уже СТОИТ в терминале заявкой "
                        f"{standing['num']} ({standing['side']} "
                        f"{standing['balance']}): второй не ставим", now_ms=now)
                # У ПОДХВАЧЕННОГО УРОВНЯ ПУТЬ УЧЁТА РОВНО ОДИН.
                #
                # Книга лежит на диске и переживает рестарт, поэтому старый client_id
                # уровня остаётся в live рядом с меткой adopt — а запись о филле
                # приходит и в склад заявок (агент не перезапускали, его карта цела),
                # и в таблицу терминала. 01.10.2026 это посчитало ОДИН филл ДВАЖДЫ:
                # «уровень +3 (85840) исполнен buy 1» в 23:40:56 и ещё раз в 23:41:06,
                # тогда как в рынке была одна сделка 23:40:55. Книга записала 27
                # филлов против 26 настоящих, и g_pos разошёлся с журналом: −1 против
                # −2. От g_pos считаются объём встречной заявки и объём закрытия по
                # стопу, то есть ошибка уходит в рыночную заявку на живые деньги.
                #
                # Подхваченная заявка опознаётся НОМЕРОМ, client_id ей не нужен:
                # снятие второй проход делает по таблице (_cancel_resting).
                if live.pop(key, None):
                    dirty = True
                continue
            if not price_within_limits(px, limits):
                # ЗА ПЛАНКОЙ БИРЖИ. Не выставляем и не считаем это ошибкой:
                # планки двигает MOEX по своему расписанию, и уровень оживёт сам,
                # когда границы расширятся — проход сторожа идёт каждые пять
                # секунд, ждать больше нечего. Говорим оператору ОДИН раз на
                # уровень, иначе журнал утонет в повторах.
                if live.get(f"limit:{level}") != 1:
                    live[f"limit:{level}"] = 1
                    dirty = True
                    so_journal.record("grid_limit", so, so_journal.LIMITS,
                                      f"уровень {level:+d} ({px:g}) за планкой биржи "
                                      f"{limits}: держу у себя, жду расширения границ",
                                      now_ms=now)
                    log.info("smart_order.grid_level_beyond_limit", so_id=so.so_id,
                             level=level, price=px, limits=limits)
                continue
            if live.pop(f"limit:{level}", None):
                dirty = True
                so_journal.record("grid_limit", so, so_journal.WATCHER,
                                  f"уровень {level:+d} ({px:g}) снова внутри планок: "
                                  "выставляю", now_ms=now)
            new_cid = _grid_cid(so.so_id, level) + f":{now % 100000}"
            try:
                validate_place(lim, code=so.code, quantity=so.g_lot,
                               collar=lim.price_collar_frac,
                               current_working=ost.working_contracts(agent),
                               placed_today=ost.placed_today(agent))
                ost.register_pending(agent, new_cid, so.code, side, px, so.g_lot)
                ost.record_placement(agent)
                srv.enqueue_order(agent, order_msgs.build_place_order(
                    client_id=new_cid, code=so.code, side=side, price=px,
                    quantity=so.g_lot, collar=lim.price_collar_frac))
            except LimitError as exc:
                # Кап — не повод сносить всю сетку: часть уровней стоит и
                # работает. Говорим один раз на уровень и идём дальше.
                log.warning("smart_order.grid_level_refused", so_id=so.so_id,
                            level=level, error=str(exc))
                so_journal.record("error", so, so_journal.LIMITS,
                                  f"уровень {level:+d} не выставлен: {exc}", now_ms=now)
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("smart_order.grid_place_failed", so_id=so.so_id,
                            level=level, error=str(exc))
                continue
            live[key] = new_cid
            dirty = True
        if live != (so.g_live or {}):
            so.g_live = live
            dirty = True
    return dirty



_WALL_MOVE_EVERY_MS = 10_000   # как часто двигать заявку вслед за наклоном


def _wall_count_fill(so: SmartOrder, live: dict, wall: str, side_was: str,
                     got: int, price: float, now: int, how: str = "",
                     num: str = "") -> None:
    """Провести исполнение стенки коридора: позиция, счётчик переворотов, конец.

    ЕДИНСТВЕННЫЙ путь учёта, и источников у него два: запись склада заявок и
    исчезновение строки из таблицы терминала (подхваченная после рестарта заявка
    своего client_id не имеет). Считать они обязаны одинаково — от c_pos зависят и
    объём встречной заявки, и гейт «на стенке, от которой мы уже в позиции, заявки
    быть не должно».

    Считаем ФАКТИЧЕСКИ налитое, а не базовый объём: corridor_after_fire ставит
    позицию равной базе целиком и на неполном наливе соврал бы.
    """
    was = so.c_pos
    so.c_pos, so.c_avg = so_mod.blend_avg(
        so.c_pos, so.c_avg, got, price, side_was == "buy")
    if was != 0 and (was > 0) != (so.c_pos > 0) and so.c_pos != 0:
        so.c_flips += 1
        if so.c_flips_max and so.c_flips >= so.c_flips_max:
            so.c_done = True              # лимит переворотов выбран
    live.pop(wall, None)                  # заявки на этой стенке больше нет
    live.pop(f"moved:{wall}", None)
    _mark_counted(live, num)
    so_journal.record(
        "corridor", so, so_journal.WATCHER,
        f"стенка {wall} исполнена {side_was} {got} по {price:g}; "
        f"позиция {so.c_pos:+d}, переворотов {so.c_flips}"
        + (f"/{so.c_flips_max}" if so.c_flips_max else "") + how, now_ms=now)


def _walls_sync(book: SmartOrderBook, store: Any, ost: Any, srv: Any, lim: Any,
                agent: str, steps: dict[str, float],
                price_limits: dict[str, tuple[float, float]], now: int,
                schedule: dict | None = None,
                session_open: bool | None = None) -> bool:
    """Держать заявки коридора и треугольника В СТАКАНЕ, на обеих стенках.

    До 30.09.2026 сторож ждал касания и стрелял в тот же миг — то есть вставал в
    очередь последним ровно там, где важна очередь. Оператор потребовал держать
    заявки заранее; стенки при этом движутся, поэтому заявка не просто ставится,
    а ПЕРЕСТАВЛЯЕТСЯ вслед за линией, пока цена до неё не дошла.

    Объём считает та же логика, что вела сторожа: на пустой позиции это базовый
    объём, в позиции — вдвое (переворот обязан и закрыть, и открыть).
    """
    dirty = False
    work = {d.get("client_id"): d for d in ost.working_orders(agent)}
    # Что стоит в терминале — один запрос на проход; None = «не знаю» (см. _grid_sync).
    _term_ok = terminal.fresh(store, agent, now)
    term_all = terminal.by_smart_order(store, agent) if _term_ok else None
    term_num = terminal.by_num(store, agent) if _term_ok else {}
    for so in book.orders:
        if so.kind not in ("corridor", "triangle") or so.status not in ("armed", "native"):
            continue
        if so.c_done:
            continue
        term_live = None if term_all is None else term_all.get(so.so_id, [])
        step = steps.get(so.code, 0.0)
        limits = price_limits.get(so.code)
        low, top = so_mod.corridor_bounds(so, now, schedule)
        if low >= top:
            continue                      # апекс: стенок больше нет, ведёт сторож
        live = dict(so.c_live or {})
        base = so.c_qty or so.qty
        tick = store.tick(so.code, agent) or {}
        last = float(tick.get("last") or 0)
        bid, ask = float(tick.get("bid") or 0), float(tick.get("ask") or 0)
        if last <= 0 and not (bid or ask):
            # Без котировки не понять, по ту или эту сторону рынка стенка.
            # Выставлять вслепую нельзя: ровно так 30.09.2026 продались 43
            # контракта. Ждём кадр — он приходит каждые полсекунды.
            continue
        for wall, px_raw, side in (("top", top, "sell"), ("low", low, "buy")):
            # СТЕНКА ИСПОЛНИЛАСЬ — УЧЕСТЬ И ОТПУСТИТЬ. Этой ветки не было вовсе, и
            # это второй случай той же болезни, что у сетки: 30.09.2026 стенки
            # научились СТОЯТЬ в стакане, а учёт их исполнения остался на старом
            # пути «выстрел по касанию» (corridor_after_fire в блоке status=fired).
            # Позиция коридора c_pos двигалась только там, поэтому у стоящей стенки
            # она оставалась нулём — а запрет ниже («на стенке, от которой мы уже в
            # позиции, заявки быть не должно») гейтится именно c_pos. Стенка
            # наливалась, сторож видел ноль и ставил её заново, та наливалась опять:
            # каждое касание удваивало объём, и остановить это было нечем.
            #
            # Считаем ФАКТИЧЕСКИ исполненное и сторону ИЗ ЗАПИСИ ЗАЯВКИ, а не из
            # рынка: цена с момента постановки уезжает, и выведенная заново сторона
            # переворачивает знак позиции (та же ошибка стоила сетке неверного
            # знака 01.10.2026). Частичное исполнение тоже учитываем по факту —
            # corridor_after_fire ставит позицию равной базовому объёму целиком и
            # на половине налива соврал бы.
            cid_f = live.get(wall) or ""
            rec_f = work.get(cid_f) if cid_f else None
            # ЧАСТИЧНЫЙ НАЛИВ НЕ ЗАКРЫВАЕТ ЗАЯВКУ. Проверка шла по filled > 0 без
            # оглядки на остаток: продажа 10, налилось 4 — ветка писала позицию −4 и
            # ЗАБЫВАЛА cid, а остальные 6 продолжали стоять в рынке уже никому не
            # известные. Снять их было нечем (гейт c_pos ниже хочет снять верх, а
            # идентификатора у него больше нет), и когда они наливались, позиция
            # оставалась −4 вместо −10: от неё считаются и объём встречной заявки, и
            # стоп. Образец — сетка (_grid_sync): там сначала «стоит в стакане»,
            # и только потом учёт филла. Здесь порядок был обратный.
            still_working = _is_working(rec_f) and int(rec_f.get("remaining") or 0) > 0
            if rec_f is not None and not still_working and int(rec_f.get("filled") or 0) > 0:
                got = int(rec_f["filled"])
                side_was = str(rec_f.get("side") or "").lower()
                if side_was not in ("buy", "sell"):
                    side_was = side
                    log.warning("smart_order.wall_fill_side_unknown",
                                so_id=so.so_id, wall=wall, assumed=side)
                _wall_count_fill(so, live, wall, side_was, got,
                                 float(rec_f.get("price") or 0), now,
                                 num=str(rec_f.get("order_id") or ""))
                dirty = True
                continue
            # На стенке, от которой мы уже в позиции, заявки быть не должно:
            # иначе она нарастила бы позицию там, где по правилам коридора мы
            # только ждём противоположную стенку.
            if (wall == "top" and so.c_pos < 0) or (wall == "low" and so.c_pos > 0):
                cid = live.pop(wall, "")
                rec = work.get(cid) or {}
                if rec.get("order_id"):
                    srv.enqueue_order(agent, order_msgs.build_cancel_order(
                        client_id=cid, order_id=str(rec["order_id"]), code=so.code))
                    dirty = True
                continue
            qty = base + abs(so.c_pos)
            px = so_mod.quantize(px_raw, step, side)
            # ЗАЯВКА НА СТЕНКЕ НЕ ИМЕЕТ ПРАВА ПЕРЕСЕКАТЬ РЫНОК. 30.09.2026 это
            # стоило оператору 43 контрактов: верхняя стенка треугольника
            # оказалась НИЖЕ цены, продажа на ней стала маркетабельной и
            # исполнялась мгновенно — а сторож каждые десять секунд ставил
            # следующую. Лимит на продажу обязан стоять ВЫШЕ рынка, на покупку
            # НИЖЕ; иначе это не «ждём касания», а вход по рынку прямо сейчас.
            # Цена ушла за стенку — значит фигура нарушена, и решает стоп или
            # оператор, но не молчаливая череда заявок.
            ref_sell = max(bid, last) or last
            ref_buy = min(ask, last) if (ask and last) else (ask or last)
            crosses = (side == "sell" and ref_sell > 0 and px <= ref_sell) or                       (side == "buy" and ref_buy > 0 and px >= ref_buy)
            if crosses:
                if live.get(f"cross:{wall}") != 1:
                    live[f"cross:{wall}"] = 1
                    dirty = True
                    so_journal.record("wall_crossed", so, so_journal.WATCHER,
                                      f"стенка {wall} ({px:g}) по ту сторону рынка "
                                      f"({last:g}): заявку НЕ ставлю — она исполнилась бы "
                                      "мгновенно. Цена вне фигуры.", now_ms=now)
                    log.warning("smart_order.wall_crosses_market", so_id=so.so_id,
                                wall=wall, wall_price=px, last=last)
                # висящую заявку на этой стенке снимаем: рынок ушёл за неё
                cid_old = live.pop(wall, "")
                rec_old = work.get(cid_old) or {}
                if rec_old.get("order_id"):
                    srv.enqueue_order(agent, order_msgs.build_cancel_order(
                        client_id=cid_old, order_id=str(rec_old["order_id"]), code=so.code))
                continue
            if live.pop(f"cross:{wall}", None):
                dirty = True
            if not price_within_limits(px, limits):
                continue                  # за планкой биржи: подождём расширения
            cid = live.get(wall) or ""
            rec = work.get(cid) if cid else None
            alive = _is_working(rec) and int(rec.get("remaining") or 0) > 0
            if alive and abs(float(rec.get("price") or 0) - px) < (step or 1) / 2                     and int(rec.get("remaining") or 0) == qty:
                continue                  # стоит там, где надо, и нужного размера
            if alive and rec.get("order_id"):
                # Стенка уехала (наклон) или изменился объём — двигаем ОДНОЙ
                # транзакцией, а не снять-поставить: между двумя действиями есть
                # тик, в который защита отсутствует. Но не чаще раза в
                # _WALL_MOVE_EVERY_MS: наклонная ползёт непрерывно, и двигать
                # заявку каждым проходом значит гонять транзакции ради долей
                # шага (оператор назвал десять секунд, 30.09.2026).
                if now - int(live.get(f"moved:{wall}") or 0) < _WALL_MOVE_EVERY_MS:
                    continue
                try:
                    srv.enqueue_order(agent, order_msgs.build_replace_order(
                        client_id=cid, order_id=str(rec["order_id"]),
                        new_price=px, new_quantity=qty))
                    live[f"moved:{wall}"] = now
                    dirty = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("smart_order.wall_move_failed", so_id=so.so_id, error=str(exc))
                continue
            # ИСПОЛНЕНИЕ ПОДХВАЧЕННОЙ СТЕНКИ — ДОГНАТЬ ПО ТАБЛИЦЕ. Без этого стенка,
            # подхваченная после рестарта, после налива переставлялась бы заново, а
            # c_pos оставался нулём: тот самый механизм, которым каждое касание
            # удваивало объём, только зашедший через подхват.
            adopted = live.get(f"adopt:{wall}")
            if isinstance(adopted, dict) and live.get(f"fin:{adopted.get('num')}"):
                live.pop(f"adopt:{wall}", None)
                dirty = True
                so_journal.record(
                    "adopted", so, so_journal.WATCHER,
                    f"подхват стенки {wall} снят: налив заявки {adopted['num']} уже "
                    "засчитан, строка таблицы отстаёт", now_ms=now)
                adopted = None
            if isinstance(adopted, dict) and adopted.get("num"):
                row = term_num.get(str(adopted["num"]))
                if row is not None and not row["active"]:
                    live.pop(f"adopt:{wall}", None)
                    dirty = True
                    if row["filled"] > 0:
                        side_was = row["side"] if row["side"] in ("buy", "sell") else side
                        _wall_count_fill(so, live, wall, side_was, row["filled"],
                                         row["price"], now,
                                         f" (подхваченная заявка {row['num']}, "
                                         "узнали из таблицы терминала)",
                                         num=str(row["num"]))
                        continue
                    so_journal.record(
                        "adopted", so, so_journal.WATCHER,
                        f"подхваченная заявка {row['num']} стенки {wall} снята без "
                        "исполнения — стенка снова свободна", now_ms=now)
                elif row is None:
                    live.pop(f"adopt:{wall}", None)
                    dirty = True
                    so_journal.record(
                        "adopted", so, so_journal.WATCHER,
                        f"подхваченная заявка {adopted['num']} стенки {wall} пропала "
                        "из таблицы терминала: исполнение по ней НЕ учтено, сверьте "
                        "позицию", now_ms=now)
                    log.warning("smart_order.adopted_row_vanished", so_id=so.so_id,
                                wall=wall, num=adopted["num"])
            # ПЕРЕД ПОСТАНОВКОЙ СПРАШИВАЕМ ТЕРМИНАЛ, А НЕ СВОЙ СКЛАД. Здесь стояла
            # задержка на 90 с после старта процесса — лечение часов, а не причины:
            # склад заявок живёт в ПАМЯТИ и пустеет при рестарте, поэтому своя же
            # живая заявка выглядела отсутствующей и ставилась ВТОРОЙ. Заявке в QUIK
            # пережить 90 с ничего не стоит — она живёт сутками. Таблица заявок
            # терминала живёт в QUIK и переживает рестарт и STL, и агента.
            # Только на выход — см. тот же гейт в сетке: ставим лишь закрывающую
            # сторону и только по цене не хуже средней входа.
            if so.exit_only:
                _xs, _xq = so_mod.exit_side_qty(so.c_pos)
                why = ""
                if _xq == 0:
                    why = "позиции нет, открывать нечего"
                elif side != _xs:
                    why = f"стенка открывает ({side}), а позиция {so.c_pos:+d} закрывается {_xs}"
                elif not so_mod.exit_without_loss(so.c_pos, so.c_avg, px):
                    why = (f"цена {px:g} хуже средней {so.c_avg:g}: закрытие здесь "
                           "дало бы убыток")
                if why:
                    if live.get(f"exit:{wall}") != 1:
                        live[f"exit:{wall}"] = 1
                        dirty = True
                        so_journal.record(
                            "held", so, so_journal.WATCHER,
                            f"только на выход: стенка {wall} не выставлена — {why}",
                            now_ms=now)
                    continue
                live.pop(f"exit:{wall}", None)
                qty = min(qty, _xq)      # закрываем РОВНО позицию, не больше
            # Биржа не торгует — не ставим; аукцион торгами не является (см. тот же
            # гейт в сетке). None = «не знаю», тоже запрет.
            if session_open is not True:
                if live.get(f"closed:{wall}") != 1:
                    live[f"closed:{wall}"] = 1
                    dirty = True
                    so_journal.record(
                        "held", so, so_journal.WATCHER,
                        f"стенка {wall} не выставлена: биржа не торгует "
                        f"(расписание: {'неизвестно' if session_open is None else 'закрыто'})",
                        now_ms=now)
                continue
            live.pop(f"closed:{wall}", None)
            if term_live is None:
                if live.get(f"warm:{wall}") != 1:
                    live[f"warm:{wall}"] = 1
                    dirty = True
                    so_journal.record(
                        "held", so, so_journal.WATCHER,
                        f"стенка {wall} не выставлена: таблицы заявок терминала нет "
                        "(зеркало агента молчит). Что стоит в QUIK — неизвестно, "
                        "ставить вслепую нельзя.", now_ms=now)
                continue
            live.pop(f"warm:{wall}", None)
            # ponytail: стенка опознаётся по ЦЕНЕ — client_id в brokerref QUIK не
            # влезает (20 символов, см. terminal.so_id_of).
            standing = terminal.find_level(                     # без стороны, см. выше
                [r for r in term_live if not live.get(f"fin:{r['num']}")], px, step)
            if standing is not None:
                # Номер, а не единица: по нему догоняется исполнение этой заявки —
                # своего client_id у подхваченной нет (brokerref QUIK, 20 символов),
                # и склад заявок о её филле не узнает никогда. Словарь, а не строка:
                # строку в live считают идентификатором заявки.
                prev = live.get(f"adopt:{wall}")
                if not isinstance(prev, dict) or prev.get("num") != standing["num"]:
                    live[f"adopt:{wall}"] = {"num": standing["num"]}
                    dirty = True
                    so_journal.record(
                        "adopted", so, so_journal.WATCHER,
                        f"стенка {wall} ({px:g}) уже СТОИТ в терминале заявкой "
                        f"{standing['num']} ({standing['side']} "
                        f"{standing['balance']}): вторую не ставим", now_ms=now)
                # Путь учёта ровно один — см. ту же правку в сетке: иначе филл
                # подхваченной стенки посчитают и склад заявок по старому client_id,
                # и догон по таблице.
                if live.pop(wall, None):
                    dirty = True
                continue
            # СВОЯ ЗАЯВКА НЕ НА ЭТОЙ ЦЕНЕ — СНЯТЬ, А НЕ ОСТАВИТЬ СИРОТОЙ.
            #
            # У наклонного коридора и треугольника стенка ползёт, и после рестарта
            # цена уехала от той, по которой заявка стоит. Подхват по цене её не
            # узнаёт, и дальше ставилась ВТОРАЯ стенка, а первая оставалась жить
            # никому не известной: налиться могли обе. Своих заявок на стенке
            # положено ноль или одна, поэтому всё своё не на текущей цене снимаем —
            # по номеру и инструменту, без чьей-либо памяти.
            #
            # Что считать «своим»: только строки этой умной заявки (тег stl-so-<id>),
            # и только по ЭТУ сторону — чужую стенку и заявку оператора не трогаем.
            for _orphan in term_live:
                if terminal.matches_price(_orphan["price"], px, step):
                    continue                      # эта и есть наша, её уже искали
                if _orphan["side"] and _orphan["side"] != side:
                    continue                      # заявка другой стенки, не этой
                srv.enqueue_order(agent, order_msgs.build_cancel_order(
                    client_id=f"op:kill:{_orphan['num']}", order_id=_orphan["num"],
                    code=_orphan["sec"]))
                dirty = True
                so_journal.record(
                    "resting_withdrawn", so, so_journal.WATCHER,
                    f"снята своя заявка {_orphan['num']} ({_orphan['side']} "
                    f"{_orphan['balance']} по {_orphan['price']:g}): стенка {wall} "
                    f"уехала на {px:g}, двух заявок на одной стенке быть не должно",
                    now_ms=now)
            # Хвост остаётся СЛУЧАЙНЫМ намеренно. Детерминированное имя выглядит
            # аккуратнее, но менять схему имён, пока в рынке живут заявки со
            # старой, значит ровно воспроизвести чинимый баг: сторож счёл бы их
            # чужими и поставил бы дубли (01.10.2026, 26 живых заявок у
            # оператора в момент правки).
            new_cid = f"so:{so.so_id}:{wall}:{now % 100000}"
            try:
                validate_place(lim, code=so.code, quantity=qty,
                               collar=lim.price_collar_frac,
                               current_working=ost.working_contracts(agent),
                               placed_today=ost.placed_today(agent))
                ost.register_pending(agent, new_cid, so.code, side, px, qty)
                ost.record_placement(agent)
                srv.enqueue_order(agent, order_msgs.build_place_order(
                    client_id=new_cid, code=so.code, side=side, price=px,
                    quantity=qty, collar=lim.price_collar_frac))
            except LimitError as exc:
                log.warning("smart_order.wall_refused", so_id=so.so_id,
                            wall=wall, error=str(exc))
                so_journal.record("error", so, so_journal.LIMITS,
                                  f"стенка {wall} не выставлена: {exc}", now_ms=now)
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("smart_order.wall_place_failed", so_id=so.so_id, error=str(exc))
                continue
            live[wall] = new_cid
            dirty = True
        if live != (so.c_live or {}):
            so.c_live = live
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
    native_rows = _stop_rows_live(store, agent)
    dirty_meta = _mark_orphans(book, ost, agent, so_mod.now_ms())
    dirty_meta = _track_fills(book, ost, store, agent) or dirty_meta
    dirty_meta = _revive_false_orphans(book) or dirty_meta
    # Защитная заявка обязана умирать вместе с позицией, которую охраняет
    # (инцидент 01.10.2026: стопы на 53 контракта простояли взведёнными после
    # исчезновения шорта и открыли бы лонг с нуля).
    dirty_meta = _retire_unprotecting(book, store, agent,
                                      so_mod.now_ms()) or dirty_meta
    steps_all = _price_steps(store, agent)
    dirty_meta = _snap_entries_to_grid(book, steps_all) or dirty_meta
    # Защита открытой позиции переезжает в терминал: он держит стоп-заявку сам и
    # переживает падение STL. Порядок важен - сначала уточнённая цена входа
    # (_track_fills/_snap_entries_to_grid), потом передача от неё.
    dirty_meta = _handover_to_terminal(book, steps_all, ost, srv, agent, so_mod.now_ms()) or dirty_meta
    # Одиночный стоп или тейк оператора на уже открытую позицию - туда же.
    dirty_meta = _handover_standalone(book, steps_all, store, ost, srv, agent,
                                      so_mod.now_ms()) or dirty_meta
    # Сверка книги с терминалом — после всех переводов статусов этого прохода,
    # иначе она ругалась бы на промежуточные состояния.
    await _report_audit(srv, agent, book, _stop_rows_live(store, agent))
    for parent in _track_native(book, store, agent, so_mod.now_ms()):
        dirty_meta = True
        # Причины разные, и оператору важно ИМЕННО какая: не принял терминал,
        # сняло сроком (заявка жива и снова у STL) или исполнилось непонятно чем.
        await _alert_reject(srv, agent, parent, {
            "failed": "терминал не принял стоп-заявку защиты: защиту ведёт STL",
            "": "стоп-заявка снялась в терминале (срок - торговый день), сделок нет: "
                "заявка снова взведена, её ведёт STL",
        }.get(parent.native_state,
              "стоп-заявка исчезла из терминала и по инструменту есть сделка: "
              "проверьте позицию"))
    if dirty_meta:
        book.save()
    if not active:
        return
    if ost.is_blocked(agent):
        return  # kill-switch: keep orders armed, fire nothing

    lim = OrderLimits.from_settings(state.settings)
    steps = _price_steps(store, agent)
    # Незалившаяся защита — раньше новых срабатываний: открытая позиция без
    # исполненного стопа опаснее пропущенного входа.
    if _escalate_protection(book, store, ost, srv, lim, agent, steps, so_mod.now_ms()):
        book.save()
    # ...и то же самое для стопа, отданного ТЕРМИНАЛУ: он тоже выставляет лимит,
    # и его тоже могут не налить.
    if _escalate_native_child(book, store, srv, lim, agent,
                              _stop_rows_by_tag(store, agent), so_mod.now_ms()):
        book.save()
    # ТОРГУЕТ ЛИ БИРЖА — по оракулу расписания, не по свежести кадра. Считается
    # ЗДЕСЬ, до проходов: заявки в стакан ставят они, и им это знать обязательно.
    session_open = (getattr(state, "market_session", None) or {}).get("open")
    # Сетка: доставить недостающие уровни, перевернуть исполненные, снять по стопу.
    _limits_now = _price_limits(store, agent)
    if _grid_sync(book, store, ost, srv, lim, agent, steps,
                  _limits_now, so_mod.now_ms(), session_open):
        book.save()
    # Коридор и треугольник — тоже в стакан: заявка на стенке стоит заранее и
    # переставляется вслед за линией.
    if _walls_sync(book, store, ost, srv, lim, agent, steps, _limits_now,
                   so_mod.now_ms(), getattr(state, "market_schedule", None),
                   session_open):
        book.save()
    filled = {d["client_id"] for d in ost.working_orders(agent)
              if d.get("state") == "filled"}
    now = so_mod.now_ms()
    dirty = False

    # КАРАНТИН ПОСЛЕ СЛЕПОТЫ. 25.09.2026 связь вернулась после трёх часов тишины,
    # и через одиннадцать минут сторож купил 20 контрактов по уровню, пройденному
    # в дыре, — пока оператор в дороге уже набирал позицию руками. Формально
    # правильно, фактически исполнено намерение, устаревшее за три часа.
    quar = getattr(state, "blind_quarantine", None)
    if quar is None:
        quar = state.blind_quarantine = bq.BlindQuarantine()
    # ПУСТАЯ КНИГА — ЭТО «НЕЧЕГО ПРОВЕРЯТЬ», А НЕ «ДАННЫХ НЕТ».
    #
    # 01.10.2026: в 08:01 оператор снял две последние живые заявки, книга
    # опустела, и `book.codes()` стал пустым множеством. `any(... for c in ())`
    # это False, поэтому карантин каждую секунду считал, что рынка не видно, и
    # не двигал отметку «последний раз видели». Данные при этом шли непрерывно.
    # Через два часа оператор создал тейк на RIZ6 — свежесть «вернулась», разрыв
    # посчитался в 7601 секунду, и ВХОД был задержан на полные три минуты
    # карантина. Залилось по 86090 вместо 86100 тремя минутами ранее.
    #
    # Карантин защищает от исполнения намерения, УСТАРЕВШЕГО за время слепоты.
    # Пока в книге нет ни одной живой заявки, устаревать нечему — значит и
    # разрыв копить не из чего. Нет кодов, за которыми следим: считаем, что
    # рынок видим, и отметка идёт дальше.
    codes_now = book.codes()
    fresh_now = (not codes_now) or any(
        (now - int((store.tick(c, agent) or {}).get("received_at_unix_ms") or 0))
        <= so_mod._STALE_TICK_MS for c in codes_now)
    was_active = quar.active(now)
    quar.observe(fresh_now, now)
    if quar.active(now) and not was_active:
        log.warning("smart_order.blind_quarantine", gap_sec=quar.gap_sec,
                    hold_sec=quar.left_sec(now))
        await _alert_reject(
            srv, agent, next(iter(active), book.orders[0] if book.orders else None) or
            SmartOrder(so_id="-", kind="sl", code="-", side="buy", qty=0),
            f"данных не было {quar.gap_sec} с: входы взведённых заявок задержаны на "
            f"{quar.left_sec(now)} с. Проверьте, нужны ли они ещё — сигнал мог "
            "родиться, пока мы не видели рынок. Защитные заявки не задержаны.")

    for code in book.codes():
        t = store.tick(code, agent) or {}
        trail_before = [(o.so_id, o.activated, o.peak) for o in book.orders]
        actions = so_mod.evaluate(
            book.orders, code, schedule=getattr(state, "market_schedule", None),
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
            # Активация следящей — событие, которого оператор ждёт глазами:
            # «дошла ли цена до уровня» (вопрос по 2b2990d2c4, 23.09.2026).
            was = {sid: act for sid, act, _ in trail_before}
            for o in book.orders:
                if o.activated and not was.get(o.so_id, True):
                    so_journal.record("activated", o, so_journal.WATCHER,
                                      f"цена дошла до {o.trigger_price:g}, "
                                      f"ведём от пика {o.peak:g}", now_ms=now)
        for act in actions:
            dirty = True
            if isinstance(act, Cancel):
                act.so.status = "cancelled"
                act.so.note = act.reason
                log.info("smart_order.oco_cancelled", so_id=act.so.so_id)
                so_journal.record("cancelled", act.so, so_journal.WATCHER,
                                  act.reason, now_ms=now)
                continue
            assert isinstance(act, Fire)
            so = act.so
            # ПОСЛЕДНИЙ РУБЕЖ ПРОТИВ ДВОЙНОЙ ОХРАНЫ: в терминале жива наша же
            # стоп-заявка — стрелять нельзя, иначе одну заявку исполнят дважды
            # (29.09.2026: 80 проданных контрактов вместо 40). Проверяем факт
            # таблицы, а не статус книги: именно расхождение между ними и стоило
            # оператору перевёрнутой позиции.
            if so.so_id in native_rows or any(
                    c.parent_id == so.so_id and c.so_id in native_rows
                    for c in book.orders):
                so_journal.record("held", so, so_journal.WATCHER,
                                  "в терминале жива своя стоп-заявка: сторож не стреляет",
                                  now_ms=now)
                log.warning("smart_order.fire_blocked_by_native", so_id=so.so_id)
                continue
            # Карантин держит ВХОДЫ: защитные заявки закрывают открытую позицию,
            # и их задержка оставила бы её голой.
            if quar.active(now) and bq.holds(so):
                so_journal.record("held", so, so_journal.WATCHER,
                                  f"карантин после слепоты ({quar.gap_sec} с без данных): "
                                  f"вход отложен, осталось {quar.left_sec(now)} с",
                                  now_ms=now)
                log.info("smart_order.held_by_quarantine", so_id=so.so_id,
                         left_sec=quar.left_sec(now))
                continue
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
                if so.kind in ("corridor", "triangle"):
                    # Коридор и треугольник — заявки МНОГОРАЗОВЫЕ: отстреляв от стенки, он ждёт
                    # противоположную. Поэтому статус возвращается в armed, а
                    # «fired» остаётся только следом для эскалации, которая
                    # доводит этот вход до исполнения.
                    stop_exit = (so.note or "").startswith("стоп")
                    closing = so.c_done and not stop_exit and so.c_pos != 0
                    so_mod.corridor_after_fire(
                        so, 1 if so.side == "buy" else -1, so.qty, stop_exit or closing)
                    so.status = "cancelled" if so.c_done and so.c_pos == 0 else "armed"
                    so_journal.record(
                        "corridor", so, so_journal.WATCHER,
                        f"{so.note or 'ход коридора'}: {so.side} {so.qty} по {act.price:g}; "
                        f"позиция {so.c_pos:+d}, переворотов {so.c_flips}"
                        + (f"/{so.c_flips_max}" if so.c_flips_max else ""),
                        now_ms=now)
                log.info("smart_order.fired", so_id=so.so_id, kind=so.kind,
                         code=so.code, side=so.side, qty=so.qty, price=act.price)
                so_journal.record("fired", so, so_journal.WATCHER,
                                  f"заявка выставлена по {act.price:g}"
                                  + (f", пик {so.peak:g}" if so.peak else ""), now_ms=now)
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
                    so_journal.record("created", child, so_journal.WATCHER,
                                      f"защита после входа по {act.price:g}: "
                                      f"уровень {child.trigger_price:g}", now_ms=now)
            except LimitError as exc:
                so.status = "error"
                so.note = f"отклонено лимитами: {exc}"
                log.warning("smart_order.rejected", so_id=so.so_id, error=str(exc))
                so_journal.record("rejected", so, so_journal.LIMITS, str(exc), now_ms=now)
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
