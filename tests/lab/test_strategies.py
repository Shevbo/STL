import pytest
from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.ema_crossover import on_bar as ema_on_bar
from trader.lab.strategies.rsi_mean_reversion import on_bar as rsi_on_bar


def make_runtime(prices: list[float]) -> BacktestRuntime:
    bars = [Bar(time=i*60, open=p, high=p+0.5, low=p-0.5, close=p, volume=1000)
            for i, p in enumerate(prices)]
    return BacktestRuntime(bars=bars, symbol="SIM6", initial_equity=100_000.0)


@pytest.mark.asyncio
async def test_ema_crossover_buys_on_uptrend():
    prices = [100.0] * 30 + [100.0 + i * 0.5 for i in range(30)]
    rt = make_runtime(prices)
    params = {"symbol": "SIM6", "fast_period": 5, "slow_period": 20}
    rt._cursor = 25
    for _ in range(20):
        await ema_on_bar(rt, params)
        rt.advance()
    orders = await rt.get_orders()
    buys = [o for o in orders if o.side == "buy"]
    assert len(buys) >= 1


@pytest.mark.asyncio
async def test_rsi_buys_on_oversold():
    prices = [100.0 - i * 0.8 for i in range(30)]
    rt = make_runtime(prices)
    params = {"symbol": "SIM6", "period": 14, "oversold": 30, "overbought": 70}
    rt._cursor = 20
    for _ in range(5):
        await rsi_on_bar(rt, params)
        rt.advance()
    orders = await rt.get_orders()
    buys = [o for o in orders if o.side == "buy"]
    assert len(buys) >= 1
