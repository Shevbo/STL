"""Снимок правды о позициях и сделках на ДИСК, с честным возрастом.

ЗАЧЕМ. 23.09.2026 агент (и человек следом за ним) отчитался оператору, что в
рынке висит шорт 40 контрактов, когда шорта уже полтора часа не было. Смотрел он
в книгу умных заявок (data/smart_orders.json) — а книга хранит НАМЕРЕНИЯ: что
оператор поставил и что STL отдал терминалу. Факт закрытия нативной стоп-заявкой
QUIK в неё не попадает вовсе.

Фактическая картина (позиции счёта, таблица сделок, позиции роботов) приезжает от
агента каждые ≤5 с и живёт ТОЛЬКО в оперативной памяти STL (store.AgentState).
Снаружи её видно лишь через авторизованный API, а рестарт STL стирает её начисто.
Поэтому здесь — две простые вещи:

  data/truth.json          последний снимок, перезаписывается раз в 2 с;
  data/trades/YYYY-MM-DD.jsonl  журнал сделок, дедуп по номеру, растёт навсегда.

Правило, ради которого всё написано: снимок НИКОГДА не выдаёт себя за свежий.
Судья свежести — КАНАЛ, а не возраст зеркала: агент шлёт статус только когда
содержимое изменилось (change-gate в link/status_snapshot), поэтому в тихий час
зеркалу может быть и десять минут, и это норма — менять было нечего. Но если
молчит heartbeat линка, картины нет вовсе, и читатель обязан сказать «не знаю».
Отдельно ловится замерший сборщик статуса: канал жив, а зеркало не обновлялось
дольше MIRROR_MAX_MS — тогда тишина не «ничего не менялось», а поломка.

Чистая логика — в build(); запись на диск и цикл — в run().
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

PATH = "data/truth.json"
TRADES_DIR = "data/trades"
PERIOD_SEC = 2.0
STALE_MS = 10_000          # молчащий линк старше — это воспоминание, а не знание
MIRROR_MAX_MS = 300_000    # живой линк, но зеркало не двигалось: сборщик статуса замер
MSK_OFFSET_MS = 3 * 3600 * 1000
_LAST_TRADES = 20          # хвост сделок прямо в снимке, чтобы хватало одного файла


def owner(tag: str) -> str:
    """Чья сделка: робота (brokerref rr:), умной заявки (so:) или руки оператора."""
    tag = (tag or "").strip()
    if tag.startswith("rr:"):
        return "robot"
    if tag.startswith("so:"):
        return "smart"
    return "manual"


def _msk_day_start_ms(now_ms: int) -> int:
    day = (now_ms + MSK_OFFSET_MS) // 86_400_000
    return day * 86_400_000 - MSK_OFFSET_MS


def build(status: dict[str, Any] | None, agents: list[dict[str, Any]],
          book_orders: list[Any], now_ms: int) -> dict[str, Any]:
    """Снимок: позиции счёта с разбивкой робот/рука, роботы, живые умные заявки.

    `status` — зеркало агента (store.agent_status()); None или старое зеркало
    даёт stale=True и пустые списки: лучше «не знаю», чем позавчерашний шорт."""
    status = status or {}
    received = int(status.get("_received_at_ms") or 0)
    age_ms = (now_ms - received) if received else -1
    link_age = min((int(a.get("last_seen_age_ms") or 0) for a in agents), default=-1)

    robots = []
    by_symbol: dict[str, int] = {}
    for r in status.get("robots") or []:
        pos = int(r.get("position") or 0)
        sym = str(r.get("symbol") or "")
        # Только РЕАЛЬНЫЕ: бумажный робот считает свою позицию у себя и на счёте
        # её нет. Сложив его в разбивку, мы вычли бы несуществующие контракты из
        # ручной позиции — и «рука» врала бы ровно на бумажный объём.
        if sym and str(r.get("mode") or "") == "real":
            by_symbol[sym] = by_symbol.get(sym, 0) + pos
        robots.append({
            "id": r.get("id"), "symbol": sym, "mode": r.get("mode"),
            "position": pos, "avg_price": r.get("avg_price"),
            "paused": bool(r.get("paused")), "running": bool(r.get("running")),
            "pnl_points": r.get("pnl_points"), "pnl_rub": r.get("pnl_rub"),
        })

    positions = []
    for p in (status.get("health") or {}).get("positions") or []:
        net = int(p.get("net") or 0)
        sec = str(p.get("sec") or "")
        robot_net = by_symbol.get(sec, 0)
        positions.append({
            "sec": sec, "net": net, "avg": p.get("avg"),
            "varmargin": p.get("varmargin"),
            "robots": robot_net, "manual": net - robot_net,
        })
    # РЕАЛЬНЫЙ робот в позиции по инструменту, которого нет в таблице счёта, —
    # расхождение, а не отсутствие позиции: строка с net=0, чтобы било в глаза.
    for sym, robot_net in by_symbol.items():
        if robot_net and not any(p["sec"] == sym for p in positions):
            positions.append({"sec": sym, "net": 0, "avg": None, "varmargin": None,
                              "robots": robot_net, "manual": -robot_net})

    trades = list((status.get("quik") or {}).get("trades") or [])
    day_start = _msk_day_start_ms(now_ms)
    today = [t for t in trades if int(t.get("ts_ms") or 0) >= day_start]
    by_owner: dict[str, int] = {}
    for t in today:
        by_owner[owner(t.get("tag"))] = by_owner.get(owner(t.get("tag")), 0) + 1

    smart = [{
        "so_id": o.so_id, "kind": o.kind, "code": o.code, "side": o.side,
        "qty": o.qty, "status": o.status, "native_state": o.native_state,
        "level": o.trigger_price, "trail_offset": o.trail_offset,
        "parent_id": o.parent_id,
    } for o in book_orders if o.status in ("armed", "native")]

    if received == 0:
        why = "зеркала агента нет вовсе"
    elif link_age < 0 or link_age > STALE_MS:
        why = f"линк агента молчит {link_age / 1000:.0f} с"
    elif age_ms > MIRROR_MAX_MS:
        why = f"линк жив, но статус агента не обновлялся {age_ms / 1000:.0f} с"
    else:
        why = ""

    return {
        "ts_ms": now_ms,
        "age_ms": age_ms,
        "stale": bool(why),
        "stale_why": why,
        "link_age_ms": link_age,
        "pos_age_ms": (status.get("health") or {}).get("pos_age_ms"),
        "equity": ((status.get("health") or {}).get("money") or {}).get("equity"),
        "positions": positions,
        "robots": robots,
        "smart_orders": smart,
        "trades_today": {"count": len(today), "by_owner": by_owner},
        "last_trades": [{
            "num": t.get("num"), "ts_ms": t.get("ts_ms"), "sec": t.get("sec"),
            "side": t.get("side"), "qty": t.get("qty"), "price": t.get("price"),
            "owner": owner(t.get("tag")), "tag": t.get("tag"),
        } for t in sorted(today, key=lambda x: int(x.get("ts_ms") or 0))[-_LAST_TRADES:]],
    }


def _write_atomic(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def journal_path(now_ms: int, directory: str = TRADES_DIR) -> str:
    day = time.strftime("%Y-%m-%d", time.gmtime((now_ms + MSK_OFFSET_MS) / 1000))
    return os.path.join(directory, f"{day}.jsonl")


def append_trades(status: dict[str, Any] | None, seen: set[str], now_ms: int,
                  directory: str = TRADES_DIR) -> int:
    """Дописать новые сделки в суточный журнал. Дедуп по номеру сделки QUIK.

    Ринг агента держит 500 последних сделок и обнуляется рестартом QUIK — через
    сутки ответить «что было позавчера» уже нечем. Журнал отвечает всегда."""
    rows = ((status or {}).get("quik") or {}).get("trades") or []
    fresh = [t for t in rows if str(t.get("num") or "") and str(t.get("num")) not in seen]
    if not fresh:
        return 0
    fresh.sort(key=lambda t: int(t.get("ts_ms") or 0))
    path = journal_path(now_ms, directory)
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for t in fresh:
            seen.add(str(t.get("num")))
            fh.write(json.dumps({
                "num": t.get("num"), "ts_ms": t.get("ts_ms"), "sec": t.get("sec"),
                "side": t.get("side"), "qty": t.get("qty"), "price": t.get("price"),
                "order_num": t.get("order_num"), "tag": t.get("tag"),
                "owner": owner(t.get("tag")),
            }, ensure_ascii=False) + "\n")
    return len(fresh)


def _load_seen(now_ms: int, directory: str = TRADES_DIR) -> set[str]:
    """Номера сделок, уже лежащие в сегодняшнем журнале (после рестарта STL)."""
    seen: set[str] = set()
    path = journal_path(now_ms, directory)
    if not os.path.exists(path):
        return seen
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                seen.add(str(json.loads(line).get("num")))
            except Exception:  # noqa: BLE001 — битая строка не повод терять журнал
                continue
    return seen


async def run(state: Any, path: str = PATH, directory: str = TRADES_DIR,
              period: float = PERIOD_SEC) -> None:
    """Фоновая задача: снимок на диск раз в две секунды + журнал сделок."""
    now = int(time.time() * 1000)
    seen = _load_seen(now, directory)
    day = journal_path(now, directory)
    log.info("quik.truth.started", path=path, journal=day, known_trades=len(seen))
    while True:
        try:
            store = getattr(state, "quik_store", None)
            if store is not None:
                now = int(time.time() * 1000)
                status = store.agent_status(None)
                book = getattr(state, "smart_orders", None)
                _write_atomic(path, build(status, store.status(),
                                          list(book.orders) if book else [], now))
                today = journal_path(now, directory)
                if today != day:            # смена суток МСК — журнал новый, дедуп тоже
                    seen, day = set(), today
                append_trades(status, seen, now, directory)
        except Exception as exc:  # noqa: BLE001 — сторож не имеет права падать
            log.warning("quik.truth.failed", error=str(exc))
        await asyncio.sleep(period)
