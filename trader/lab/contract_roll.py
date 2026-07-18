"""Front-contract resolver + roll helper for paper Lab robots.

A paper robot's params.symbol is a specific contract (e.g. RIM6). When it expires
the ISS feed dies and the robot freezes. front_contract() maps the symbol's series
to today's live front contract (RIM6 -> RIU6) so the scheduler can roll it.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx

from trader.lab.iss_loader import is_specific_contract

_SECURITIES_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
    "?iss.meta=off&iss.only=securities"
    "&securities.columns=SECID,LASTTRADEDATE"
)
_CACHE_TTL = 6 * 3600.0  # front changes quarterly/monthly — one ISS call per 6h
_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def base_of(symbol: str) -> str | None:
    """Series base of a specific contract: RIM6->RI, BRN6->BR, SiU6->Si.
    None if `symbol` is not a specific FORTS contract."""
    if not symbol or not is_specific_contract(symbol):
        return None
    return symbol[:-2]


async def _securities() -> list[tuple[str, str]]:
    """(SECID, LASTTRADEDATE) for all FORTS securities, cached 6h."""
    now = _time.monotonic()
    hit = _cache.get("all")
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "STL/1.0"}) as c:
        j = (await c.get(_SECURITIES_URL)).json()
    sec = j.get("securities", {})
    cols = sec.get("columns", [])
    rows: list[tuple[str, str]] = []
    for row in sec.get("data", []):
        d = dict(zip(cols, row))
        sid, ltd = d.get("SECID"), d.get("LASTTRADEDATE")
        if sid and ltd:
            rows.append((sid, ltd))
    if rows:
        _cache["all"] = (now, rows)
    return rows


async def front_contract(symbol: str, today: date | None = None) -> str | None:
    """Today's live front contract for `symbol`'s series (RIM6 -> RIU6).
    None if `symbol` isn't a specific contract or ISS returned nothing usable."""
    base = base_of(symbol)
    if base is None:
        return None
    today = today or date.today()
    try:
        rows = await _securities()
    except Exception:
        return None
    cands: list[tuple[str, date]] = []
    for sid, ltd in rows:
        if base_of(sid) != base:
            continue
        try:
            d = datetime.strptime(ltd, "%Y-%m-%d").date()
        except Exception:
            continue
        if d >= today:
            cands.append((sid, d))
    if not cands:
        return None
    return min(cands, key=lambda x: x[1])[0]


def fills_to_rows(robot_id: str, symbol: str, fills: list[dict]) -> list[tuple]:
    """Map backtest fills -> live_trades INSERT tuples for the gap simulation.
    ISS bar time is MSK-wall stamped as UTC; live_trades.timestamp is true UTC, so
    shift -3h and store naive. Column order matches the INSERT in the backfill script.
    fills: list of {side, price, qty, time} from backtest.run_single_backtest."""
    rows: list[tuple] = []
    for f in fills:
        ts = datetime.utcfromtimestamp(int(f["time"]) - 3 * 3600)
        rows.append((
            uuid4().hex, robot_id, symbol, f["side"], int(f["qty"]),
            Decimal(str(f["price"])), "sim-" + uuid4().hex[:10], "paper", ts,
        ))
    return rows
