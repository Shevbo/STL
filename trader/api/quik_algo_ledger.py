"""API over the algo-trade ledger (algo_trades): the journal itself with CSV
export, and daily/per-robot aggregates for reports and charts. See
trader/quik/algo_ledger.py for the ingest side and the accounting semantics."""
from __future__ import annotations

import csv
import datetime
import io

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from trader.auth.guard import require_auth
from trader.quik.algo_ledger import msk_date

router = APIRouter(prefix="/api/v1/quik", tags=["quik-algo-ledger"])

_MSK = datetime.timezone(datetime.timedelta(hours=3))


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    return pool


def _date_bounds(date_from: str | None, date_to: str | None) -> tuple[int | None, int | None]:
    """MSK calendar dates -> [start_ms, end_ms) epoch bounds."""
    def day_start(d: str) -> int:
        dt = datetime.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=_MSK)
        return int(dt.timestamp() * 1000)
    lo = day_start(date_from) if date_from else None
    hi = day_start(date_to) + 24 * 3600 * 1000 if date_to else None
    return lo, hi


_COLS = ("seq", "robot_id", "mode", "ts_ms", "trade_num", "order_num", "symbol",
         "side", "qty", "price", "order_kind", "pnl_gross_rub", "commission_rub",
         "pnl_net_rub", "pos_after")


@router.get("/algo-trades")
async def algo_trades(request: Request, robot_id: str | None = None,
                      mode: str | None = None, symbol: str | None = None,
                      date_from: str | None = None, date_to: str | None = None,
                      limit: int = 500, format: str | None = None):
    """The ledger, newest first. Filters: robot_id, mode (real|paper), symbol,
    date_from/date_to (MSK calendar dates, inclusive). format=csv exports the
    filtered set (limit capped at 50k for CSV, 5k for JSON)."""
    _auth(request)
    pool = _pool(request)
    is_csv = (format or "").lower() == "csv"
    limit = max(1, min(int(limit), 50_000 if is_csv else 5_000))
    lo, hi = _date_bounds(date_from, date_to)

    where, args = [], []
    def add(cond: str, val):
        args.append(val)
        where.append(cond.replace("?", f"${len(args)}"))
    if robot_id:
        add("robot_id = ?", robot_id)
    if mode in ("real", "paper"):
        add("mode = ?", mode)
    if symbol:
        add("symbol = ?", symbol)
    if lo is not None:
        add("ts_ms >= ?", lo)
    if hi is not None:
        add("ts_ms < ?", hi)
    args.append(limit)
    sql = (f"SELECT {', '.join(_COLS)}, avg_after FROM algo_trades"
           + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY seq DESC LIMIT ${len(args)}")
    rows = await pool.fetch(sql, *args)

    def enrich(r) -> dict:
        d = dict(r)
        d["dt_msk"] = datetime.datetime.fromtimestamp(
            r["ts_ms"] / 1000, tz=_MSK).strftime("%Y-%m-%d %H:%M:%S")
        return d

    if is_csv:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(("seq", "robot_id", "mode", "dt_msk", "trade_num", "order_num",
                    "symbol", "side", "qty", "price", "order_kind",
                    "pnl_gross_rub", "commission_rub", "pnl_net_rub", "pos_after"))
        for r in reversed(rows):  # CSV oldest-first reads naturally in Excel
            d = enrich(r)
            w.writerow([d[c] if c != "ts_ms" else d["dt_msk"] for c in _COLS])
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=algo_trades.csv"})
    return {"trades": [enrich(r) for r in rows], "count": len(rows)}


@router.get("/algo-report")
async def algo_report(request: Request, days: int = 30, mode: str | None = None,
                      robot_id: str | None = None):
    """Aggregates for reports/charts over the last N MSK days: per-day totals
    (trades, contracts, gross, commission, net) and per-robot totals over the
    same window. Cumulative net per day included for an equity-style chart."""
    _auth(request)
    pool = _pool(request)
    days = max(1, min(int(days), 366))
    today = datetime.datetime.now(tz=_MSK).date()
    start = datetime.datetime.combine(
        today - datetime.timedelta(days=days - 1),
        datetime.time.min, tzinfo=_MSK)
    lo = int(start.timestamp() * 1000)

    where, args = ["ts_ms >= $1"], [lo]
    if mode in ("real", "paper"):
        args.append(mode)
        where.append(f"mode = ${len(args)}")
    if robot_id:
        args.append(robot_id)
        where.append(f"robot_id = ${len(args)}")
    cond = " AND ".join(where)
    rows = await pool.fetch(
        f"SELECT robot_id, mode, ts_ms, qty, pnl_gross_rub, commission_rub, "
        f"pnl_net_rub FROM algo_trades WHERE {cond} ORDER BY ts_ms", *args)

    daily: dict[str, dict] = {}
    robots: dict[str, dict] = {}
    for r in rows:
        d = daily.setdefault(msk_date(r["ts_ms"]), {
            "trades": 0, "contracts": 0, "gross": 0.0, "commission": 0.0, "net": 0.0})
        d["trades"] += 1
        d["contracts"] += r["qty"]
        d["gross"] += r["pnl_gross_rub"]
        d["commission"] += r["commission_rub"]
        d["net"] += r["pnl_net_rub"]
        b = robots.setdefault(r["robot_id"], {
            "mode": r["mode"], "trades": 0, "contracts": 0,
            "gross": 0.0, "commission": 0.0, "net": 0.0})
        b["trades"] += 1
        b["contracts"] += r["qty"]
        b["gross"] += r["pnl_gross_rub"]
        b["commission"] += r["commission_rub"]
        b["net"] += r["pnl_net_rub"]

    out_days, cum = [], 0.0
    for date in sorted(daily):
        d = daily[date]
        cum += d["net"]
        out_days.append({"date": date, **{k: round(v, 2) for k, v in d.items()},
                         "cum_net": round(cum, 2)})
    out_robots = [{"robot_id": rid, **{k: (round(v, 2) if isinstance(v, float) else v)
                                       for k, v in b.items()}}
                  for rid, b in sorted(robots.items())]
    return {"days": out_days, "robots": out_robots,
            "total_net": round(sum(d["net"] for d in daily.values()), 2)}
