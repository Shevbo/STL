"""STL Companion: pairing, device tokens, and the single read-only snapshot the
tray panel renders.

Threat model, deliberately narrow:
  * A companion device token can call EXACTLY ONE endpoint — GET /snapshot. It
    cannot place, cancel, pause, arm or deploy anything. There is no code path
    from a companion token to a trading action, so a leaked token leaks READS.
  * Pairing needs a short-lived single-use code the operator generates in STL
    (watchdog settings). The code is bound to a Windows account name; the
    companion stores the resulting token DPAPI-encrypted under that same
    account, so a copied token file is useless on another machine/user.
  * Everything is persisted in agent_control (key/value), like the robot-name
    overlay — no new table, no migration.

Only sha256(token) is ever stored, so a DB dump does not yield working tokens.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import auth_ok, require_auth
from trader.util import i9_hb_view

router = APIRouter(prefix="/api/v1/quik/companion", tags=["quik-companion"])


class _CachedStar(Exception):
    """Внутренний сигнал «значение взято из кэша, запрос не нужен»."""

_MSK = datetime.timezone(datetime.timedelta(hours=3))

_CODE_PREFIX = "companion:code:"
_DEV_PREFIX = "companion:dev:"
_TOKEN_PREFIX = "stlc_"
# Unambiguous alphabet: no O/0, no I/1 — the operator retypes this by hand.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8
_CODE_TTL_SEC = 15 * 60

# Brute-force brake on the ONE unauthenticated endpoint. 40 bits of entropy in a
# 15-minute window is already out of reach, this just makes noise cheap to spot.
_PAIR_FAIL_WINDOW_SEC = 600
_PAIR_FAIL_MAX = 10
_pair_fails: list[float] = []


def _operator(request: Request) -> str:
    """Operator (portal session) auth — required to mint or revoke access."""
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="БД недоступна")
    return pool


def _norm_account(s: str) -> str:
    """Windows account names are case-insensitive; whoami prints them lowercase
    but the operator may type DOMAIN\\User. Compare on a single normal form."""
    return (s or "").strip().replace("/", "\\").upper()


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _kv_get(pool, key: str) -> dict | None:
    raw = await pool.fetchval("SELECT value FROM agent_control WHERE key=$1", key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def _kv_put(pool, key: str, value: dict) -> None:
    await pool.execute(
        "INSERT INTO agent_control(key,value) VALUES($1,$2) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        key, json.dumps(value, ensure_ascii=False))


# ---------------------------------------------------------------- pairing ----

class PairingCodeBody(BaseModel):
    account: str
    label: str = ""


@router.post("/pairing-code")
async def pairing_code(body: PairingCodeBody, request: Request):
    """Mint a single-use pairing code for one Windows account (operator only)."""
    _operator(request)
    pool = _pool(request)
    account = _norm_account(body.account)
    if not account:
        raise HTTPException(status_code=400, detail="Укажите учётную запись, например WIN10-HYPERV\\admin")
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    now = int(time.time() * 1000)
    await _kv_put(pool, _CODE_PREFIX + code, {
        "account": account, "label": (body.label or "").strip()[:64],
        "created_ms": now, "expires_ms": now + _CODE_TTL_SEC * 1000})
    return {"code": code, "account": account, "expires_ms": now + _CODE_TTL_SEC * 1000,
            "ttl_sec": _CODE_TTL_SEC}


class PairBody(BaseModel):
    code: str
    account: str
    machine: str = ""


@router.post("/pair")
async def pair(body: PairBody, request: Request):
    """Exchange a pairing code for a long-lived read-only device token.

    UNAUTHENTICATED by design — this is how a fresh companion gets its first
    credential. Gated by: the code must exist, be unexpired, and name the SAME
    account the companion reports. The code is consumed either way, so a wrong
    guess never gets a second try at it.
    """
    pool = _pool(request)
    now_s = time.time()
    _pair_fails[:] = [t for t in _pair_fails if now_s - t < _PAIR_FAIL_WINDOW_SEC]
    if len(_pair_fails) >= _PAIR_FAIL_MAX:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Подождите 10 минут.")

    code = (body.code or "").strip().upper()
    rec = await _kv_get(pool, _CODE_PREFIX + code) if code else None
    now = int(now_s * 1000)
    bad = (rec is None
           or now > int(rec.get("expires_ms") or 0)
           or _norm_account(body.account) != rec.get("account"))
    if rec is not None:
        await pool.execute("DELETE FROM agent_control WHERE key=$1", _CODE_PREFIX + code)
    if bad:
        _pair_fails.append(now_s)
        raise HTTPException(status_code=403, detail="Код не подходит, просрочен или выдан другой учётной записи.")

    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    await _kv_put(pool, _DEV_PREFIX + _token_id(token), {
        "account": rec["account"], "label": rec.get("label") or "",
        "machine": (body.machine or "").strip()[:64],
        "created_ms": now, "last_seen_ms": 0})
    return {"token": token, "account": rec["account"]}


@router.get("/devices")
async def devices(request: Request):
    """Paired companions + codes still awaiting use (operator only)."""
    _operator(request)
    pool = _pool(request)
    rows = await pool.fetch(
        "SELECT key, value FROM agent_control WHERE key LIKE $1 OR key LIKE $2",
        _DEV_PREFIX + "%", _CODE_PREFIX + "%")
    now = int(time.time() * 1000)
    devs, codes = [], []
    for r in rows:
        try:
            v = json.loads(r["value"])
        except ValueError:
            continue
        if r["key"].startswith(_DEV_PREFIX):
            devs.append({"id": r["key"][len(_DEV_PREFIX):], **v})
        elif now <= int(v.get("expires_ms") or 0):
            codes.append({"code": r["key"][len(_CODE_PREFIX):], **v})
    devs.sort(key=lambda d: d.get("created_ms") or 0, reverse=True)
    codes.sort(key=lambda c: c.get("expires_ms") or 0)
    return {"devices": devs, "codes": codes}


class RevokeBody(BaseModel):
    id: str


@router.post("/revoke")
async def revoke(body: RevokeBody, request: Request):
    """Kill one paired companion (operator only). It gets 401 on the next poll."""
    _operator(request)
    pool = _pool(request)
    key = _DEV_PREFIX + (body.id or "").strip()
    if not (body.id or "").strip():
        raise HTTPException(status_code=400, detail="Не указан компаньон")
    await pool.execute("DELETE FROM agent_control WHERE key=$1", key)
    return {"ok": True, "id": body.id}


async def _companion_or_operator(request: Request) -> str:
    """Accept either a companion device token or a normal operator session.

    Keeping companion auth HERE (not in the shared guard) is the whole point:
    the token is meaningless on every other route in the app.
    """
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    if token.startswith(_TOKEN_PREFIX):
        pool = _pool(request)
        key = _DEV_PREFIX + _token_id(token)
        rec = await _kv_get(pool, key)
        if rec is None:
            raise HTTPException(status_code=401, detail="Компаньон не сопряжён или отозван")
        rec["last_seen_ms"] = int(time.time() * 1000)
        await _kv_put(pool, key, rec)
        return "companion:" + rec.get("account", "?")
    if auth_ok(request.app.state.settings.shectory_auth_bridge_secret, request):
        return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
    raise HTTPException(status_code=401, detail="Unauthorized")


# --------------------------------------------------------------- snapshot ----

# Цена закрытия ПРОШЛОЙ торговой сессии — точка отсчёта для «ВМ за сегодня».
# Без неё перенесённая со вчера позиция приписывала бы сегодняшнему дню весь свой
# накопленный ход. В QLua-параметрах такого поля нет, берём дневные свечи ISS.
# Кэш на сутки: панель дёргает снапшот раз в 5 секунд, ISS долбить нельзя.
_PREV_CLOSE: dict[tuple[str, str], float | None] = {}
_PREV_CLOSE_FAIL: dict[str, float] = {}     # symbol -> когда сорвалось (ретрай через 5 мин)
_PREV_CLOSE_RETRY_SEC = 300


async def _prev_close(symbol: str) -> float | None:
    if not symbol:
        return None
    day = datetime.datetime.now(tz=_MSK).date().isoformat()
    key = (symbol, day)
    if key in _PREV_CLOSE:
        return _PREV_CLOSE[key]
    failed_at = _PREV_CLOSE_FAIL.get(symbol)
    if failed_at and time.time() - failed_at < _PREV_CLOSE_RETRY_SEC:
        return None                       # недавно не получилось — не долбим ISS
    val: float | None = None
    try:
        import httpx
        frm = (datetime.date.fromisoformat(day) - datetime.timedelta(days=12)).isoformat()
        url = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities/"
               f"{symbol}/candles.json")
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "STL/1.0"}) as cl:
            r = await cl.get(url, params={"interval": 24, "from": frm, "iss.meta": "off"})
            c = (r.json() or {}).get("candles") or {}
            cols, rows = c.get("columns") or [], c.get("data") or []
            if rows and "close" in cols and "begin" in cols:
                i_c, i_b = cols.index("close"), cols.index("begin")
                # строго ДО сегодняшней даты: сегодняшняя свеча ещё не закрыта
                past = [row for row in rows if str(row[i_b])[:10] < day]
                if past:
                    val = float(past[-1][i_c])
    except Exception:  # noqa: BLE001 — панель не должна падать из-за ISS
        val = None
    if val is None:
        # НЕ кэшируем неудачу на сутки: одна осечка ISS означала бы прочерк до
        # завтра, а карточка при этом молча меняла смысл строки «сегодня»
        # (оператор видел скачки на десятки тысяч, 29.07). Повторим через 5 минут.
        _PREV_CLOSE_FAIL[symbol] = time.time()
        return None
    _PREV_CLOSE[key] = val
    return val


def _merge_fills(fills: list[dict], lo_ms: int) -> list[dict]:
    """Филлы одного ордера — один маркер на графике.

    Зеркало хранит строку на КАЖДУЮ сделку QUIK, а тонкая вечерняя книга наливает
    один ордер по частям. Без склейки ордер на 5 контрактов рисуется входом и
    четырьмя фантомными «усреднениями» — ровно та же ошибка, что однажды уже
    развела маркеры графика и таблицу сделок на стенде.

    Цена склеенного маркера — СРЕДНЕВЗВЕШЕННАЯ по количеству, время — последнего
    филла: именно тогда ордер закончил исполняться.
    """
    by_order: dict[str, dict] = {}
    for f in fills:
        if f.get("status") != "filled":
            continue
        ts = int(f.get("ts_unix_ms") or 0)
        if ts < lo_ms:
            continue
        try:
            qty, price = abs(int(f.get("qty") or 0)), float(f.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        key = f.get("order_id") or f"{ts}:{price}"
        cur = by_order.get(key)
        if cur is None:
            by_order[key] = {"ts": ts, "side": f.get("side"), "price": price,
                             "qty": qty, "_pq": price * qty}
            continue
        cur["qty"] += qty
        cur["_pq"] += price * qty
        cur["ts"] = max(cur["ts"], ts)
    out = []
    for m in by_order.values():
        m["price"] = m.pop("_pq") / m["qty"]
        out.append(m)
    return sorted(out, key=lambda m: m["ts"])


def _msk_midnight_ms() -> int:
    today = datetime.datetime.now(tz=_MSK).date()
    return int(datetime.datetime.combine(today, datetime.time.min, tzinfo=_MSK).timestamp() * 1000)


def _wd_read_jsonl(path: str) -> list[dict]:
    out: list[dict] = []
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


# Правила пометки контроля: (подстрока в тексте тревоги) -> (статус, нужен_оператор).
# Первое совпадение выигрывает. Статус приписывается после двоеточия, чтобы оператор
# сразу видел — рассосётся само или нужно его включение.
_AUTO = "на контроле, исправится автоматически"
_MANUAL = "ТРЕБУЕТ твоего включения"
_CONTROL_RULES: list[tuple[str, str, bool]] = [
    ("восстановилось", "", False),                    # это уже хорошая новость, без пометки
    ("ТЕСТ вотчдога", "проверка канала, реагировать не нужно", False),
    # Телефон-шлюз стоит на даче, ssh туда нет: САМО не рассосётся никогда.
    # Дефолтная пометка «исправится автоматически» тут была прямым враньём (27.07).
    ("кросс-заявки", f"{_MANUAL}: два робота одного счёта торгуют встречно по одному "
                     "инструменту — биржа блокирует сделку с самим собой; разведи их по "
                     "инструментам или оставь одного", True),
    ("SMS-шлюз молчит", f"{_MANUAL}: подними телефон-шлюз (интернет/WireGuard/приложение); "
                        "алерты пока идут в Telegram", True),
    ("ПОСТАВЛЕН НА ПАУЗУ", f"{_AUTO} (снимется при живой ленте)", False),
    ("на паузу", f"{_AUTO} (снимется при живой ленте)", False),
    ("реплей", f"{_AUTO}, если рынок закрыт; при открытом — открой окно «Все сделки» в QUIK", False),
    ("отстаёт", f"{_AUTO}, если рынок закрыт; при открытом — {_MANUAL}: открой окно «Все сделки» в QUIK", False),
    ("QUIK-гард", f"{_AUTO} (гард сам перезапустит QUIK)", False),
    ("мало памяти", f"{_MANUAL}: освободи память на VDS", True),
    ("лимит заявок", f"{_MANUAL}: подними дневной лимит", True),
    ("зависших прогонов", f"{_AUTO} (репер перезапустит); если висит — проверь i9", False),
    ("i9 молчит", f"{_MANUAL}: проверь агента на i9", True),
    ("нет связи с агентом", f"{_MANUAL}: проверь агента/VDS", True),
    ("раннер не отвечает", f"{_MANUAL}: проверь раннер на VDS", True),
    ("QUIK молчит", f"{_MANUAL}: проверь QUIK на VDS", True),
    ("вотчер молчит", f"{_MANUAL}: проверь cron на smain", True),
    ("хостер загружен", f"{_AUTO} (разгрузится); если долго — {_MANUAL}", False),
]


def _with_control(text: str, market_open: bool | None) -> str:
    """Дописать к тексту тревоги пометку контроля после двоеточия."""
    if not text:
        return text
    low = text.lower()
    for needle, note, _ in _CONTROL_RULES:
        if needle.lower() in low:
            return text if not note else f"{text} — {note}"
    return f"{text} — {_AUTO}"  # неизвестная тревога: считаем, что под наблюдением


def _watch_runner(health: dict, received_ms: int | None, now_ms: int,
                  market: dict | None = None) -> dict:
    """4.1 — runner + Lua. Everything here is a FACT from the agent's own status
    mirror; nothing is inferred from the watchdog's prose.

    market — состояние сессии MOEX. Замершая лента при ЗАКРЫТОМ рынке это норма
    (ночь/выходная пауза/праздник), а не авария QUIK, поэтому в этом случае
    «лента отстаёт» не поднимаем."""
    issues = []
    market_open = (market or {}).get("open")  # True | False | None(неизвестно)
    if received_ms and now_ms - received_ms > 120_000:
        issues.append(f"нет связи с агентом {(now_ms - received_ms) // 1000} с")
    if not health.get("runner_healthy", False):
        issues.append("раннер не отвечает")
    age = health.get("runner_report_age_ms")
    if isinstance(age, int) and age > 120_000:
        issues.append(f"отчёт раннера {age // 1000} с назад")
    lag = health.get("exchange_lag_ms")
    # Гейт по сессии: лаг ленты тревожит только когда биржа реально торгует
    # (open True или неизвестно). При закрытом рынке замершая лента ожидаема.
    if isinstance(lag, int) and lag > 120_000 and market_open is not False:
        issues.append(f"лента отстаёт на {lag // 1000} с")
    pong = health.get("pong_age_ms")
    if isinstance(pong, int) and pong > 120_000:
        issues.append(f"QUIK молчит {pong // 1000} с")
    used, cap = health.get("daily_orders_used"), health.get("daily_orders_cap")
    if isinstance(used, int) and isinstance(cap, int) and cap and used >= cap * 0.85:
        issues.append(f"дневной лимит заявок {used}/{cap}")
    # vdsguard.StatusView: quik_state is OK | SLOW | HUNG | DISABLED.
    vds = health.get("vds") or {}
    qs = vds.get("quik_state")
    if qs and qs not in ("OK", "DISABLED"):
        issues.append(f"QUIK-гард: {qs}")
    if vds.get("low_memory"):
        issues.append("на VDS мало памяти")
    return {"ok": not issues, "issues": issues,
            "exchange_lag_ms": lag, "pong_age_ms": pong,
            "orders_used": used, "orders_cap": cap}


@router.get("/snapshot")
async def snapshot(request: Request, agent_id: str | None = None, bars: int = 30):
    """Everything the tray panel shows, in one read. Companion token or session.

    Read-only aggregation over mirrors STL already holds: no agent command is
    enqueued, no DB write happens beyond the device's own last_seen stamp.
    """
    await _companion_or_operator(request)
    pool = _pool(request)
    now_ms = int(time.time() * 1000)
    # Плотность мини-графика выбирает панель. Режем ЗДЕСЬ, а не на клиенте:
    # телефон опрашивает снапшот раз в 5 секунд по мобильному интернету, и
    # возить 200 баров на робота, когда показывают 30, значит жечь его трафик.
    bars_n = max(30, min(200, int(bars or 30)))

    store = getattr(request.app.state, "quik_store", None)
    # agent_status() returns the agent's own status JSON (agent/health/robots/
    # recon/quik) with _received_at_ms injected at the TOP level — it is NOT
    # wrapped in a "status" key, whatever the /agent-local-status fallback shape
    # suggests. Reading it wrong blanks the whole panel silently.
    status = (store.agent_status(agent_id) if store is not None else None) or {}
    health = status.get("health") or {}
    money = health.get("money") or {}
    received_ms = status.get("_received_at_ms")

    # 1. Account state, straight from the QUIK money row.
    account = {
        "limit": money.get("limit"), "used": money.get("used"),
        "planned": money.get("planned"), "varmargin": money.get("varmargin"),
        "ts_comission": money.get("ts_comission"), "equity": money.get("equity"),
        "age_ms": money.get("age_ms"), "has_data": bool(money),
    }

    # 2. Positions per instrument (ВМ is null on an old Lua build).
    # last/bid/ask — живая котировка инструмента из фида агента (панель их показывает
    # рядом с позицией). Цену НИКОГДА не округляем: у BR шаг 0.01.
    _feed = {f.get("code"): f for f in (health.get("feed") or []) if f.get("code")}
    positions = [
        {"sec": p.get("sec"), "net": p.get("net"), "avg": p.get("avg"),
         "varmargin": p.get("varmargin"),
         "last": (_feed.get(p.get("sec")) or {}).get("last"),
         "bid": (_feed.get(p.get("sec")) or {}).get("bid"),
         "ask": (_feed.get(p.get("sec")) or {}).get("ask"),
         "quote_age_ms": (_feed.get(p.get("sec")) or {}).get("age_ms")}
        for p in (health.get("positions") or []) if p.get("net")
    ]

    # 3. Robots — ТОЛЬКО РЕАЛЬНЫЕ ДЕНЬГИ. По просьбе оператора: показываем реальный
    # P&L каждого робота, КОТОРЫЙ ТОРГОВАЛ РЕАЛОМ, включая тех, кого потом перевели
    # в бумагу (их РЕАЛЬНЫЙ убыток раньше терялся: зеркало отдавало paper-эру).
    # Источник — журнал algo_trades с mode='real', а не pnl_rub из зеркала.
    # Бумажную информацию не показываем вовсе.
    names = {}
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM agent_control WHERE key LIKE $1", "robotname:%")
        names = {r["key"][len("robotname:"):]: r["value"] for r in rows}
    except Exception:
        pass
    today_lo = _msk_midnight_ms()
    # Позиция и средняя цена на КОНЕЦ ВЧЕРА — из журнала algo_trades: он пишет
    # pos_after/avg_after на каждой сделке и хранит ВСЮ историю робота. Прежний
    # способ (проигрыш 200-филлового хвоста из зеркала) у активного робота не
    # сходился: хвост обрезан, среднюю перенесённой позиции восстановить нечем,
    # и ВМ за сегодня оставалась прочерком (29.07).
    carry: dict[str, tuple[float, float]] = {}
    real_all, real_today = {}, {}
    try:
        for r in await pool.fetch(
                "SELECT robot_id, sum(pnl_net_rub) AS net, count(*) AS trades, "
                "max(ts_ms) AS last_ts, min(ts_ms) AS first_ts, sum(qty) AS qty, "
                # пик контрактов = максимальное ГО, хоть раз задействованное
                "max(abs(pos_after)) AS peak FROM algo_trades "
                "WHERE mode='real' GROUP BY robot_id"):
            real_all[r["robot_id"]] = {"net": float(r["net"] or 0),
                                       "trades": int(r["trades"]),
                                       "last_ts": int(r["last_ts"] or 0),
                                       "first_ts": int(r["first_ts"] or 0),
                                       "peak": int(r["peak"] or 0)}
        for r in await pool.fetch(
                "SELECT robot_id, sum(pnl_net_rub) AS net, count(*) AS trades "
                "FROM algo_trades WHERE mode='real' AND ts_ms >= $1 GROUP BY robot_id",
                today_lo):
            real_today[r["robot_id"]] = {"net": float(r["net"] or 0),
                                         "trades": int(r["trades"])}
    except Exception:
        pass
    try:
        for r in await pool.fetch(
                "SELECT DISTINCT ON (robot_id) robot_id, pos_after, avg_after "
                "FROM algo_trades WHERE mode='real' AND ts_ms < $1 "
                "ORDER BY robot_id, ts_ms DESC, seq DESC", today_lo):
            carry[r["robot_id"]] = (float(r["pos_after"] or 0), float(r["avg_after"] or 0))
    except Exception:  # noqa: BLE001 — панель не должна падать из-за журнала
        pass
    # Текущее состояние робота из зеркала (позиция/пауза/режим сейчас).
    mirror_by_id = {rob.get("id"): rob for rob in status.get("robots") or []}
    # Список = все, у кого есть РЕАЛЬНАЯ история, плюс те, кто ПРЯМО СЕЙЧАС в реале
    # (даже если ещё не наторговал ни одного филла в журнале).
    ids = set(real_all) | {rid for rid, rob in mirror_by_id.items()
                           if rob.get("mode") == "real"}
    # ВМ позиции робота = ЕГО СОБСТВЕННАЯ нереализованная прибыль по ЕГО позиции:
    #   vm = position * (текущая цена - средняя входа) * coef(₽/пункт).
    # QUIK отдаёт варьмаржу только на ИНСТРУМЕНТ целиком (не на робота), и позиции
    # роботов НЕ обязаны сходиться с нетто счёта (несколько роботов на коде + ручная
    # торговля). Прошлый вариант делил инструментальную ВМ на позицию робота и при
    # разных знаках переворачивал её в бред (+5481 из -6851). Теперь считаем по данным
    # САМОГО робота: цена — из фида агента (last по коду), coef — из QLua-параметров.
    _params = (store.params(agent_id) if store is not None else None) or {}
    _coef_by = {r.get("code"): r.get("coef") for r in (_params.get("rows") or []) if r.get("code")}
    _last_by = {f.get("code"): f.get("last") for f in (health.get("feed") or []) if f.get("code")}
    # ГО/контракт — из QLua-параметров (агент), иначе из справочника инструментов.
    # Фид отдаёт БИРЖЕВОЕ ГО (BUYDEPO), а брокер списывает своё, кратно большее
    # (RIU6 30.07.2026: биржа 22 375 ₽, счёт 53 672 ₽). Без множителя «пик ГО» и
    # годовая завышены ровно в это число раз.
    _mult = float(getattr(request.app.state.settings, "quik_margin_multiplier", 1.0) or 1.0)
    _margin_by = {r.get("code"): float(r.get("margin") or 0) * _mult
                  for r in ((health.get("params") or []) if isinstance(health.get("params"), list) else [])
                  if r.get("code")}
    def _robot_vm(sym, pos, avg):
        try:
            pos, avg = float(pos), float(avg)
        except (TypeError, ValueError):
            return None
        px, c = _last_by.get(sym), _coef_by.get(sym)
        if not pos or not avg or px in (None, 0) or c in (None, 0):
            return None
        return pos * (float(px) - avg) * float(c)
    robots = []
    for rid in ids:
        ra = real_all.get(rid) or {}
        rt = real_today.get(rid) or {}
        rob = mirror_by_id.get(rid) or {}
        cur_mode = rob.get("mode")           # 'real' | 'paper' | None(снят)
        # Статус для оператора: торгует ли робот реалом ПРЯМО СЕЙЧАС.
        # ВАЖНО: «пауза» сама по себе не говорит, реал это или бумага — оператор
        # 30.07.2026 не мог понять по панели, в каком режиме робот. Режим отдаём
        # ОТДЕЛЬНЫМ полем (строки state трогать нельзя: по ним панель делит
        # роботов на активных и выведенных).
        if cur_mode == "real":
            state = "пауза" if rob.get("paused") else "реал"
        elif cur_mode == "paper":
            state = "переведён в бумагу"     # реальные деньги больше НЕ рискует
        else:
            state = "снят"
        vm = (_robot_vm(rob.get("symbol"), rob.get("position"), rob.get("avg_price"))
              if cur_mode == "real" else None)
        fix = ra.get("net")
        # РЕЗУЛЬТАТ ЗА СЕГОДНЯ показываем ВСЕГДА, а не только на флэте: пока робот
        # в позиции, оператор иначе не понимает, что он сделал за день.
        #   сегодня = фикс за сегодня (журнал) + ИЗМЕНЕНИЕ ВМ за сегодня.
        # ВМ за сегодня = ВМ сейчас − ВМ на закрытии прошлой сессии; иначе позиция,
        # перенесённая со вчера, приписала бы сегодняшнему дню весь свой ход.
        # ВМ за сегодня считаем и когда робот СЕЙЧАС во флэте: он мог закрыть
        # позицию сегодня, и перенесённая со вчера нереализованная часть обязана
        # вернуться — иначе «сегодня» завышено ровно на неё.
        vm_today, vm_y = None, None
        if cur_mode == "real":
            sym = rob.get("symbol")
            coef = _coef_by.get(sym)
            pos_y, avg_y = carry.get(rid, (0.0, 0.0))
            if coef:
                if abs(pos_y) < 1e-9:
                    vm_y = 0.0            # вчера закрылись в ноль — переносить нечего
                else:
                    pc = await _prev_close(sym)
                    if pc and avg_y > 0:
                        vm_y = pos_y * (float(pc) - avg_y) * float(coef)
                if vm_y is not None:
                    vm_today = float(vm or 0) - vm_y
        # ПРАВИЛО: фин.рез = фикс + ВМ открытой позиции. Голый фикс при живой
        # позиции врёт (робот сидит в минусе, а карточка рисует «итог»).
        total = None if fix is None else float(fix) + float(vm or 0)
        # «Доходность в год» = (фикс + ВМ) / МАКС. ГО, хоть раз задействованное,
        # линейно приведённое к году. Пик контрактов — из журнала, ГО/контракт —
        # из QLua-параметров. Меньше 3 дней истории => не считаем (враньё масштаба).
        # Итог за сегодня и насколько он сдвинул общий результат: % считаем к
        # ФИНРЕЗУ НА КОНЕЦ ВЧЕРА (фикс без сегодняшнего + ВМ на вчерашнем закрытии).
        # Нет строк в журнале за сегодня — значит закрытых сделок не было, и фикс
        # за сегодня РАВЕН НУЛЮ (это факт, а не «неизвестно»). Неизвестной бывает
        # только ВМ за сегодня — без неё сумму не показываем вовсе, иначе строка
        # молча меняет смысл и число прыгает на величину ВМ (жалоба 29.07).
        today_fix = float(rt.get("net") or 0)
        today_total = None if vm_today is None else today_fix + float(vm_today)
        chg_pct = None
        if total is not None and today_total is not None and vm_y is not None:
            base = float(total) - today_total
            if abs(base) >= 100:          # у копеечной базы процент — шум
                chg_pct = today_total / abs(base) * 100
        margin = _margin_by.get(rob.get("symbol"))
        max_go = (float(margin) * int(ra.get("peak") or 0)) if margin else 0.0
        first_ts, ann = int(ra.get("first_ts") or 0), None
        if total is not None and max_go > 0 and first_ts > 0:
            days = (now_ms - first_ts) / 86_400_000
            if days >= 3:
                ann = (total / max_go) * (365 / days) * 100
        # «Только на выход» — ОТДЕЛЬНЫЙ флаг, а не ещё одно значение state: панель
        # делит роботов на активных и выведенных сравнением state с 'реал'/'пауза',
        # и новая строка молча схлопнула бы такого робота в «выведены из реала».
        exit_only = False
        if cur_mode == "real":
            try:
                exit_only = bool(json.loads(rob.get("params_json") or "{}").get("exit_only"))
            except (TypeError, ValueError):
                exit_only = False
        # Мини-график робота: свечи, его висящие заявки, его сделки. Всё из
        # ЗЕРКАЛА одного и того же отчёта, поэтому три слоя заведомо согласованы
        # между собой. Бары строит раннер из ленты всех сделок — это ровно те
        # свечи, по которым робот принимал решение; подмешивать сюда бары ISS
        # нельзя, там московское время штамповано как UTC и сделки уехали бы
        # на три часа от своей свечи.
        # Отдаём только активному реалу: у снятого робота заявок нет, а свечи без
        # них — просто график инструмента, который в панели уже есть.
        chart = None
        if cur_mode == "real":
            tail = [b for b in (rob.get("bars_tail") or [])
                    if b.get("t") and b.get("c")][-bars_n:]
            if tail:
                lo_ms = int(tail[0]["t"]) * 1000
                # Сигнал стратегии ПРЯМО СЕЙЧАС: want 1/-1/0. Нужен, чтобы пустой
                # график (ни заявок, ни сделок) не молчал о причине — робот может
                # стоять и потому, что сигнала нет, и потому, что его не посчитать.
                sig = {}
                try:
                    sig = json.loads(rob.get("signal_json") or "{}") or {}
                except (TypeError, ValueError):
                    sig = {}
                # Что держит вход прямо сейчас: раннер присылает целую фразу
                # («долина смерти: коридор закрытий 34 пт за 20 баров …»). В
                # статусную строку шириной в панель она не влезает, поэтому
                # рядом кладём короткий ярлык — часть до двоеточия без «держит».
                # Полная фраза остаётся: панель вешает её подсказкой.
                blocked = str(sig.get("entry_blocked") or "")
                chart = {
                    "bars": tail,
                    "want": sig.get("want") if sig.get("ready") else None,
                    "signal_known": bool(sig.get("ready")),
                    "blocked": blocked,
                    "blocked_tag": blocked.split(":")[0].replace("держит", "").strip(),
                    # висящие заявки: то, чего робот ждёт прямо сейчас
                    "orders": [{"side": w.get("side"), "price": w.get("price"),
                                "qty": w.get("qty"), "state": w.get("state")}
                               for w in (rob.get("working_orders") or [])
                               if w.get("price")],
                    # Сделки, попадающие в окно графика. Хвост в зеркале длиннее
                    # окна, и филлы ОДНОГО ордера схлопываем в один маркер: журнал
                    # хранит строку на каждую сделку QUIK, и ордер, налившийся
                    # 1+1+1+2, нарисовался бы входом и тремя лишними «усреднениями».
                    "fills": _merge_fills(rob.get("recent_fills") or [], lo_ms),
                    "avg": rob.get("avg_price") or None,
                }
        robots.append({
            "id": rid, "name": names.get(rid) or rid,
            "symbol": rob.get("symbol"),
            "chart": chart,
            "state": state,
            "mode": cur_mode,                     # real | paper | None(снят с агента)
            "paused": bool(rob.get("paused")) if cur_mode else None,
            "exit_only": exit_only,               # закрывает свою позицию, новых не берёт
            "real_net": fix,                      # ФИКС: весь реальный итог по закрытым
            "real_total": total,                  # фикс + ВМ открытой позиции
            "real_today": today_fix,
            "vm_today": vm_today,                 # изменение ВМ за сегодня (None = не знаем)
            "today_total": today_total,           # фикс сегодня + ВМ сегодня
            "chg_pct": chg_pct,                   # % к финрезу на конец вчера
            "real_trades_today": rt.get("trades") or 0,
            "position": rob.get("position") if cur_mode == "real" else None,
            # Потолок позиции, который агент реально сторожит (из спеки робота) —
            # панель печатает «поз 3 из 6», иначе число без масштаба ни о чём.
            "max_position": rob.get("max_position") if cur_mode == "real" else None,
            "varmargin": vm,
            "max_go": max_go or None,
            "started_ms": first_ts or None,
            "ann_pct": ann,
            "last_trade_ms": ra.get("last_ts") or 0,
        })
    # Сортировка: сначала активный реал, потом переведённые в бумагу, потом снятые;
    # внутри — по имени.
    _ord = {"реал": 0, "пауза": 0, "переведён в бумагу": 1, "снят": 2}
    robots.sort(key=lambda r: (_ord.get(r["state"], 3), r.get("name") or ""))

    # Разбивка позиции инструмента: сколько от РОБОТОВ (сумма их позиций) и сколько
    # РУКАМИ (остаток QUIK-нетто минус роботы — торговля оператора мимо STL).
    _robot_net_by_sec: dict[str, float] = {}
    for _rid in ids:
        _rob = mirror_by_id.get(_rid) or {}
        if _rob.get("mode") == "real" and _rob.get("symbol"):
            try:
                _robot_net_by_sec[_rob["symbol"]] = (
                    _robot_net_by_sec.get(_rob["symbol"], 0.0) + float(_rob.get("position") or 0))
            except (TypeError, ValueError):
                pass
    for p in positions:
        rn = _robot_net_by_sec.get(p["sec"], 0.0)
        p["robot_net"] = rn
        try:
            p["manual_net"] = float(p["net"]) - rn   # ручное = факт QUIK минус роботы
        except (TypeError, ValueError):
            p["manual_net"] = None

    # 3.5 Ручные заявки (#6): простые — из таблицы заявок QUIK (без тега = ручной
    # класс, включая детей умных заявок so:), умные — из книги STL. Только чтение.
    manual_orders = []
    for o in (status.get("quik") or {}).get("orders") or []:
        if o.get("tag"):
            continue  # роботные/recon — не ручные
        try:
            qty, bal = int(o.get("qty") or 0), int(o.get("balance") or 0)
        except (TypeError, ValueError):
            qty, bal = 0, 0
        if o.get("active"):
            st = "активна"
        elif bal == 0 and qty > 0:
            st = "исполнена"
        else:
            st = "снята" + (f", исполнено {qty - bal}" if qty > bal else "")
        manual_orders.append({"num": o.get("num"), "sec": o.get("sec"),
                              "side": o.get("side"), "price": o.get("price"),
                              "qty": qty, "balance": bal, "state": st,
                              "active": bool(o.get("active")),
                              "ts_ms": o.get("ts_ms")})
    manual_orders.sort(key=lambda d: (not d["active"], -(d.get("ts_ms") or 0)))
    smart_list = []
    so_book = getattr(request.app.state, "smart_orders", None)
    for so in (so_book.orders if so_book else []):
        if so.status != "armed" and max(so.created_ms, so.fired_ms) < today_lo:
            continue  # старую историю в панель не тянем
        smart_list.append({
            "so_id": so.so_id, "kind": so.kind, "code": so.code, "side": so.side,
            "qty": so.qty, "trigger": so.trigger_price,
            "trail_offset": so.trail_offset, "activated": so.activated,
            "peak": so.peak, "status": so.status, "fired_ms": so.fired_ms,
            # цена РЕАЛЬНОЙ сделки, а не уровень срабатывания: по уровню не понять,
            # во что обошёлся вход
            "fired_price": so.fired_price, "fired_qty": so.fired_qty,
            "sl_offset": so.sl_offset, "tp_offset": so.tp_offset,
            "parent_id": so.parent_id})
    orders_block = {"manual": manual_orders[:8], "smart": smart_list[:8]}

    # Состояние сессии MOEX (открыта/закрыта по ISS) — нужно и вотчеру раннера
    # (гейт лага ленты), и панели (отдельная строка «биржа»).
    market = getattr(request.app.state, "market_session", None)
    # ВРЕМЯ ПОСЛЕДНЕЙ СДЕЛКИ БЕРЁМ У АГЕНТА, А НЕ У ISS. Оракул сессии читает
    # ISS, а тот отдаёт рынок с задержкой около 15 минут: панель показывала
    # «торговали 14:21», когда в «Таблице всех сделок» оператора уже шло 14:37.
    # Для вопроса «открыт ли рынок» задержка безвредна (SYSTIME и сделка едут с
    # одинаковым сдвигом, лаг между ними не врёт), но как ЧАСЫ это ложь.
    # У агента та же лента в реальном времени: exchange_lag_ms — насколько
    # последняя сделка ленты отстала от момента снимка.
    market_out = market.to_dict() if hasattr(market, "to_dict") else dict(market or {})
    _lag = health.get("exchange_lag_ms")
    if received_ms and _lag is not None:
        try:
            market_out["tape_last_ms"] = int(received_ms) - int(_lag)
        except (TypeError, ValueError):
            pass

    # 4.1 runner + Lua
    watch_runner = _watch_runner(health, received_ms, now_ms, market)
    # КРОСС-ЗАЯВКИ. Биржа запрещает сделку с самим собой: если два робота одного
    # счёта одновременно выставляют встречные заявки по одному инструменту и цены
    # пересекаются, транзакция ОТКЛОНЯЕТСЯ («Обработка кросс-заявок блокирована»).
    # Отказ приходит без client_id, поэтому робот о нём не узнаёт: его заявка
    # просто не появляется, а намерение теряется до следующего бара. Раньше это
    # молчало в таблице транзакций QUIK — теперь видно оператору.
    _cross = [t for t in ((status.get("quik") or {}).get("trans") or [])
              if "кросс" in str(t.get("text") or "").lower()
              and int(t.get("ts_ms") or 0) >= today_lo]
    if _cross:
        watch_runner["issues"].append(
            f"кросс-заявки отклонены: {len(_cross)} (роботы одного счёта встали встречно)")
        watch_runner["ok"] = False

    # 4.2 backtests
    bt = {"ok": True, "issues": [], "running": 0, "queued": 0, "stuck": []}
    try:
        cnt = await pool.fetch(
            "SELECT status, count(*) AS n FROM backtest_runs "
            "WHERE status IN ('running','queued') GROUP BY status")
        for r in cnt:
            bt[r["status"]] = int(r["n"])
        stuck = await pool.fetch(
            "SELECT id, EXTRACT(EPOCH FROM (now() - coalesce(claimed_at, created_at)))::int "
            "AS elapsed FROM backtest_runs WHERE status='running' "
            "AND coalesce(claimed_at, created_at) < now() - interval '1 hour' "
            "ORDER BY elapsed DESC LIMIT 5")
        bt["stuck"] = [{"id": r["id"], "elapsed_sec": int(r["elapsed"])} for r in stuck]
        if bt["stuck"]:
            bt["issues"].append(f"зависших прогонов: {len(bt['stuck'])}")
            bt["ok"] = False
    except Exception as exc:  # DB hiccup must not blank the whole panel
        bt = {"ok": False, "issues": [f"нет данных по бэктестам: {exc.__class__.__name__}"],
              "running": 0, "queued": 0, "stuck": []}

    # 4.3 platform: hoster (this box), i9 (heartbeat), smain (watchdog freshness)
    hoster: dict = {"load": None}
    try:
        hoster["load"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    i9 = None
    try:
        i9 = i9_hb_view(
            await pool.fetchval("SELECT value FROM agent_control WHERE key='i9_heartbeat'"),
            time.time())
    except Exception:
        pass
    runs = _wd_read_jsonl("~/stl-watchdog-runs.jsonl")
    last_run = runs[-1] if runs else None
    last_run_ms = int((last_run or {}).get("ts_ms") or 0)
    # The probe is cron'd from smain every 10 min inside 06:55-23:55 MSK. Silence
    # during the session means smain (or its cron) is down — that IS the signal.
    hhmm = datetime.datetime.now(tz=_MSK).strftime("%H:%M")
    in_session = "06:55" <= hhmm <= "23:55"
    smain_silent = in_session and (not last_run_ms or now_ms - last_run_ms > 25 * 60 * 1000)
    plat_issues = []
    if hoster["load"] is not None and hoster["load"] > 8:
        plat_issues.append(f"хостер загружен: LA {hoster['load']}")
    if i9 and i9.get("stale"):
        plat_issues.append(f"i9 молчит {i9.get('age_sec')} с")
    if i9 and (i9.get("ram_pct") or 0) >= 92:
        plat_issues.append(f"i9 память {i9.get('ram_pct')}%")
    if smain_silent:
        plat_issues.append("вотчер молчит (smain/cron)")
    platform = {"ok": not plat_issues, "issues": plat_issues, "hoster": hoster,
                "i9": i9, "watchdog_last_ms": last_run_ms, "in_session": in_session}

    # Сигнальная лампа перебора: лучший СВЕЖИЙ кандидат из хитпарада за 24ч.
    # «Отличный» = ПРИБЫЛЬ при разумной устойчивости: сортируем по net_profit,
    # RF — только ФИЛЬТР (сортировка по RF выносит наверх вырожденные конфиги
    # с нулевой просадкой: RF 3.7 млн при +6к ₽ — шум, было на бою). Порог
    # прибыли отсекает копеечных чемпионов; сделок >= 30 — анти-survivorship.
    # КЭШ на минуту. Хит-парад — таблица на миллионы строк (3.5 млн / 1.6 ГБ на
    # 03.08.2026), и этот запрос единственный в снапшоте, который её трогает. Панель
    # опрашивает снапшот каждые несколько секунд: без кэша каждый опрос платил за
    # обход таблицы, и после четырёх залитых за сутки кампаний снапшот стал отвечать
    # за 23 с — компаньон показывал «нет связи». Индекс по created_at добавлен, но
    # кэш остаётся: лампа «свежий кандидат» меняется раз в кампанию, а не раз в
    # секунду, и панель не должна зависеть от того, насколько разрослась таблица.
    _star_cache = getattr(request.app.state, "_sweep_star_cache", None)
    sweep_star = _star_cache[1] if _star_cache else None
    try:
        if _star_cache and time.time() - _star_cache[0] < 60:
            raise _CachedStar          # свежий кэш — в базу не идём вовсе
        # fast >= slow — вырожденный MACD-класс конфиг (нулевая линия, сделки =
        # числовой шум): такие в кандидаты не берём, даже пока они ещё в базе.
        row = await pool.fetchrow(
            "SELECT campaign_run, strategy, symbol, net_profit, recovery_factor, "
            "total_trades FROM optimization_leaderboard "
            "WHERE created_at > now() - interval '24 hours' "
            "AND net_profit >= 50000 AND recovery_factor BETWEEN 3 AND 1000 "
            "AND total_trades >= 30 "
            "AND NOT (params ? 'fast' AND params ? 'slow' "
            "         AND (params->>'fast')::numeric >= (params->>'slow')::numeric) "
            "ORDER BY net_profit DESC LIMIT 1")
        if row:
            sweep_star = {
                "campaign": row["campaign_run"], "strategy": row["strategy"],
                "symbol": row["symbol"],
                "net": round(float(row["net_profit"] or 0)),
                "rf": round(float(row["recovery_factor"] or 0), 2),
                "trades": int(row["total_trades"] or 0),
            }
        else:
            sweep_star = None          # свежих кандидатов нет — лампа гаснет
        request.app.state._sweep_star_cache = (time.time(), sweep_star)
    except _CachedStar:
        pass
    except Exception:
        pass
    bt["star"] = sweep_star

    # Каждой тревоге — пометка контроля: рассосётся само или требует оператора.
    # По просьбе оператора каждый алерт заканчивается двоеточием и статусом.
    market_open = (market or {}).get("open")
    for blk in (watch_runner, bt, platform):
        blk["issues"] = [_with_control(t, market_open) for t in blk.get("issues", [])]

    # 5. Escalations: what the watchdog left unresolved + today's SMS.
    # items — структурировано для панели: свежие СВЕРХУ, каждая запись несёт
    # ok-флаг («восстановилось» = норма-зелёная, остальное — тревога-красная).
    esc = _wd_read_jsonl("~/stl-watchdog-escalations.jsonl")
    today = [e for e in esc if (e.get("ts_ms") or 0) >= today_lo]
    items = [{"ts_ms": int(e.get("ts_ms") or 0),
              "text": _with_control(e.get("text") or "", market_open),
              "ok": "восстановилось" in (e.get("text") or "").lower()}
             for e in today]
    for t in (last_run or {}).get("unresolved") or []:
        items.append({"ts_ms": last_run_ms, "text": _with_control(t, market_open),
                      "ok": False})
    items.sort(key=lambda x: x["ts_ms"], reverse=True)
    alerts = {
        "items": items[:8],
        "unresolved": [_with_control(t, market_open) for t in (last_run or {}).get("unresolved") or []],
        "found": (last_run or {}).get("found") or [],
        "sms_today": [_with_control(e.get("text") or "", market_open) for e in today[-5:]],
        "sms_count": len(today),
        "last_run_ms": last_run_ms,
    }

    return {
        "ts_ms": now_ms, "agent_seen_ms": received_ms,
        "ping_ms": health.get("rtt_ms"),   # пинг агент<->QUIK (pong RTT) для шапки
        "account": account, "positions": positions, "robots": robots,
        "orders": orders_block,
        "watch": {"runner": watch_runner, "backtests": bt, "platform": platform},
        "alerts": alerts, "market": market_out,
    }
