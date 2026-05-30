"""
Donchian Channel Breakout (Turtle Trading rules, long-only variant).

Source/reference:
  https://github.com/nickolasg/turtles-trading
  Classic «System 1» rules from Turtle Traders (Dennis & Eckhardt, 1983).

Logic:
  ENTRY:  Buy when close breaks above the N-bar high (entry_period).
  EXIT:   Sell when close drops below the M-bar low  (exit_period, M < N).

  Long-only for FORTS futures (short requires separate risk approval).

Parameters (params dict):
  symbol        – MOEX FORTS secid, e.g. "RIM6"
  entry_period  – breakout lookback, bars (default 20)
  exit_period   – exit lookback, bars (default 10)
  qty           – contracts per trade (default 1)
"""

from trader.lab.runtime import STLRuntime


async def on_start(stl: STLRuntime, params: dict) -> None:
    ep = params.get("entry_period", 20)
    xp = params.get("exit_period", 10)
    stl.log(f"Donchian Breakout started | entry={ep} exit={xp} symbol={params.get('symbol')}")


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol      = params["symbol"]
    entry_period = int(params.get("entry_period", 20))
    exit_period  = int(params.get("exit_period", 10))
    qty          = int(params.get("qty", 1))

    # Need enough bars for the longest channel
    n_needed = entry_period + 2
    bars = await stl.get_bars(symbol, tf=1, n=n_needed)
    if len(bars) < entry_period + 1:
        return

    # Exclude the current (incomplete) bar from channel calculation
    history = bars[:-1]
    current = bars[-1]

    entry_high = max(b.high for b in history[-entry_period:])
    entry_low  = min(b.low  for b in history[-entry_period:])

    # Exit channel (shorter period) — safe minimum
    xp = min(exit_period, len(history))
    exit_low = min(b.low for b in history[-xp:])

    pos = await stl.get_position(symbol)

    if pos.side == "flat":
        if current.close > entry_high:
            await stl.place_order(symbol, "buy", qty, current.close)
            stl.log(
                f"BUY  close={current.close:.2f}  entry_high={entry_high:.2f}"
            )

    elif pos.side == "long":
        if current.close < exit_low:
            await stl.place_order(symbol, "sell", pos.quantity, current.close)
            stl.log(
                f"SELL close={current.close:.2f}  exit_low={exit_low:.2f}"
            )


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Donchian Breakout stopped")


# ── Metadata used by the settings UI ─────────────────────────────────────────
STRATEGY_META = {
    "name": "Donchian Channel Breakout",
    "description": (
        "Turtle Trading System 1. Buys on N-bar high breakout, "
        "exits on M-bar low breakdown. Long-only, FORTS futures."
    ),
    "source": "https://github.com/nickolasg/turtles-trading",
    "params_schema": [
        {
            "key": "symbol",
            "label": "Инструмент",
            "type": "text",
            "default": "RIM6",
            "hint": "Тикер FORTS: RIM6, SIM6, GZM6 и т.д.",
        },
        {
            "key": "entry_period",
            "label": "Период входа (N)",
            "type": "number",
            "default": 20,
            "min": 5,
            "max": 200,
            "hint": "Breakout: покупка при пробое максимума за N баров",
        },
        {
            "key": "exit_period",
            "label": "Период выхода (M)",
            "type": "number",
            "default": 10,
            "min": 2,
            "max": 100,
            "hint": "Exit: продажа при пробое минимума за M баров (M < N)",
        },
        {
            "key": "qty",
            "label": "Количество контрактов",
            "type": "number",
            "default": 1,
            "min": 1,
            "max": 10,
            "hint": "Лотность на одну сделку",
        },
    ],
}
