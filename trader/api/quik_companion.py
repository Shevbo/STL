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


def _watch_runner(health: dict, received_ms: int | None, now_ms: int) -> dict:
    """4.1 — runner + Lua. Everything here is a FACT from the agent's own status
    mirror; nothing is inferred from the watchdog's prose."""
    issues = []
    if received_ms and now_ms - received_ms > 120_000:
        issues.append(f"нет связи с агентом {(now_ms - received_ms) // 1000} с")
    if not health.get("runner_healthy", False):
        issues.append("раннер не отвечает")
    age = health.get("runner_report_age_ms")
    if isinstance(age, int) and age > 120_000:
        issues.append(f"отчёт раннера {age // 1000} с назад")
    lag = health.get("exchange_lag_ms")
    if isinstance(lag, int) and lag > 120_000:
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
async def snapshot(request: Request, agent_id: str | None = None):
    """Everything the tray panel shows, in one read. Companion token or session.

    Read-only aggregation over mirrors STL already holds: no agent command is
    enqueued, no DB write happens beyond the device's own last_seen stamp.
    """
    await _companion_or_operator(request)
    pool = _pool(request)
    now_ms = int(time.time() * 1000)

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
    positions = [
        {"sec": p.get("sec"), "net": p.get("net"), "avg": p.get("avg"),
         "varmargin": p.get("varmargin")}
        for p in (health.get("positions") or []) if p.get("net")
    ]

    # 3. Robots: agent truth + today's ledger + the operator's display names.
    names = {}
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM agent_control WHERE key LIKE $1", "robotname:%")
        names = {r["key"][len("robotname:"):]: r["value"] for r in rows}
    except Exception:
        pass
    today_lo = _msk_midnight_ms()
    ledger = {}
    try:
        for r in await pool.fetch(
                "SELECT robot_id, count(*) AS trades, sum(pnl_net_rub) AS net, "
                "max(ts_ms) AS last_ts FROM algo_trades WHERE ts_ms >= $1 "
                "GROUP BY robot_id", today_lo):
            ledger[r["robot_id"]] = {"trades": int(r["trades"]),
                                     "net": float(r["net"] or 0),
                                     "last_ts": int(r["last_ts"] or 0)}
    except Exception:
        pass
    robots = []
    for rob in status.get("robots") or []:
        rid = rob.get("id")
        led = ledger.get(rid) or {}
        robots.append({
            "id": rid, "name": names.get(rid) or rid, "symbol": rob.get("symbol"),
            "mode": rob.get("mode"), "paused": rob.get("paused"),
            "position": rob.get("position"),
            "pnl_rub": rob.get("pnl_rub"), "float_rub": rob.get("float_rub"),
            "today_net": led.get("net"), "today_trades": led.get("trades") or 0,
            "last_trade_ms": led.get("last_ts") or 0,
        })
    robots.sort(key=lambda r: (r.get("mode") != "real", r.get("name") or ""))

    # 4.1 runner + Lua
    watch_runner = _watch_runner(health, received_ms, now_ms)

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

    # 5. Escalations: what the watchdog left unresolved + today's SMS.
    esc = _wd_read_jsonl("~/stl-watchdog-escalations.jsonl")
    today_sms = [e.get("text") for e in esc if (e.get("ts_ms") or 0) >= today_lo]
    alerts = {
        "unresolved": (last_run or {}).get("unresolved") or [],
        "found": (last_run or {}).get("found") or [],
        "sms_today": today_sms[-5:],
        "sms_count": len(today_sms),
        "last_run_ms": last_run_ms,
    }

    return {
        "ts_ms": now_ms, "agent_seen_ms": received_ms,
        "account": account, "positions": positions, "robots": robots,
        "watch": {"runner": watch_runner, "backtests": bt, "platform": platform},
        "alerts": alerts,
    }
