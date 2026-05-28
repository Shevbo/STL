import asyncio
import math
import multiprocessing
from types import ModuleType
from typing import Any

from trader.lab.runtime import BacktestRuntime, Bar


def compute_metrics(trades: list[dict], initial_equity: float) -> dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0,
            "total_return": 0.0, "sharpe": None, "max_drawdown": 0.0,
        }
    pairs = []
    buy_price = None
    for t in trades:
        if t["side"] == "buy":
            buy_price = t["price"]
        elif t["side"] == "sell" and buy_price is not None:
            pairs.append(t["price"] - buy_price)
            buy_price = None

    if not pairs:
        return {
            "total_trades": 0, "win_rate": 0.0,
            "total_return": 0.0, "sharpe": None, "max_drawdown": 0.0,
        }

    wins = sum(1 for p in pairs if p > 0)
    win_rate = wins / len(pairs)
    total_pnl = sum(pairs)
    total_return = total_pnl / initial_equity

    if len(pairs) > 1:
        mean_r = sum(pairs) / len(pairs)
        std_r = math.sqrt(sum((p - mean_r) ** 2 for p in pairs) / len(pairs))
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else None
    else:
        sharpe = None

    equity = initial_equity
    peak = equity
    max_dd = 0.0
    for p in pairs:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": len(pairs),
        "win_rate": win_rate,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
    }


async def run_single_backtest(
    strategy_module: ModuleType,
    bars: list[Bar],
    symbol: str,
    params: dict,
    initial_equity: float = 100_000.0,
) -> dict[str, Any]:
    runtime = BacktestRuntime(bars=bars, symbol=symbol, initial_equity=initial_equity)

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
        {"side": o.side, "price": o.fill_price or o.price, "qty": o.qty}
        for o in await runtime.get_orders()
    ]
    metrics = compute_metrics(trades, initial_equity)
    return {"trades": trades, "equity_curve": equity_curve, **metrics}


def _subprocess_run(script_code: str, bars_data: list[dict], symbol: str,
                    params: dict, result_queue: multiprocessing.Queue) -> None:
    import asyncio
    import types

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
