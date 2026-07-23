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


def test_atr_tail_equals_full_window():
    """ATR по хвосту == ATR по всему окну (Уайлдер забывает старое): защищает
    оптимизацию library.py, которая кормит atr только хвостом bars[-(n*40+1):]."""
    import trader.lab.indicators as I
    hi, lo, cl = [], [], []
    px = 100000.0
    for i in range(3000):                       # окно длиннее любого хвоста
        px += (((i * 37) % 101) - 50) * 3.0
        hi.append(px + 60)
        lo.append(px - 60)
        cl.append(px)
    for n in (5, 14, 20, 40):
        tail = n * 40 + 1
        full = I.atr(hi, lo, cl, n)
        cut = I.atr(hi[-tail:], lo[-tail:], cl[-tail:], n)
        assert full == cut, f"atr(n={n}) хвост {tail} != полное окно: {full} vs {cut}"
