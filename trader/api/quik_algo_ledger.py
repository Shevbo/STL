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


def _margin_map(request: Request) -> dict[str, float]:
    """symbol -> initial margin (₽/contract, BUYDEPO) from the agent's opaque
    status mirror (health.params). Empty until the agent build that relays
    margin is live — callers treat missing symbols as 'ГО unknown'."""
    store = getattr(request.app.state, "quik_store", None)
    status = store.agent_status(None) if store is not None else None
    out: dict[str, float] = {}
    for p in ((status or {}).get("health") or {}).get("params", []) or []:
        m = float(p.get("margin") or 0)
        if m > 0:
            out[p.get("code", "")] = m
    return out


@router.get("/algo-report")
async def algo_report(request: Request, days: int = 30, mode: str | None = None,
                      robot_id: str | None = None):
    """Aggregates for reports/charts over the last N MSK days: per-day totals,
    per-robot totals, per-robot DAILY series (cum net for the multi-line chart),
    and занятое ГО (max held |position| x initial margin) per day/robot. The
    period return (return_pct) is total net over the PEAK total ГО of the
    period. ГО fields are null until the agent relays margin (health.params)."""
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
        f"SELECT robot_id, mode, symbol, ts_ms, qty, pos_after, pnl_gross_rub, "
        f"commission_rub, pnl_net_rub FROM algo_trades WHERE {cond} ORDER BY ts_ms",
        *args)
    # Positions carried INTO the window still занимают ГО on fill-less days:
    # per robot, the last pos_after strictly before the window start.
    carry = {r["robot_id"]: r["pos_after"] for r in await pool.fetch(
        """SELECT DISTINCT ON (robot_id) robot_id, pos_after FROM algo_trades
           WHERE ts_ms < $1 ORDER BY robot_id, seq DESC""", lo)}

    margins = _margin_map(request)
    all_dates = [(start.date() + datetime.timedelta(days=i)).isoformat()
                 for i in range(days) if start.date() + datetime.timedelta(days=i) <= today]

    daily: dict[str, dict] = {}
    robots: dict[str, dict] = {}
    by_robot_day: dict[str, dict[str, dict]] = {}
    for r in rows:
        date = msk_date(r["ts_ms"])
        d = daily.setdefault(date, {
            "trades": 0, "contracts": 0, "gross": 0.0, "commission": 0.0, "net": 0.0})
        d["trades"] += 1
        d["contracts"] += r["qty"]
        d["gross"] += r["pnl_gross_rub"]
        d["commission"] += r["commission_rub"]
        d["net"] += r["pnl_net_rub"]
        b = robots.setdefault(r["robot_id"], {
            "mode": r["mode"], "symbol": r["symbol"], "trades": 0, "contracts": 0,
            "gross": 0.0, "commission": 0.0, "net": 0.0})
        b["trades"] += 1
        b["contracts"] += r["qty"]
        b["gross"] += r["pnl_gross_rub"]
        b["commission"] += r["commission_rub"]
        b["net"] += r["pnl_net_rub"]
        # Per-fill cum-net curve -> realized max drawdown (rows come ORDER BY ts_ms).
        # REALIZED only: open-position MTM drawdown is invisible to the fill journal.
        cum = b["_cum"] = b.get("_cum", 0.0) + r["pnl_net_rub"]
        b["_peak"] = max(b.get("_peak", cum), cum)
        b["_maxdd"] = max(b.get("_maxdd", 0.0), b["_peak"] - cum)
        rd = by_robot_day.setdefault(r["robot_id"], {}).setdefault(
            date, {"net": 0.0, "pos_max": 0, "pos_last": 0})
        rd["net"] += r["pnl_net_rub"]
        rd["pos_max"] = max(rd["pos_max"], abs(r["pos_after"]))
        rd["pos_last"] = r["pos_after"]

    # Robots that only CARRY a position through the window still bind ГО.
    for rid, pos in carry.items():
        if pos and rid not in by_robot_day and (not robot_id or rid == robot_id):
            by_robot_day[rid] = {}
            robots.setdefault(rid, {"mode": None, "symbol": None, "trades": 0,
                                    "contracts": 0, "gross": 0.0, "commission": 0.0,
                                    "net": 0.0})

    series: dict[str, list] = {}
    day_go: dict[str, float] = {}
    for rid, days_map in by_robot_day.items():
        sym = robots.get(rid, {}).get("symbol")
        margin = margins.get(sym) if sym else None
        pos = carry.get(rid, 0)
        cum = 0.0
        out = []
        for date in all_dates:
            rd = days_map.get(date)
            held = max(abs(pos), rd["pos_max"]) if rd else abs(pos)
            net = rd["net"] if rd else 0.0
            cum += net
            go = round(held * margin, 2) if (margin and held) else (0.0 if margin else None)
            if go:
                day_go[date] = day_go.get(date, 0.0) + go
            out.append({"date": date, "net": round(net, 2), "cum_net": round(cum, 2),
                        "pos_max": held, "go_rub": go})
            if rd:
                pos = rd["pos_last"]
        series[rid] = out
        b = robots.get(rid)
        if b is not None:
            peaks = [p["go_rub"] for p in out if p["go_rub"]]
            b["peak_go_rub"] = max(peaks) if peaks else None

    out_days, cum = [], 0.0
    for date in all_dates:
        d = daily.get(date)
        if not d and date not in day_go:
            continue
        d = d or {"trades": 0, "contracts": 0, "gross": 0.0, "commission": 0.0, "net": 0.0}
        cum += d["net"]
        out_days.append({"date": date, **{k: round(v, 2) for k, v in d.items()},
                         "cum_net": round(cum, 2),
                         "go_rub": round(day_go[date], 2) if date in day_go else None})

    # Per-fill cum-net series (chart «по сделкам»): exact trade ticks instead of
    # daily cutoffs. rows are already ORDER BY ts_ms.
    series_fills: dict[str, list] = {}
    for r in rows:
        sf = series_fills.setdefault(r["robot_id"], [])
        cum = (sf[-1]["cum_net"] if sf else 0.0) + r["pnl_net_rub"]
        sf.append({"ts_ms": r["ts_ms"], "net": round(r["pnl_net_rub"], 2),
                   "cum_net": round(cum, 2)})

    total_net = round(sum(d["net"] for d in daily.values()), 2)
    peak_go = max((v for v in day_go.values()), default=None)
    out_robots = []
    for rid, b in sorted(robots.items()):
        maxdd = b.pop("_maxdd", 0.0)
        b.pop("_cum", None)
        b.pop("_peak", None)
        row = {"robot_id": rid,
               **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in b.items()}}
        pk = b.get("peak_go_rub")
        row["return_pct"] = round(b["net"] / pk * 100, 2) if pk else None
        row["max_dd_rub"] = round(maxdd, 2) if maxdd else None
        row["rf"] = round(b["net"] / maxdd, 2) if maxdd > 0 else None
        out_robots.append(row)
    return {"days": out_days, "robots": out_robots, "series": series,
            "series_fills": series_fills,
            "total_net": total_net,
            "peak_go_rub": round(peak_go, 2) if peak_go else None,
            "return_pct": round(total_net / peak_go * 100, 2) if peak_go else None,
            "margin_known": bool(margins)}
