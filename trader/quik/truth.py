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


SMART_TAG = "stl-so-"      # см. quik_agent/internal/trade/bridge.go: ownerTag()
TAG_WIDTH = 20             # brokerref QUIK: длинный robot_id в нём обрезан
ROBOTS_PATH = "data/robot_ids.json"


def owner(tag: str, robot_ids: set[str] | None = None) -> str:
    """Чья сделка, по brokerref из таблицы QUIK.

    Агент пишет в комментарий заявки: ID робота, "recon" у выравнивающей, а у
    ребёнка умной заявки "stl-so-<so_id>". Пусто — торговал человек руками.
    Это НЕ client_id ("rr:"/"so:"): в brokerref QUIK всего 20 символов.

    `robot_ids` — реестр известных роботов. БЕЗ НЕГО любой незнакомый непустой тег
    приходилось звать роботом, и в роботы попадало приложение брокера (теги вида
    "}S…XдD", 24.09.2026 их набралось 69 сделок). С реестром незнакомый тег честно
    зовётся "external": это тоже торговля оператора, просто другим каналом."""
    tag = (tag or "").strip()
    if not tag:
        return "manual"
    if tag.startswith(SMART_TAG):
        return "smart"
    if tag == "recon":
        return "recon"
    if robot_ids is None:
        return "robot"
    return "robot" if tag in {r[:TAG_WIDTH] for r in robot_ids} else "external"


# Канал ручной торговли для экрана: три, как их называет оператор. Робот и
# выравнивание каналами не являются и в ручную торговлю не входят.
CHANNELS = {"manual": "quik", "external": "broker", "smart": "smart"}


def channel(tag: str, robot_ids: set[str] | None = None) -> str:
    """quik | broker | smart | robot | recon."""
    own = owner(tag, robot_ids)
    return CHANNELS.get(own, own)


