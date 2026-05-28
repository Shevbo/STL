import pytest
from trader.lab.backtest import run_single_backtest, compute_metrics
from trader.lab.runtime import Bar


def make_bars(n=100, trend="up") -> list[Bar]:
    prices = []
    p = 100.0
    for i in range(n):
        p += 0.3 if trend == "up" else -0.3
        prices.append(p)
    return [Bar(time=i*60, open=p-0.1, high=p+0.5, low=p-0.5, close=p, volume=1000)
            for i, p in enumerate(prices)]


def test_compute_metrics_winning():
    trades = [
        {"side": "buy", "price": 100.0, "qty": 1},
        {"side": "sell", "price": 105.0, "qty": 1},
        {"side": "buy", "price": 103.0, "qty": 1},
        {"side": "sell", "price": 108.0, "qty": 1},
    ]
    metrics = compute_metrics(trades, initial_equity=100_000.0)
    assert metrics["total_trades"] == 2
    assert metrics["win_rate"] == pytest.approx(1.0)
    assert metrics["total_return"] > 0


def test_compute_metrics_empty():
    metrics = compute_metrics([], initial_equity=100_000.0)
    assert metrics["total_trades"] == 0
    assert metrics["total_return"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_run_single_backtest_ema():
    from trader.lab.strategies import ema_crossover
    bars = make_bars(200, trend="up")
    result = await run_single_backtest(
        strategy_module=ema_crossover,
        bars=bars,
        symbol="TESTSYMBOL",
        params={"symbol": "TESTSYMBOL", "fast_period": 5, "slow_period": 20},
        initial_equity=100_000.0,
    )
    assert "trades" in result
    assert "equity_curve" in result
    assert "sharpe" in result
