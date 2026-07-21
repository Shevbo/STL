import asyncio

from trader.lab.runtime import Bar
from trader.lab import backtest


# A strategy that longs 1 on the very first tradable bar and holds, so the open
# position rides the full price path. Price dips hard (unrealized hole) then
# recovers to a small gain at the last bar's close. The mirage: closed-trade
# drawdown ~0 (no round-trip ever closes), MAE/mtm-drawdown large.
#
# BacktestRuntime has no buy()/signed_position() helpers (checked
# trader/lab/runtime.py); the real API is place_order(symbol, side, qty, price)
# and reading runtime._positions[symbol] directly. The entry is placed in
# on_start (not on_bar): BacktestRuntime.__init__ seeds _cursor = min(4, len-1)
# for warmup, and place_order always fills at the NEXT bar's open, so on_start
# (called before the bar loop) still fills correctly. Placing it here also
# means the commission-driven equity drop lands BEFORE run_single_backtest
# captures mtm_peak = runtime._equity: if the entry were placed inside on_bar's
# first call instead, that pre-fill equity baseline would outlive the fill and
# permanently inflate max_drawdown_mtm by the (nonzero) entry commission,
# breaking the exact-RUB assertions below.
async def on_start(rt, params):
    await rt.place_order(rt._symbol, "buy", 1, rt._bars[rt._cursor].close)


async def on_bar(rt, params):
    pass


def _bar(t, price):
    return Bar(time=t, open=price, high=price, low=price, close=price, volume=1)


def test_mae_and_mtm_drawdown_expose_open_position_risk():
    # close path: 100 (flat warmup+entry) -> 60 (deep unrealized hole) -> 105
    # (small gain, position still OPEN at the end). 9 bars: BacktestRuntime seeds
    # _cursor=min(4, len-1)=4 and advance() stops once cursor >= len-2, so the
    # last bar (idx 8) is a reserved lookahead slot never visited as "current".
    prices = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 60.0, 105.0, 105.0]
    bars = [_bar(i * 60, p) for i, p in enumerate(prices)]
    res = asyncio.run(backtest.run_single_backtest(
        strategy_module=__import__(__name__, fromlist=["on_bar"]),
        params={}, bars=bars, symbol="TEST", initial_equity=100_000.0,
        point_value=1.0))
    # No round-trip closed -> closed-trade drawdown is ~0 (the mirage).
    assert res["max_drawdown"] <= 1.0
    # But MAE saw the 40-point hole on 1 contract = 40 RUB.
    assert abs(res["max_mae"] - 40.0) < 1e-6
    assert abs(res["max_drawdown_mtm"] - 40.0) < 1e-6
    assert res["recovery_factor_mtm"] is not None
