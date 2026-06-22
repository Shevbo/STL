"""team-46 sweep unit, executed on the i9 via the generic agent task queue.

One unit = one (param-combo, symbol). The agent fans many units across its process
pool. Bars are fetched from MOEX ISS once per (symbol, date-range) and disk-cached in
the OS temp dir so repeated combos for a symbol reuse them. Returns a JSON-serializable
metrics row (net/gross/fees/trades) for the leaderboard.
"""
from __future__ import annotations

import asyncio
import os
import pickle
import tempfile
from datetime import date

import httpx

from trader.lab.ai46.backtest import Ai46Backtester
from trader.lab.ai46.params import BotParams
from trader.lab.iss_loader import load_bars_iss
from trader.lab.runtime import Bar

_CACHE = os.path.join(tempfile.gettempdir(), "ai46_bt_cache")


def _hoster_bars(key: str) -> list:
    """Pre-fetched bars served by the hoster (fast). The agent's own continuous-roll
    fetch from ISS enumerates ~120 contracts and hangs on its network, so bars are
    prepared centrally and pulled over the (proven-fast) i9->hoster link instead."""
    api = os.environ.get("STL_API", "https://stl.shectory.ru").rstrip("/")
    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    # TLS verification stays ON by default; if the i9 sits behind a TLS-intercepting
    # proxy, the agent's existing --insecure / OPT_AGENT_INSECURE path globally relaxes
    # httpx in the worker (_run_task_unit) — we don't weaken it here.
    r = httpx.get(f"{api}/api/v1/agent/bars/{key}",
                  headers={"X-Agent-Token": tok}, timeout=120)
    r.raise_for_status()
    return r.json().get("rows", [])


def _cached_bars(key: str, date_from: str, date_to: str) -> list:
    os.makedirs(_CACHE, exist_ok=True)
    path = os.path.join(_CACHE, f"{key}_{date_from}_{date_to}.pkl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:  # noqa: BLE001 — corrupt cache → refetch
            pass

    bars: list = []
    try:
        rows = _hoster_bars(key)            # primary: served by the hoster
        bars = [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
                for r in rows]
    except Exception:  # noqa: BLE001 — fall back to ISS with a hard timeout
        bars = []
    if not bars:
        async def _iss():
            return await asyncio.wait_for(
                load_bars_iss(key, date.fromisoformat(date_from),
                              date.fromisoformat(date_to), interval=1),
                timeout=180,
            )
        try:
            bars = asyncio.run(_iss())
        except Exception:  # noqa: BLE001 — never hang the worker
            bars = []
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(bars, f)
        os.replace(tmp, path)   # atomic; tolerates concurrent writers
    except Exception:  # noqa: BLE001
        pass
    return bars


def run_combo(arg: dict) -> dict:
    """arg = {key, fields, date_from, date_to, point_value, cfg}. Runs one combo on one
    symbol and returns its net/gross/fees/trades."""
    key = arg["key"]
    fields = arg["fields"]
    cfg = arg["cfg"]
    try:
        bars = _cached_bars(key, arg["date_from"], arg["date_to"])
        if not bars:
            return {"key": key, "combo": fields, "error": "no bars"}
        bt = Ai46Backtester(
            {key: bars}, step_secs=cfg["step"], window_secs=cfg["window_days"] * 86400,
            ofi_mode=cfg.get("ofi_mode", "proxy"), model_refresh_secs=cfg["refresh"],
            model_window=cfg["model_window"], model_iter=cfg["model_iter"],
            point_values={key: arg.get("point_value", 1.0)},
            taker=cfg.get("taker", False), params=BotParams(**fields),
        )
        m = asyncio.run(bt.run())
        return {"key": key, "combo": fields, "net": m["net_pnl"], "gross": m["gross_pnl"],
                "fees": m["fees"], "trades": m["trades_closed"], "ticks": m["ticks"]}
    except Exception as exc:  # noqa: BLE001
        return {"key": key, "combo": fields, "error": f"{type(exc).__name__}: {exc}"}
