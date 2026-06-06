import asyncio
import math
import multiprocessing
import os
from types import ModuleType
from typing import Any

from trader.lab.commission import commission_for
from trader.lab.runtime import BacktestRuntime, Bar


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
                    initial_margin: float = 0.0) -> dict[str, Any]:
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
    """
    empty = {"total_trades": 0, "win_rate": 0.0, "total_return": 0.0,
             "sharpe": None, "max_drawdown": 0.0, "recovery_factor": None,
             "net_profit": 0.0, "peak_contracts": 0, "margin_used": 0.0}
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

    pairs = []          # realized PnL per closed round-trip, in RUB, NET of fees
    entry = None        # (signed_qty, price, fee_carried) — entry commission carried to the close
    for t in trades:
        q = t["qty"] * (1 if t["side"] == "buy" else -1)
        c = commission_for(symbol, t["price"], t["qty"], point_value, taker=True)
        if entry is None:
            entry = [q, t["price"], c]            # this opening fill's fee
            continue
        eq, ep, fee = entry
        if (eq > 0) == (q > 0):          # same direction → average in
            tot = eq + q
            ep = (ep * eq + t["price"] * q) / tot if tot != 0 else t["price"]
            entry = [tot, ep, fee + c]            # averaging fill adds a fee
        else:                            # opposite → close
            closed = min(abs(eq), abs(q))
            gross = (t["price"] - ep) * (1 if eq > 0 else -1) * closed * point_value
            # Net of: this closing fill's fee + the carried entry/averaging fees.
            pairs.append(gross - c - fee)
            rem = eq + q
            # Leftover (reverse/partial) carries one fee for its remaining entry.
            entry = [rem, t["price"], c] if rem != 0 else None

    if not pairs:
        return empty

    wins = sum(1 for p in pairs if p > 0)
    win_rate = wins / len(pairs)
    net_profit = sum(pairs)
    total_return = net_profit / margin_used

    if len(pairs) > 1:
        mean_r = net_profit / len(pairs)
        std_r = math.sqrt(sum((p - mean_r) ** 2 for p in pairs) / len(pairs))
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else None
    else:
        sharpe = None

    equity = initial_equity
    peak = equity
    max_dd_money = 0.0
    for p in pairs:
        equity += p
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
    while True:
        await strategy_module.on_bar(runtime, params)
        bar = bars[runtime._cursor]
        equity_curve.append({"time": bar.time, "equity": runtime._equity})
        if not runtime.advance():
            break

    if hasattr(strategy_module, "on_stop"):
        await strategy_module.on_stop(runtime, params)

    trades = [
        {"side": o.side, "price": o.fill_price or o.price, "qty": o.qty, "time": o.fill_time}
        for o in await runtime.get_orders()
    ]
    metrics = compute_metrics(trades, initial_equity, point_value, symbol=symbol,
                              initial_margin=initial_margin)
    return {"trades": trades, "equity_curve": equity_curve, **metrics}


def _subprocess_run(script_code: str, bars_data: list[dict], symbol: str,
                    params: dict, result_queue: multiprocessing.Queue) -> None:
    import asyncio
    import types

    _demote_to_background()
    bars = [Bar(**b) for b in bars_data]
    mod = types.ModuleType("robot_script")
    exec(compile(script_code, "<robot>", "exec"), mod.__dict__)

    async def _run():
        return await run_single_backtest(mod, bars, symbol, params)

    try:
        result = asyncio.run(_run())
        result_queue.put({"ok": True, "result": result})
    except Exception as exc:
        result_queue.put({"ok": False, "error": str(exc)})


async def run_backtest_isolated(
    script_code: str,
    bars: list[Bar],
    symbol: str,
    params: dict,
) -> dict[str, Any]:
    bars_data = [
        {"time": b.time, "open": b.open, "high": b.high,
         "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ]
    q: multiprocessing.Queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_subprocess_run,
        args=(script_code, bars_data, symbol, params, q),
        daemon=True,
    )
    proc.start()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, q.get, True, 120)
    proc.join(timeout=5)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Unknown backtest error"))
    return result["result"]


def _subprocess_run_many(script_code: str, bars_data: list[dict], symbol: str,
                         param_sets: list[dict], result_queue: multiprocessing.Queue,
                         point_value: float = 1.0, initial_margin: float = 0.0) -> None:
    """Run MANY param combos in ONE subprocess — bars pickled once, not per combo.
    Runs as a background-priority process and yields the CPU between combos so the
    box stays responsive during a big sweep."""
    import asyncio
    import types

    _demote_to_background()
    bars = [Bar(**b) for b in bars_data]
    mod = types.ModuleType("robot_script")
    exec(compile(script_code, "<robot>", "exec"), mod.__dict__)

    async def _run_all():
        out = []
        for i, ps in enumerate(param_sets):
            try:
                r = await run_single_backtest(mod, bars, symbol, ps, point_value=point_value,
                                              initial_margin=initial_margin)
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
        args=(script_code, bars_data, symbol, param_sets, q, point_value, initial_margin),
        daemon=True,
    )
    proc.start()
    loop = asyncio.get_event_loop()
    payload = await loop.run_in_executor(None, q.get, True, timeout)
    proc.join(timeout=5)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "Unknown grid error"))
    return payload["results"]
