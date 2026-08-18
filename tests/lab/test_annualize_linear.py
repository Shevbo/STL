"""Годовые не должны превращать две недели в обещание 458 концов за год.

18.08.2026 строка bollinger_bo_m1 RIU6 (+26% за 14 дней) уехала в компаньон как
45 897% годовых — сложный процент возвёл двухнедельный результат в 26-ю степень.
Тест пинит линейную форму и сравнимость окон разной длины.
"""
import math

from trader.lab.backtest import compute_metrics


def _round_trip(entry: float, exit_: float, t0: int, t1: int) -> list[dict]:
    return [{"side": "buy", "price": entry, "qty": 1, "time": t0},
            {"side": "sell", "price": exit_, "qty": 1, "time": t1}]


def _ann(days: int, entry: float, exit_: float) -> float:
    t0 = 1_700_000_000
    m = compute_metrics(_round_trip(entry, exit_, t0, t0 + days * 86400),
                        initial_equity=100_000.0, point_value=1.0, symbol="RIU6",
                        bars_days=days, initial_margin=10_000.0)
    return m["ann_return_go"]


def test_short_window_does_not_explode():
    a = _ann(14, 100_000.0, 102_600.0)          # +2600 руб на ГО 10 000 = +26%
    assert a is not None
    assert a < 10, f"годовые {a*100:.0f}% — это сложный процент, а не линейный"
    assert math.isclose(a, 0.26 * 365 / 14, rel_tol=0.05), a


def test_same_rate_on_different_windows_gives_same_annual():
    """Полпроцента в день — это одна и та же годовая на любом окне. У сложной
    формулы такие строки несравнимы между собой, у линейной — сравнимы."""
    short = _ann(20, 100_000.0, 101_000.0)      # +1000 за 20 дней
    long_ = _ann(80, 100_000.0, 104_000.0)      # +4000 за 80 дней, та же скорость
    assert math.isclose(short, long_, rel_tol=0.02), (short, long_)


def test_window_shorter_than_a_week_gives_nothing():
    assert _ann(3, 100_000.0, 101_000.0) is None
