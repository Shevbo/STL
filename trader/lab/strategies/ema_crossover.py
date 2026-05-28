from trader.lab.indicators import ema
from trader.lab.runtime import STLRuntime


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    fast_period = int(params.get("fast_period", 9))
    slow_period = int(params.get("slow_period", 21))

    bars = await stl.get_bars(symbol, tf=5, n=slow_period + 2)
    if len(bars) < slow_period + 1:
        return

    closes = [b.close for b in bars]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)

    pos = await stl.get_position(symbol)

    if fast[-1] > slow[-1] and fast[-2] <= slow[-2] and pos.side != "long":
        await stl.place_order(symbol, "buy", 1, bars[-1].close)
        stl.log(f"EMA cross UP — buy at {bars[-1].close}")

    elif fast[-1] < slow[-1] and fast[-2] >= slow[-2] and pos.side == "long":
        await stl.place_order(symbol, "sell", pos.quantity, bars[-1].close)
        stl.log(f"EMA cross DOWN — sell at {bars[-1].close}")


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log("EMA Crossover strategy started")


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("EMA Crossover strategy stopped")
