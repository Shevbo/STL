"""Таблица заявок QUIK — единственный правдивый ответ «что живо в терминале».

Три косяка, названные оператором одним письмом 01.10.2026 («не видишь активные
заявки в квике вне зависимости от их природы, не можешь их снять, пугаешь
амнезией при перезагрузке»), оказались одним корнем: STL судил о живых заявках
по своему складу в ПАМЯТИ, а не по таблице терминала. Склад знает только то,
что ставил сам, пустеет при рестарте и слеп к заявкам, поставленным руками или
переживившим перезапуск. Отсюда все три: экран не показывал чужое, снимать было
нечем, а после рестарта сторож ставил дубли к живым и невидимым заявкам — ровно
так 01.10.2026 сетка удвоилась на 24 заявках.

Таблица приезжает от агента в зеркале (`agent_status()["quik"]["orders"]`,
публикует QLua `publish_acc_orders`) и несёт всё нужное: номер, инструмент,
сторону, цену, объём, остаток, активность и brokerref.

Два правила, за которые и написан модуль:

* **Природа заявки — ЯРЛЫК, а не фильтр.** Экран обязан показать живую заявку,
  кто бы её ни поставил. Компаньон до этого дня выбрасывал роботные, `recon` и
  `stl-so*` строки ещё до показа, и «активных заявок в QUIK» у него не бывало
  в принципе.
* **Пустая таблица и отсутствие таблицы — разные ответы.** `rows()` вернёт `[]`
  в обоих случаях, поэтому решения о ПОСТАНОВКЕ заявки принимаются только через
  `fresh()`: нет зеркала — «не знаю», и ставить вслепую нельзя.
"""

from __future__ import annotations

import time
from typing import Any

from trader.quik.truth import MIRROR_MAX_MS, SMART_TAG, owner

# Цена уровня/стенки совпадает с ценой строки в QUIK с точностью до шага цены.
# Сравнение идёт по шагу, а не по «равно»: QUIK отдаёт double, а шаг RIZ6 = 10.
_PRICE_EPS_STEPS = 0.5


def _status(store: Any, agent: str | None) -> dict[str, Any]:
    """Зеркало агента или пустой словарь. `store` здесь Any (так во всём модуле
    quik): к нему приходят и None, и объекты без зеркала вовсе — отсутствие метода
    это «зеркала нет», и дальше всё ведёт себя как при молчащем агенте, то есть
    запрещает ставить заявки, а не разрешает."""
    getter = getattr(store, "agent_status", None) if store is not None else None
    if getter is None:
        return {}
    status = getter(agent) or {}
    return status if isinstance(status, dict) else {}


def snapshot(store: Any, agent: str | None = None) -> dict[str, Any]:
    """Блок `quik` зеркала агента. Пустой словарь — зеркала нет."""
    return _status(store, agent).get("quik") or {}


def fresh(store: Any, agent: str | None = None, now_ms: int | None = None) -> bool:
    """Можно ли опираться на таблицу при РЕШЕНИИ поставить заявку.

    False = «не знаю, что в терминале»: зеркала нет или оно встало. Отличать от
    пустой таблицы обязательно — иначе STL примет собственную слепоту за флэт и
    поставит дубль к живой заявке (инцидент 01.10.2026)."""
    status = _status(store, agent)
    if not status:
        return False
    quik = status.get("quik")
    if not isinstance(quik, dict) or "orders" not in quik:
        return False          # старая сборка агента: таблицы в зеркале нет вовсе
    # ПУСТОЙ СПИСОК «orders» ЕСТЬ В СНИМКЕ ВСЕГДА, даже до первого кадра от QLua:
    # агент инициализирует поле пустым срезом (buildQuikJSON). Значит наличия ключа
    # мало — по нему слепота сразу после рестарта АГЕНТА выглядела бы флэтом, и все
    # уровни сетки встали бы заново (окно до 15 с: столько Lua держит keepalive).
    #
    # Правду про саму таблицу говорит health.ord_age_ms: −1 = не публиковалась НИ
    # РАЗУ (accounts.Snapshot, «never an epoch-sized number»), иначе возраст
    # таблицы в мс. Это ответ именно про ТАБЛИЦУ, а не про зеркало, поэтому он
    # главный — зеркало может быть свежим, а таблицы в нём ещё не быть.
    health = status.get("health")
    ord_age = (health or {}).get("ord_age_ms") if isinstance(health, dict) else None
    if ord_age is None:
        return False          # сборка агента без ord_age_ms: судить не на чем
    try:
        ord_age = int(ord_age)
    except (TypeError, ValueError):
        return False
    if ord_age < 0:
        return False          # таблица не публиковалась ни разу — «НЕ ЗНАЮ»
    if ord_age > MIRROR_MAX_MS:
        return False          # публикации встали: таблица описывает прошлое
    # Возраст самого зеркала у store ОДИН: `_received_at_ms` (см.
    # store.set_agent_status — generated_at агента там принимается и намеренно не
    # используется). Таблица может быть свежей у агента, а до нас не доезжать.
    got = int(status.get("_received_at_ms") or 0)
    if got <= 0:
        return True           # возраст не заявлен — зеркало есть, судим по нему
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return (now - got) <= MIRROR_MAX_MS


