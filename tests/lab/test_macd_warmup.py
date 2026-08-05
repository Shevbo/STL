"""Окно прогрева macd_cross должно быть длиннее самой длинной EMA, иначе знак залипает.

Баг: прогрев считался как `slow + signal + 2`. У живого робота
lxk22tsffsxiiotb8kmpsato (RIU6, fast=57, slow=48, signal=10) это давало окно 60 —
короче самой длинной EMA(57). Она не успевала разойтись с сигнальной линией,
`sig_macd` возвращал +1 НАВСЕГДА, и робот за 6 суток реала сделал 778 закрытий
лонга и ноль шортов, торгуя вместо MACD односторонней усредняющей машиной.
Замер на RIU6 M1 за 01.06-31.07.2026: окно 60 -> 0 переворотов, окно 120 -> 1453.
"""
import math
from types import SimpleNamespace

from trader.lab.strategies.library import REGISTRY

LIVE = {"fast": 57, "slow": 48, "signal": 10, "qty": 2}


def _wave(n: int, period: int = 400) -> list:
    """Синусоида: знак MACD ОБЯЗАН перевернуться хотя бы раз за полный период."""
    return [SimpleNamespace(close=90000 + 500 * math.sin(2 * math.pi * i / period))
            for i in range(n)]


def test_macd_cross_window_outlives_longest_ema():
    spec = REGISTRY["macd_cross"]
    need = spec["warmup"](LIVE)
    assert need > max(LIVE["fast"], LIVE["slow"]) * 2, (
        f"окно {need} не длиннее самой длинной EMA — знак MACD залипнет")


def test_macd_cross_signal_flips_within_its_own_window():
    spec = REGISTRY["macd_cross"]
    need = spec["warmup"](LIVE)
    bars = _wave(need + 400)
    # Ровно то, что делает make_on_bar: signal() на последних `need` барах.
    seen = {spec["signal"](bars[i - need:i], LIVE) for i in range(need, len(bars))}
    assert seen == {1, -1}, f"сигнал не переворачивается на своём окне: {seen}"
