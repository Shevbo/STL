"""ATR без выходных: значение продолжается с пятницы (заказ оператора 21.09.2026).

Что охраняем:
  1. включённый параметр даёт ATR, равный расчёту ПО БУДНЯМ, а выключенный — по всем
     барам; разница на реальном рынке кратная (выходные почти не двигаются);
  2. параметр НЕ запрещает торговлю в выходные — это дело отдельного skip_weekend;
  3. если будней в окне меньше, чем нужно ATR, считаем как раньше (не падаем и не
     возвращаем нуль: робот без ATR не умеет ни тейка, ни усреднения).
"""
import asyncio
import types
from datetime import datetime, timezone

from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
# 2026-09-18 — пятница, 19-20 — выходные, 21 — понедельник.
FRI = int(datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc).timestamp())
BASE = {"symbol": SYM, "qty": 1, "fast": 3, "slow": 6, "signal": 3,
        "tp_atr": 80, "avg_atr_n": 14, "avg_max": 1}


def _bars() -> list[Bar]:
    """Будни с размахом 100 пт, выходные с размахом 5 пт."""
    out, t, px = [], FRI, 80000.0
    for i in range(600):
        wd = datetime.fromtimestamp(t, tz=timezone.utc).weekday()
        rng = 5.0 if wd >= 5 else 100.0
        out.append(Bar(t, px, px + rng, px - rng, px, 10))
        t += 60
        if i == 299:                     # прыжок в воскресенье, чтобы выходные попали в окно
            t = FRI + 2 * 86400
    return out


def test_weekend_bars_leave_the_atr_window():
    """ATR по будням (размах 100) много больше ATR по смеси с выходными (размах 5)."""
    from trader.lab import indicators as I
    bars = _bars()
    tail = bars[-(14 * 40 + 1):]
    work = [b for b in tail if datetime.fromtimestamp(b.time, tz=timezone.utc).weekday() < 5]
    all_atr = I.atr([b.high for b in tail], [b.low for b in tail],
                    [b.close for b in tail], 14)
    work_atr = I.atr([b.high for b in work], [b.low for b in work],
                     [b.close for b in work], 14)
    assert work_atr > all_atr * 1.5, (work_atr, all_atr)


def test_trading_on_weekend_is_not_blocked():
    """Параметр меняет только окно ATR: запрет входов — дело skip_weekend."""
    bars = _bars()
    weekend = [b for b in bars
               if datetime.fromtimestamp(b.time, tz=timezone.utc).weekday() >= 5]
    assert weekend, "в выборке нет выходных баров"
    orders = []
    for params in ({**BASE, "atr_skip_weekend": 1}, {**BASE, "atr_skip_weekend": 0}):
        rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
        mod = types.ModuleType("m")
        mod.on_bar = make_on_bar("macd_cross")
        while True:
            asyncio.run(mod.on_bar(rt, params))
            if not rt.advance():
                break
        orders.append(len(rt._orders))
    assert orders[0] > 0 and orders[1] > 0
