"""
SuperTrend (ATR trend-following), long + short.

Source/reference:
  https://github.com/jigneshpylab/ZerodhaPythonScripts (supertrend.py)
  Classic TradingView "SuperTrend" indicator (Olivier Seban), widely used on MOEX futures.

Logic:
  band basis  = (high + low) / 2
  upper = basis + mult * ATR(period)
  lower = basis - mult * ATR(period)
  Trend flips to UP   when close crosses above the prior upper band.
  Trend flips to DOWN when close crosses below the prior lower band.
  Position follows trend: long in uptrend, short in downtrend (always in market once warmed up).

Parameters (params dict):
  symbol      – FORTS secid, e.g. "RIM6"
  atr_period  – ATR lookback, bars (default 10)
  multiplier  – band width = multiplier * ATR (default 3; stored x10 as int for grid)
  qty         – contracts per trade (default 1)

Note: multiplier is given as an integer tenths (e.g. 30 = 3.0) so the optimizer
grid can sweep it with integer from/to/step. mult_real = multiplier / 10.
"""

from trader.lab.indicators import atr
from trader.lab.runtime import STLRuntime


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"SuperTrend started | atr={params.get('atr_period')} mult={params.get('multiplier')}/10 symbol={params.get('symbol')}")


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    atr_period = int(params.get("atr_period", 10))
    mult = float(params.get("multiplier", 30)) / 10.0
    qty = int(params.get("qty", 1))

    need = atr_period + 3
    bars = await stl.get_bars(symbol, tf=1, n=need)
    if len(bars) < atr_period + 2:
        return

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]

    # ATR on the two most recent windows to detect a band cross between prev and cur bar.
    a_cur = atr(highs, lows, closes, atr_period)
    a_prev = atr(highs[:-1], lows[:-1], closes[:-1], atr_period)

    basis_cur = (highs[-1] + lows[-1]) / 2.0
    basis_prev = (highs[-2] + lows[-2]) / 2.0
    upper_prev = basis_prev + mult * a_prev
    lower_prev = basis_prev - mult * a_prev

    cur_close = closes[-1]

    # Persisted trend direction: +1 up, -1 down, 0 not set yet.
    # Enter ONLY on an actual trend change, then hold until it reverses —
    # otherwise the band test fires every bar and the position keeps growing.
    trend = int(stl.get_state("trend", 0))

    new_trend = trend
    if cur_close > upper_prev:
        new_trend = 1
    elif cur_close < lower_prev:
        new_trend = -1

    # Publish the next planned operation: the price level at which the trend would
    # flip and the robot would act. Drawn as a dotted line in the robot window.
    #   holding long  → flips SHORT if price drops below the lower band → planned sell
    #   holding short → flips LONG  if price rises above the upper band → planned buy
    #   flat/unknown  → both bands are candidate triggers
    plan: list = []
    if trend == 1:
        plan.append({"side": "sell", "price": round(lower_prev), "qty": qty,
                     "reason": "флип в шорт при пробое нижней полосы"})
    elif trend == -1:
        plan.append({"side": "buy", "price": round(upper_prev), "qty": qty,
                     "reason": "флип в лонг при пробое верхней полосы"})
    else:
        plan.append({"side": "buy", "price": round(upper_prev), "qty": qty,
                     "reason": "вход в лонг при пробое верхней полосы"})
        plan.append({"side": "sell", "price": round(lower_prev), "qty": qty,
                     "reason": "вход в шорт при пробое нижней полосы"})
    stl.set_state("plan", plan)

    if new_trend == trend:
        return  # trend unchanged → hold, no new orders

    pos = await stl.get_position(symbol)

    if new_trend == 1:
        if pos.side == "short":
            await stl.place_order(symbol, "buy", pos.quantity, cur_close)   # cover short
        await stl.place_order(symbol, "buy", qty, cur_close)                # open long
        stl.log(f"ST flip UP — long @ {cur_close:.0f} (upper={upper_prev:.0f})")
    elif new_trend == -1:
        if pos.side == "long":
            await stl.place_order(symbol, "sell", pos.quantity, cur_close)  # close long
        await stl.place_order(symbol, "sell", qty, cur_close)               # open short
        stl.log(f"ST flip DOWN — short @ {cur_close:.0f} (lower={lower_prev:.0f})")

    stl.set_state("trend", new_trend)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("SuperTrend stopped")


STRATEGY_META = {
    "name": "SuperTrend (ATR)",
    "description": (
        "Трендследящая по полосам ATR. Лонг при пробое верхней полосы вверх, "
        "шорт при пробое нижней вниз. Торгует в обе стороны. FORTS."
    ),
    "source": "https://github.com/jigneshpylab/ZerodhaPythonScripts",
    "params_schema": [
        {"key": "symbol",     "label": "Инструмент",       "type": "text",   "default": "RIM6", "hint": "FORTS тикер"},
        {"key": "atr_period", "label": "Период ATR",       "type": "number", "default": 10, "min": 5,  "max": 50,  "hint": "Окно расчёта ATR"},
        {"key": "multiplier", "label": "Множитель ×10",    "type": "number", "default": 30, "min": 10, "max": 60,  "hint": "Ширина полос = (множитель/10) × ATR. 30 = 3.0"},
        {"key": "qty",        "label": "Контрактов",       "type": "number", "default": 1,  "min": 1,  "max": 10,  "hint": "Лотность на сделку"},
    ],
}
