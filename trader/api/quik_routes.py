"""FastAPI routes for the QUIK agent link (sprint02 Phase 1, read-only).

Exposes agent status (link lamp / diagnostics / last_seen), securities, latest
tick + order book per code, and the "Интерфейс биржи" (exchange interface)
data-source selector (Finam Trade API vs QUIK agent), persisted in settings.

DATA SOURCE + status only. No order routing / placement (Guard 3).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth

router = APIRouter(prefix="/api/v1/quik", tags=["quik"])

_VALID_INTERFACES = ("finam", "quik")


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _store(request: Request):
    return getattr(request.app.state, "quik_store", None)


@router.get("/status")
async def quik_status(request: Request, agent_id: str | None = None):
    """Per-agent link status: lamp (green/yellow/red), diagnostics, last_seen."""
    _auth(request)
    store = _store(request)
    settings = request.app.state.settings
    if store is None:
        return {
            "enabled": settings.quik_agent_enabled,
            "listen": settings.quik_agent_grpc_listen,
            "agents": [],
        }
    return {
        "enabled": settings.quik_agent_enabled,
        "listen": settings.quik_agent_grpc_listen,
        "agents": store.status(agent_id),
    }


@router.get("/securities")
async def quik_securities(request: Request, agent_id: str | None = None):
    """FORTS securities reference reported by the agent (code, step, step cost)."""
    _auth(request)
    store = _store(request)
    if store is None:
        return {"items": []}
    return {"items": store.securities(agent_id)}


@router.get("/tick/{code}")
async def quik_tick(code: str, request: Request, agent_id: str | None = None):
    """Latest tick (last/bid/ask/OI) for one security code."""
    _auth(request)
    store = _store(request)
    tick = store.tick(code, agent_id) if store else None
    if tick is None:
        raise HTTPException(status_code=404, detail=f"no tick for {code}")
    return tick


@router.get("/orderbook/{code}")
async def quik_order_book(code: str, request: Request, agent_id: str | None = None):
    """Latest order book (стакан) for one security code."""
    _auth(request)
    store = _store(request)
    ob = store.order_book(code, agent_id) if store else None
    if ob is None:
        raise HTTPException(status_code=404, detail=f"no order book for {code}")
    return ob


@router.get("/params")
async def quik_params(request: Request, agent_id: str | None = None):
    """Price step / step cost params (commission coef) reported by the agent."""
    _auth(request)
    store = _store(request)
    return store.params(agent_id) if store else None


# ---- generic QUIK tables (any DDE sheet exported by the agent, read-only) ----

@router.get("/tables")
async def quik_tables(request: Request, agent_id: str | None = None):
    """List available raw tables (summaries) across agents or one agent.

    Each item: {agent_id, name, columns_count, rows_count, received_at_unix_ms}.
    """
    _auth(request)
    store = _store(request)
    if store is None:
        return {"tables": []}
    return {"tables": store.list_raw_tables(agent_id)}


@router.get("/tables/{name}")
async def quik_table(name: str, request: Request, agent_id: str | None = None):
    """Full raw table by name: {columns, rows, received_at_unix_ms}."""
    _auth(request)
    store = _store(request)
    tbl = store.get_raw_table(name, agent_id) if store else None
    if tbl is None:
        raise HTTPException(status_code=404, detail=f"no table {name}")
    return tbl


# ---- "Интерфейс биржи" exchange-interface selector (data source only) ----

class ExchangeInterface(BaseModel):
    interface: str  # "finam" | "quik"


@router.get("/exchange-interface")
async def get_exchange_interface(request: Request):
    """Current exchange data-source interface selection."""
    _auth(request)
    settings = request.app.state.settings
    return {
        "interface": getattr(settings, "exchange_interface", "finam"),
        "options": [
            {"value": "finam", "label": "Finam Trade API"},
            {"value": "quik", "label": "QUIK агент"},
        ],
    }


@router.put("/exchange-interface")
async def set_exchange_interface(body: ExchangeInterface, request: Request):
    """Persist the exchange data-source interface selection.

    DATA SOURCE only. Switching to 'quik' does not route or place orders; it
    selects where market data / reference comes from for display.
    """
    _auth(request)
    if body.interface not in _VALID_INTERFACES:
        raise HTTPException(
            status_code=400,
            detail=f"interface must be one of {_VALID_INTERFACES}",
        )
    settings = request.app.state.settings
    # In-process persistence: pydantic-settings reads env at boot; we mutate the
    # live Settings object so the choice survives for this process. A durable
    # store (env/DB) is wired by sub-agent D's settings persistence layer.
    settings.exchange_interface = body.interface
    return {"interface": settings.exchange_interface, "ok": True}


# ---- watchdog activation log ----
# The trading-health watchdog (hoster ~/stl-watchdog-probe.sh, cron'd from smain
# every 10 min) appends one JSONL record per activation to ~/stl-watchdog-runs.jsonl;
# smain appends every SMS escalation to ~/stl-watchdog-escalations.jsonl. This
# endpoint merges the two by timestamp for the watchdog-log.html page.

_WD_RUNS = "~/stl-watchdog-runs.jsonl"
_WD_ESCALATIONS = "~/stl-watchdog-escalations.jsonl"
_WD_JOIN_MS = 5 * 60 * 1000  # an SMS belongs to the probe run within this window


def _wd_read_jsonl(path: str) -> list[dict]:
    import json as _json
    import os as _os
    full = _os.path.expanduser(path)
    out: list[dict] = []
    try:
        with open(full, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


@router.get("/watchdog-log")
async def watchdog_log(request: Request, date_from: str | None = None,
                       date_to: str | None = None):
    """Watchdog activations for a period (MSK calendar dates, inclusive; default
    today): what was checked, what was found, what remains unresolved, and
    whether an SMS escalation went out for that run."""
    import datetime as _dt
    _auth(request)
    msk = _dt.timezone(_dt.timedelta(hours=3))
    today = _dt.datetime.now(tz=msk).date().isoformat()
    d_from = date_from or today
    d_to = date_to or d_from

    def day_ms(d: str, end: bool = False) -> int:
        t = _dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=msk)
        return int(t.timestamp() * 1000) + (24 * 3600 * 1000 if end else 0)

    lo, hi = day_ms(d_from), day_ms(d_to, end=True)
    runs = [r for r in _wd_read_jsonl(_WD_RUNS) if lo <= (r.get("ts_ms") or 0) < hi]
    esc = _wd_read_jsonl(_WD_ESCALATIONS)

    for r in runs:
        ts = r.get("ts_ms") or 0
        r["sms"] = [e.get("text") for e in esc
                    if abs((e.get("ts_ms") or 0) - ts) <= _WD_JOIN_MS]
        r["dt_msk"] = _dt.datetime.fromtimestamp(ts / 1000, tz=msk).strftime(
            "%Y-%m-%d %H:%M:%S")
    return {"runs": runs, "date_from": d_from, "date_to": d_to}


# ---- watchdog operator config ----
# ~/stl-watchdog-config.json on the hoster, read by the probe on every activation.
# Editable from the watchdog-log page: per-event SMS escalation toggles, the two
# auto-pause switches (critical-case actions) and check thresholds. smain-side
# parameters (10-min cron interval, 06:55-23:55 window, 60-min SMS cooldown per
# key) are NOT here — they live in smain cron/script and are shown read-only.

_WD_CONFIG = "~/stl-watchdog-config.json"
_WD_CFG_DEFAULTS: dict = {
    "escalate": {"api_down": True, "link_down": True, "runner_sick": True,
                 "cap_near": True, "cap_full": True, "tape_lag": True,
                 "bars": True, "hb": True, "ord": True,
                 "paused": True, "pausefail": True, "backtest_stuck": True},
    "autopause_tape_lag": True,
    "autopause_bars": True,
    "thresholds": {"tape_lag_sec": 120, "cap_warn_pct": 85, "hb_sec": 150,
                   "order_recheck_sec": 15, "bars_atr_mult": 3.0, "bars_pct": 0.5,
                   "backtest_stuck_sec": 3600},
}
_WD_THR_BOUNDS = {"tape_lag_sec": (10, 3600), "cap_warn_pct": (10, 100),
                  "hb_sec": (30, 3600), "order_recheck_sec": (0, 120),
                  "bars_atr_mult": (0.5, 20.0), "bars_pct": (0.05, 10.0),
                  "backtest_stuck_sec": (60, 86400)}


def _wd_config_read() -> dict:
    import copy
    import json as _json
    import os as _os
    cfg = copy.deepcopy(_WD_CFG_DEFAULTS)
    try:
        with open(_os.path.expanduser(_WD_CONFIG), encoding="utf-8") as f:
            user = _json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            elif k in cfg:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


@router.get("/watchdog-config")
async def watchdog_config_get(request: Request):
    _auth(request)
    return {"config": _wd_config_read(),
            "readonly": {"interval_min": 10, "session": "06:55-23:55 МСК",
                         "sms_cooldown_min": 60,
                         "note": "интервал/окно/кулдаун живут в cron на smain"}}


class WatchdogConfigBody(BaseModel):
    escalate: dict[str, bool] | None = None
    autopause_tape_lag: bool | None = None
    autopause_bars: bool | None = None
    thresholds: dict[str, float] | None = None


@router.put("/watchdog-config")
async def watchdog_config_put(body: WatchdogConfigBody, request: Request):
    """Partial update; unknown escalate/threshold keys are rejected, thresholds
    clamped to sane bounds so a typo cannot silence the watchdog entirely."""
    import json as _json
    import os as _os
    _auth(request)
    cfg = _wd_config_read()
    if body.escalate is not None:
        bad = set(body.escalate) - set(_WD_CFG_DEFAULTS["escalate"])
        if bad:
            raise HTTPException(status_code=422, detail=f"неизвестные события: {sorted(bad)}")
        cfg["escalate"].update({k: bool(v) for k, v in body.escalate.items()})
    if body.autopause_tape_lag is not None:
        cfg["autopause_tape_lag"] = bool(body.autopause_tape_lag)
    if body.autopause_bars is not None:
        cfg["autopause_bars"] = bool(body.autopause_bars)
    if body.thresholds is not None:
        bad = set(body.thresholds) - set(_WD_THR_BOUNDS)
        if bad:
            raise HTTPException(status_code=422, detail=f"неизвестные пороги: {sorted(bad)}")
        for k, v in body.thresholds.items():
            lo, hi = _WD_THR_BOUNDS[k]
            if not (lo <= float(v) <= hi):
                raise HTTPException(status_code=422,
                                    detail=f"{k}: допустимо {lo}..{hi}, получено {v}")
            cfg["thresholds"][k] = float(v)
    full = _os.path.expanduser(_WD_CONFIG)
    tmp = full + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(cfg, f, ensure_ascii=False, indent=1)
    _os.replace(tmp, full)
    return {"ok": True, "config": cfg}