def rows(store: Any, agent: str | None = None,
         robot_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Все строки таблицы заявок QUIK с ЯРЛЫКОМ природы. Ничего не фильтруется.

    `origin` — `manual` (руками в терминале) | `smart` | `robot` | `recon` |
    `external` (приложение брокера). `so_id` заполнен только у умных заявок."""
    out = []
    for o in snapshot(store, agent).get("orders") or []:
        if not isinstance(o, dict):
            continue
        tag = str(o.get("tag") or "")
        try:
            qty, bal = int(o.get("qty") or 0), int(o.get("balance") or 0)
        except (TypeError, ValueError):
            qty, bal = 0, 0
        out.append({
            "num": str(o.get("num") or ""),
            "sec": str(o.get("sec") or ""),
            "side": str(o.get("side") or ""),
            "price": float(o.get("price") or 0),
            "qty": qty,
            "balance": bal,
            "filled": max(qty - bal, 0),
            "active": bool(o.get("active")),
            "state": _state(bool(o.get("active")), qty, bal),
            "ts_ms": int(o.get("ts_ms") or 0),
            "tag": tag,
            "origin": owner(tag, robot_ids),
            "so_id": so_id_of(tag),
        })
    out.sort(key=lambda d: (not d["active"], -d["ts_ms"]))
    return out


def _state(is_active: bool, qty: int, bal: int) -> str:
    """Состояние строки словами оператора."""
    if is_active:
        return "активна"
    if bal == 0 and qty > 0:
        return "исполнена"
    return "снята" + (f", исполнено {qty - bal}" if qty > bal else "")


def active(store: Any, agent: str | None = None,
           robot_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Только живые строки: то, что ПРЯМО СЕЙЧАС стоит в рынке."""
    return [r for r in rows(store, agent, robot_ids) if r["active"]]


def so_id_of(tag: str) -> str:
    """so_id из brokerref `stl-so-<so_id>[:<хвост client_id>]`.

    В brokerref QUIK ровно 20 символов, а client_id уровня сетки выглядит как
    `so:<so_id>:<стенка>:<соль>` — в тег влезает so_id и огрызок стенки. Поэтому
    режем по первому двоеточию: so_id (10 hex) влезает всегда, огрызок никому не
    нужен. Уровень опознаётся по ЦЕНЕ строки (`matches_price`), она в таблице и
    детерминирована."""
    tag = (tag or "").strip()
    if not tag.startswith(SMART_TAG):
        return ""
    return tag[len(SMART_TAG):].split(":", 1)[0]


def by_smart_order(store: Any, agent: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """so_id -> ЖИВЫЕ строки QUIK этой умной заявки.

    Основа лечения амнезии: перед постановкой уровня сторож спрашивает не свой
    склад, а терминал."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in active(store, agent):
        if r["so_id"]:
            out.setdefault(r["so_id"], []).append(r)
    return out


def by_num(store: Any, agent: str | None = None) -> dict[str, dict[str, Any]]:
    """num -> строка таблицы, ВСЕ строки, не только живые.

    Нужна, чтобы догнать филл ПОДХВАЧЕННОЙ заявки. Подхваченной заявке STL не
    возвращает client_id (в brokerref QUIK 20 символов, он туда не влезает), поэтому
    о её исполнении нельзя узнать из склада заявок — склад про неё не знает. Зато
    исполнившаяся строка не исчезает из таблицы: она становится неактивной, и
    `qty - balance` говорит, сколько налилось."""
    return {r["num"]: r for r in rows(store, agent) if r["num"]}


def matches_price(row_price: float, want: float, step: float) -> bool:
    """Это ли строка того самого уровня/стенки. Допуск — половина шага цены."""
    if step <= 0:
        return abs(row_price - want) < 1e-9
    return abs(row_price - want) <= step * _PRICE_EPS_STEPS


def find_level(live: list[dict[str, Any]], price: float, step: float,
               side: str = "") -> dict[str, Any] | None:
    """Живая заявка этого уровня среди строк умной заявки, иначе None.

    СТОРОНУ СВЕРЯТЬ НЕ НАДО, и это не упрощение, а урок живых данных 01.10.2026.
    Сторона уровня следует РЫНКУ, а не лестнице (`grid_side_for`): в живой сетке
    41bf3af0dd уровни 85640/85740/85840 стояли ПОКУПКАМИ, хотя лежат выше базы
    85540 — рынок был над ними. Стоит рынку сместиться, и та же функция назовёт
    эти уровни продажами. Сверка по стороне тогда не нашла бы стоящую заявку и
    разрешила бы поставить ВТОРУЮ на ту же цену, в противоположную сторону — то
    есть ровно тот дубль, против которого вся эта проверка и написана, да ещё и
    кросс-заявкой к своей же.

    Цена уровня детерминирована и стороне не подчиняется, поэтому опознаём по ней.
    Два ордера на одной цене не нужны НИКОГДА, какой бы ни была сторона.

    `side` оставлен для вызывающих, которым нужна именно сторона (там, где цена
    одна, а смысл у сторон разный), но защита от дубля его не передаёт.
    """
    for r in live:
        if side and r["side"] and r["side"] != side:
            continue
        if matches_price(r["price"], price, step):
            return r
    return None