def load_robot_ids(path: str = ROBOTS_PATH) -> set[str]:
    """Накопленный реестр id роботов. Пусто — файла ещё нет."""
    try:
        with open(path, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def save_robot_ids(ids: set[str], path: str = ROBOTS_PATH) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(ids), fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("quik.truth.robot_ids_save_failed", error=str(exc))


def _msk_day_start_ms(now_ms: int) -> int:
    day = (now_ms + MSK_OFFSET_MS) // 86_400_000
    return day * 86_400_000 - MSK_OFFSET_MS


def watch_view(book_orders: list[Any], ticks: dict[str, dict[str, Any]],
               extremes: dict[str, dict[str, float]], session_open: Any,
               now_ms: int) -> list[dict[str, Any]]:
    """Чем сторож ЖИВЁТ по каждой взведённой заявке: цена, которую он видит,
    возраст кадра, торгует ли биржа, сколько осталось до уровня и куда цена
    ходила с постановки.

    Без этого «почему не сработала» не имеет ответа: сторож судит по store.tick
    (типизированный кадр), а показываем мы feed из витрины агента — это разные
    каналы, и расходятся они молча. 23.09.2026 вопрос по заявке 2b2990d2c4
    пришлось закрывать свечами ISS с четвертьчасовым опозданием."""
    out = []
    for o in book_orders:
        if o.status != "armed":
            continue
        t = ticks.get(o.code) or {}
        last = float(t.get("last") or 0)
        age = now_ms - int(t.get("received_at_unix_ms") or 0) if t else -1
        ext = extremes.get(o.code) or {}
        level = o.trigger_price or 0.0
        out.append({
            "so_id": o.so_id, "kind": o.kind, "code": o.code, "side": o.side,
            "qty": o.qty, "level": level, "trail_offset": o.trail_offset,
            "activated": bool(getattr(o, "activated", False)),
            "peak": getattr(o, "peak", 0.0),
            "last": last,
            "distance": round(level - last, 6) if (level and last) else None,
            "tick_age_ms": age,
            "watcher_blind": bool(age < 0 or age > 30_000 or last <= 0
                                  or session_open is not True),
            "session_open": session_open,
            "hi_since": ext.get("hi"), "lo_since": ext.get("lo"),
            "since_ms": ext.get("since_ms"),
        })
    return out


def track_extremes(extremes: dict[str, dict[str, float]], codes: set[str],
                   ticks: dict[str, dict[str, Any]], now_ms: int) -> None:
    """Максимум и минимум ПО ТОМУ ЖЕ кадру, по которому судит сторож.

    Ходила ли цена к уровню — вопрос факта, а не памяти оператора; ISS отвечает
    на него с опозданием на четверть часа."""
    for code in codes:
        last = float((ticks.get(code) or {}).get("last") or 0)
        if last <= 0:
            continue
        e = extremes.get(code)
        if e is None:
            extremes[code] = {"hi": last, "lo": last, "since_ms": now_ms}
        else:
            e["hi"], e["lo"] = max(e["hi"], last), min(e["lo"], last)
    for code in list(extremes):
        if code not in codes:
            del extremes[code]          # заявок по инструменту нет — и следить не за чем


def build(status: dict[str, Any] | None, agents: list[dict[str, Any]],
          book_orders: list[Any], now_ms: int,
          watch: list[dict[str, Any]] | None = None,
          robot_ids: set[str] | None = None,
          limits: dict[str, Any] | None = None) -> dict[str, Any]:
    """Снимок: позиции счёта с разбивкой робот/рука, роботы, живые умные заявки.

    `status` — зеркало агента (store.agent_status()); None или старое зеркало
    даёт stale=True и пустые списки: лучше «не знаю», чем позавчерашний шорт.

    `limits` — эхо эффективных лимитов агента (store.limits_state()); из него
    берётся kill-switch, см. agent_blocked ниже."""
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
        who = owner(t.get("tag"), robot_ids)
        by_owner[who] = by_owner.get(who, 0) + 1

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

    # Цена и возраст ленты: без них нельзя сказать, ЗАКОННО ли взведённая заявка
    # ещё не сработала (23.09.2026 вечером вопрос по заявке встал именно так).
    feed = [{"code": f.get("code"), "last": f.get("last"), "bid": f.get("bid"),
             "ask": f.get("ask"), "age_ms": f.get("age_ms")}
            for f in (status.get("health") or {}).get("feed") or []]

    # KILL-SWITCH АГЕНТА. 26.09.2026 агент час отклонял КАЖДУЮ заявку обоих реальных
    # роботов — они пытались ЗАКРЫТЬ свои шорты, — а STL считал торговлю разрешённой:
    # блокировка не публиковалась нигде, и нашёл её человек в логе раннера на VDS.
    # None значит «НЕ ЗНАЮ»: агент старше релиза поля не присылает, и звать это
    # «торговля разрешена» нельзя — это разные утверждения.
    # Возраст эха важен: агент шлёт LimitsState на старте сессии и на каждый
    # SetLimits, а не по таймеру, поэтому старое эхо это память, а не знание.
    lim = limits or {}
    blocked = lim.get("blocked")
    got = int(lim.get("received_at_ms") or 0)

    return {
        "ts_ms": now_ms,
        "feed": feed,
        "agent_blocked": (bool(blocked) if blocked is not None else None),
        "limits_age_ms": (now_ms - got) if got else -1,
        "watch": watch or [],
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
            "owner": owner(t.get("tag"), robot_ids), "tag": t.get("tag"),
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
                  directory: str = TRADES_DIR,
                  robot_ids: set[str] | None = None) -> int:
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
                "owner": owner(t.get("tag"), robot_ids),
                "channel": channel(t.get("tag"), robot_ids),
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
    extremes: dict[str, dict[str, float]] = {}
    robot_ids = load_robot_ids()
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
                orders = list(book.orders) if book else []
                codes = {o.code for o in orders if o.status == "armed"}
                ticks = {c: (store.tick(c, None) or {}) for c in codes}
                track_extremes(extremes, codes, ticks, now)
                session_open = (getattr(state, "market_session", None) or {}).get("open")
                _write_atomic(path, build(status, store.status(), orders, now,
                                          watch_view(orders, ticks, extremes,
                                                     session_open, now),
                                          robot_ids, store.limits_state(None)))
                # Реестр роботов НАКАПЛИВАЕТСЯ: снятый робот исчезает из зеркала,
                # но его сегодняшние сделки в журнале остаются, и без памяти они
                # переехали бы в «приложение брокера».
                seen_ids = {str(r.get("id")) for r in (status or {}).get("robots") or []
                            if r.get("id")}
                if not seen_ids <= robot_ids:
                    robot_ids |= seen_ids
                    save_robot_ids(robot_ids)
                today = journal_path(now, directory)
                if today != day:            # смена суток МСК — журнал новый, дедуп тоже
                    seen, day = set(), today
                append_trades(status, seen, now, directory, robot_ids)
        except Exception as exc:  # noqa: BLE001 — сторож не имеет права падать
            log.warning("quik.truth.failed", error=str(exc))
        await asyncio.sleep(period)
