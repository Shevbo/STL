import asyncio
import math
import multiprocessing
import os
from types import ModuleType
from typing import Any

from trader.lab.commission import commission_for
from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.script_guard import validate_script
from trader.lab.window_metrics import window_metrics


def _demote_to_background() -> None:
    """Make THIS process a background CPU/IO citizen so a heavy param sweep never
    starves interactive services (sshd, nginx, uvicorn). Called at the start of
    every backtest subprocess. All best-effort — never raises."""
    # Lowest CPU scheduling priority (nice 19).
    try:
        os.nice(19)
    except Exception:
        pass
    # Linux: SCHED_IDLE — only runs when nothing else wants the CPU.
    try:
        if hasattr(os, "SCHED_IDLE") and hasattr(os, "sched_setscheduler"):
            os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))  # type: ignore[attr-defined]
    except Exception:
        pass
    # Lowest IO priority (idle class) via ionice, if available.
    try:
        import psutil  # type: ignore
        p = psutil.Process()
        if hasattr(psutil, "IOPRIO_CLASS_IDLE"):
            p.ionice(psutil.IOPRIO_CLASS_IDLE)
    except Exception:
        pass


def compute_metrics(trades: list[dict], initial_equity: float,
                    point_value: float = 1.0,
                    symbol: str = "",
                    initial_margin: float = 0.0,
                    bars_days: float = 0.0) -> dict[str, Any]:
    """
    Round-trip metrics. PnL per pair is multiplied by point_value so all money
    figures are in RUBLES (RIM6 ~1.42 ₽/point). Handles both long (buy→sell) and
    short (sell→buy) round-trips by tracking signed entry. Backtests model every
    fill as a TAKER order, so each fill's commission = MOEX exchange fee (by
    instrument group, on notional) + broker fee. The entry fill's fee and the
    closing fill's fee are both charged to the round-trip they belong to, so
    per-pair PnL and all aggregates (net_profit, win_rate, drawdown) are net of
    commission. (Live trading is maker-only — see LiveRuntime.)

    Return/drawdown are measured against the REAL capital at risk, not a flat
    100k: margin_used = peak_contracts × initial_margin (ГО per contract from
    MOEX ISS). So a robot that averages up to 10 RTS contracts is scored on
    ~10×ГО, not on 100k. Falls back to initial_equity when ГО is unknown.

    Two annualized return flavors (require bars_days > 0):
      ann_return_go    — % p.a. on margin (ГО mode, with leverage)
      ann_return_full  — % p.a. on full notional (peak_contracts × avg_entry_price × point_value)
    """
    empty = {"total_trades": 0, "win_rate": 0.0, "total_return": 0.0,
             "sharpe": None, "max_drawdown": 0.0, "recovery_factor": None,
             "net_profit": 0.0, "peak_contracts": 0, "margin_used": 0.0,
             "ann_return_go": None, "ann_return_full": None,
             "max_mae": 0.0, "max_drawdown_mtm": 0.0, "recovery_factor_mtm": None,
             "closed_pairs": []}
    if not trades:
        return empty

    # Peak simultaneous contracts over the run → real margin (ГО) committed.
    _signed = 0
    peak_contracts = 0
    for t in trades:
        _signed += t["qty"] * (1 if t["side"] == "buy" else -1)
        peak_contracts = max(peak_contracts, abs(_signed))
    margin_used = (peak_contracts * initial_margin) if (initial_margin and peak_contracts) else initial_equity
    if margin_used <= 0:
        margin_used = initial_equity

    pairs: list[dict] = []   # {"time": exit_time, "pnl": net_rub} per closed round-trip
    pos_qty = 0               # signed open position, contracts
    pos_avg = 0.0             # entry average of the OPEN position
    carried_entry_fee = 0.0   # entry/averaging fees not yet realized against a close
    # Track weighted-average entry price across all round-trips for notional calc.
    _entry_price_sum = 0.0
    _entry_qty_sum = 0
    for t in trades:
        q = t["qty"] * (1 if t["side"] == "buy" else -1)
        p = t["price"]
        c = commission_for(symbol, p, t["qty"], point_value, taker=True)
        if pos_qty == 0:                       # flat → open
            pos_qty, pos_avg, carried_entry_fee = q, p, c
            _entry_price_sum += p * t["qty"]
            _entry_qty_sum += t["qty"]
        elif (pos_qty > 0) == (q > 0):         # same direction → average in
            tot = abs(pos_qty) + abs(q)
            pos_avg = (pos_avg * abs(pos_qty) + p * abs(q)) / tot
            pos_qty += q
            carried_entry_fee += c
            _entry_price_sum += p * t["qty"]
            _entry_qty_sum += t["qty"]
        else:                                  # opposite → close (fully or partially)
            closed = min(abs(pos_qty), abs(q))
            gross = (p - pos_avg) * (1 if pos_qty > 0 else -1) * closed * point_value
            # Net of: this closing fill's fee share + the carried entry fee share.
            entry_fee_closed = carried_entry_fee * closed / abs(pos_qty)
            close_fee_closed = c * closed / abs(q)
            pairs.append({"time": t["time"], "pnl": gross - entry_fee_closed - close_fee_closed})
            new_qty = pos_qty + q
            if new_qty == 0:
                pos_qty, pos_avg, carried_entry_fee = 0, 0.0, 0.0
            elif (new_qty > 0) == (pos_qty > 0):
                # Partial reduce: fewer contracts at the SAME entry average. The
                # old code re-based avg to the closing fill's price here, mis-
                # realizing every later close of an averaging strategy — fixed
                # identically in trader/lab/runtime.py and robot_runner/runtime.py.
                pos_qty = new_qty
                carried_entry_fee -= entry_fee_closed
                _entry_price_sum += p * abs(new_qty)
                _entry_qty_sum += abs(new_qty)
            else:
                # Flip through zero → the new leg opens fresh at the fill price.
                pos_qty = new_qty
                pos_avg = p
                carried_entry_fee = c * abs(new_qty) / abs(q)
                _entry_price_sum += p * abs(new_qty)
                _entry_qty_sum += abs(new_qty)

    if not pairs:
        return empty

    wins = sum(1 for pr in pairs if pr["pnl"] > 0)
    win_rate = wins / len(pairs)
    net_profit = sum(pr["pnl"] for pr in pairs)
    total_return = net_profit / margin_used

    # Notional = peak_contracts × avg_entry_price × point_value.
    avg_entry_price = (_entry_price_sum / _entry_qty_sum) if _entry_qty_sum else 0.0
    notional = (peak_contracts * avg_entry_price * point_value) if avg_entry_price else 0.0
    return_full = (net_profit / notional) if notional > 0 else None

    # Annualize: compound (1+r)^(365/days) − 1. Require ≥7 days to avoid noise.
    def _annualize(r):
        if r is None or bars_days < 7:
            return None
        # r <= -100% (futures loss beyond initial equity): a negative base to a
        # fractional power is COMPLEX in Python and poisons the whole sweep
        # batch at json.dumps ("Object of type complex is not JSON serializable",
        # killed a 55-min 100k run). Annualizing a wipeout is meaningless — None.
        if r <= -1.0:
            return None
        return (1.0 + r) ** (365.0 / bars_days) - 1.0

    ann_return_go = _annualize(total_return)
    ann_return_full = _annualize(return_full)

    if len(pairs) > 1:
        mean_r = net_profit / len(pairs)
        std_r = math.sqrt(sum((pr["pnl"] - mean_r) ** 2 for pr in pairs) / len(pairs))
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else None
    else:
        sharpe = None

    equity = initial_equity
    peak = equity
    max_dd_money = 0.0
    for pr in pairs:
        equity += pr["pnl"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_money:
            max_dd_money = dd
    max_dd = max_dd_money / margin_used if margin_used else 0.0
    recovery = (net_profit / max_dd_money) if max_dd_money > 0 else None

    return {
        "total_trades": len(pairs),
        "win_rate": win_rate,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "recovery_factor": recovery,
        "net_profit": net_profit,
        "peak_contracts": peak_contracts,
        "margin_used": margin_used,
        "ann_return_go": ann_return_go,
        "ann_return_full": ann_return_full,
        "closed_pairs": pairs,
    }


async def run_single_backtest(
    strategy_module: ModuleType,
    bars: list[Bar],
    symbol: str,
    params: dict,
    initial_equity: float = 100_000.0,
    point_value: float = 1.0,
    initial_margin: float = 0.0,
) -> dict[str, Any]:
    runtime = BacktestRuntime(bars=bars, symbol=symbol,
                              initial_equity=initial_equity, point_value=point_value)

    if hasattr(strategy_module, "on_start"):
        await strategy_module.on_start(runtime, params)

    equity_curve = []
    mtm_peak = runtime._equity          # highest mark-to-market equity seen
    max_dd_mtm = 0.0                    # deepest peak->trough on the mtm curve
    max_mae = 0.0                       # worst open-position adverse excursion (RUB)
    while True:
        await strategy_module.on_bar(runtime, params)
        bar = bars[runtime._cursor]
        pos = runtime._positions.get(symbol, {"side": "flat", "qty": 0, "avg": 0.0})
        signed = pos["qty"] * (1 if pos["side"] == "long"
                               else -1 if pos["side"] == "short" else 0)
        unreal = signed * (bar.close - pos["avg"]) * point_value if signed else 0.0
        mtm = runtime._equity + unreal
        mtm_peak = max(mtm_peak, mtm)
        max_dd_mtm = max(max_dd_mtm, mtm_peak - mtm)
        if unreal < 0:
            max_mae = max(max_mae, -unreal)
        equity_curve.append({"time": bar.time, "equity": runtime._equity})
        if not runtime.advance():
            break

    if hasattr(strategy_module, "on_stop"):
        await strategy_module.on_stop(runtime, params)

    trades = [
        {"side": o.side, "price": o.fill_price or o.price, "qty": o.qty, "time": o.fill_time}
        for o in await runtime.get_orders()
    ]
    bars_days = (bars[-1].time - bars[0].time) / 86400.0 if len(bars) > 1 else 0.0
    metrics = compute_metrics(trades, initial_equity, point_value, symbol=symbol,
                              initial_margin=initial_margin, bars_days=bars_days)
    rf_mtm = (metrics["net_profit"] / max_dd_mtm) if max_dd_mtm > 0 else None

    # Time-sliced IS/OOS + per-window scoring (window_metrics) consumes the SAME
    # closed round-trips compute_metrics already built (net of fees, keyed by
    # exit time) — single source, so window metrics reconcile with net_profit.
    span = (bars[0].time, bars[-1].time)
    wm = window_metrics(metrics["closed_pairs"], span=span, is_frac=0.7, splits=4)
    # v1: whole-run mtm drawdown, not a per-slice OOS dd (later refinement).
    rf_mtm_oos = (wm["net_oos"] / max_dd_mtm) if max_dd_mtm > 0 else None

    # Return the EXACT point_value the engine used, so the chart's P&L (and its
    # commission, which scales with notional=price×point_value) is computed on the
    # same basis as net_profit — the two must never disagree ("единая логика").
    # **metrics spread BEFORE the mtm/MAE keys (not after): compute_metrics only
    # sees discrete CLOSED round-trips, so when there are none (pairs empty) it
    # falls back to `empty`, which now carries these same 3 key names as inert
    # 0.0/0.0/None placeholders (for standalone callers of compute_metrics). The
    # bar loop above is the only place with the full mark-to-market curve, and an
    # open position that never closes is exactly the mirage this feature exists
    # to expose, so its real MAE/mtm-dd/RF-mtm must win, not get stomped back to
    # zero by compute_metrics's placeholder defaults.
    res = {"trades": trades, "equity_curve": equity_curve,
           "point_value": point_value, **metrics,
           "max_mae": max_mae, "max_drawdown_mtm": max_dd_mtm,
           "recovery_factor_mtm": rf_mtm,
           "net_oos": wm["net_oos"], "recovery_factor_mtm_oos": rf_mtm_oos,
           "degrade": wm["degrade"], "windows_profitable": wm["windows_profitable"],
           "windows_total": wm["windows_total"]}
    # closed_pairs is an INTERNAL handoff to window_metrics — pop it so it
    # doesn't bloat every shipped result (a 3-month sweep can have thousands).
    res.pop("closed_pairs", None)
    return res


def _subprocess_run_many(script_code: str, bars_data: list[dict], symbol: str,
                         param_sets: list[dict], result_queue: multiprocessing.Queue,
                         point_value: float = 1.0, initial_margin: float = 0.0,
                         metrics_only: bool = False) -> None:
    """Run MANY param combos in ONE subprocess — bars pickled once, not per combo.
    Runs as a background-priority process and yields the CPU between combos so the
    box stays responsive during a big sweep. metrics_only=True drops the per-combo
    trades + equity_curve arrays IN THIS SUBPROCESS so they never cross back to the
    caller — a sweep over a 3-month 1-min series builds ~50k equity points per combo;
    × hundreds of combos that ballooned the web process to OOM. Sweeps need metrics
    only (leaderboard), so they pass metrics_only=True."""
    import asyncio
    import types

    _demote_to_background()
    validate_script(script_code)
    bars = [Bar(**b) for b in bars_data]
    mod = types.ModuleType("robot_script")
    exec(compile(script_code, "<robot>", "exec"), mod.__dict__)

    async def _run_all():
        out = []
        for i, ps in enumerate(param_sets):
            try:
                r = await run_single_backtest(mod, bars, symbol, ps, point_value=point_value,
                                              initial_margin=initial_margin)
                if metrics_only:
                    r.pop("trades", None)
                    r.pop("equity_curve", None)
                out.append({"ok": True, "params": ps, "result": r})
            except Exception as exc:
                out.append({"ok": False, "params": ps, "error": str(exc)})
            # Briefly yield every few combos so a long grid can't peg a core.
            if (i & 7) == 7:
                await asyncio.sleep(0)
        return out

    try:
        result_queue.put({"ok": True, "results": asyncio.run(_run_all())})
    except Exception as exc:
        result_queue.put({"ok": False, "error": str(exc)})


async def run_backtest_grid(
    script_code: str,
    bars: list[Bar],
    symbol: str,
    param_sets: list[dict],
    timeout: float = 600,
    point_value: float = 1.0,
    initial_margin: float = 0.0,
    metrics_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Run a whole parameter grid in ONE subprocess (bars serialized once).
    Returns list of {ok, params, result|error} in input order.
    """
    bars_data = [
        {"time": b.time, "open": b.open, "high": b.high,
         "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ]
    q: multiprocessing.Queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_subprocess_run_many,
        args=(script_code, bars_data, symbol, param_sets, q, point_value, initial_margin, metrics_only),
        daemon=True,
    )
    proc.start()
    loop = asyncio.get_event_loop()
    payload = await loop.run_in_executor(None, q.get, True, timeout)
    proc.join(timeout=5)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "Unknown grid error"))
    return payload["results"]
