import pytest
from trader.lab.indicators import ema, rsi


def test_ema_basic():
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = ema(prices, period=3)
    assert len(result) == len(prices)
    assert result[-1] == pytest.approx(4.0, rel=1e-3)


def test_ema_requires_enough_data():
    with pytest.raises(ValueError, match="period"):
        ema([1.0, 2.0], period=5)


def test_rsi_overbought():
    prices = [float(i) for i in range(16)]
    result = rsi(prices, period=14)
    assert result > 90.0


def test_rsi_oversold():
    prices = [float(16 - i) for i in range(16)]
    result = rsi(prices, period=14)
    assert result < 10.0
