from trader.lab.indicators import rsi
from trader.lab.runtime import STLRuntime


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    period = int(params.get("period", 14))
    oversold = float(params.get("oversold", 30.0))
    overbought = float(params.get("overbought", 70.0))

    bars = await stl.get_bars(symbol, tf=5, n=period + 2)
    if len(bars) < period + 1:
        return

    closes = [b.close for b in bars]
    rsi_val = rsi(closes, period)
    pos = await stl.get_position(symbol)

    if rsi_val < oversold and pos.side != "long":
        await stl.place_order(symbol, "buy", 1, bars[-1].close)
        stl.log(f"RSI {rsi_val:.1f} < {oversold} — buy at {bars[-1].close}")

    elif rsi_val > overbought and pos.side == "long":
        await stl.place_order(symbol, "sell", pos.quantity, bars[-1].close)
        stl.log(f"RSI {rsi_val:.1f} > {overbought} — sell at {bars[-1].close}")


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log("RSI Mean Reversion strategy started")


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("RSI Mean Reversion strategy stopped")
