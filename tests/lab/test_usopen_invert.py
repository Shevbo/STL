"""Инверсия us_open_fvg: тот же сигнал, сделка в другую сторону.

Модуль самостоятельный и через make_on_bar не проходит, поэтому суффикс `__inv`
на него не действует — инверсия своя, параметром. Проверяем ФАКТ сделок робота на
синтетическом дне, а не наличие ветки в коде.

Что должно быть верно и что легко сломать:
  1. Сторона переворачивается.
  2. Стоп и тейк переворачиваются ВМЕСТЕ со стороной (иначе шорт получил бы стоп
     под диапазоном, то есть в сторону прибыли, и не закрылся бы никогда).
  3. Гейт сторон считается по ФАКТИЧЕСКОЙ стороне: запрет шорта обязан гасить
     инвертированный вход по сигналу лонга.
  4. Выключенная инверсия не меняет НИ ОДНОЙ сделки.
"""
import asyncio

import pytest

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.us_open_fvg import on_bar

SYM = "RIU6"
DAY = 1788307200                      # среда 02.09.2026 00:00 UTC (= МСК-стенка)
OPEN_HM = 16 * 60 + 30                # 16:30, открытие США летом


def _bar(minute: int, o, h, low, c) -> Bar:
    return Bar(time=DAY + minute * 60, open=o, high=h, low=low, close=c, volume=100)


def _day_bars() -> list[Bar]:
    """День с чистым пробоем ВВЕРХ после опорной свечи 16:30-16:34."""
    bars = [_bar(m, 100.0, 100.2, 99.8, 100.0) for m in range(OPEN_HM - 30, OPEN_HM)]
    # опорная свеча: диапазон 99.5 .. 100.5
    for m in range(OPEN_HM, OPEN_HM + 5):
        bars.append(_bar(m, 100.0, 100.5, 99.5, 100.0))
    # пробой вверх с телом и FVG: low[-1] > high[-3]
    bars.append(_bar(OPEN_HM + 5, 100.4, 100.6, 100.3, 100.5))
    bars.append(_bar(OPEN_HM + 6, 100.6, 101.4, 100.6, 101.3))
    bars.append(_bar(OPEN_HM + 7, 101.3, 102.0, 101.2, 101.9))
    # дальше цена ползёт вверх — лонг идёт в прибыль, шорт в стоп
    for i, m in enumerate(range(OPEN_HM + 8, OPEN_HM + 40)):
        px = 102.0 + i * 0.2
        bars.append(_bar(m, px, px + 0.3, px - 0.2, px))
    return bars


async def _run(extra: dict) -> list[tuple[str, int, float]]:
    rt = BacktestRuntime(bars=_day_bars(), symbol=SYM, initial_equity=1_000_000.0)
    params = {"symbol": SYM, "qty": 1, "entry_mode": 0, "req_fvg": 1, "min_frac": 5,
              "range_min": 5, "signal_min": 60, "rr_x10": 20, "stop_pct": 0,
              "open_hour": 16, "open_min": 30, **extra}
    while True:
        await on_bar(rt, params)
        if not rt.advance():
            break
    return [(o.side, int(o.qty), float(o.price)) for o in rt._orders]


def test_signal_long_becomes_a_short_when_inverted():
    plain = asyncio.run(_run({}))
    inv = asyncio.run(_run({"invert": 1}))
    assert plain and inv, (plain, inv)
    assert plain[0][0] == "buy", plain
    assert inv[0][0] == "sell", inv
    assert plain[0][2] == inv[0][2], "вход в одной и той же точке, меняется только сторона"


def test_inverted_short_stops_out_above_the_range_not_below():
    """Цена после пробоя идёт вверх. Инвертированный шорт обязан закрыться стопом
    ВЫШЕ входа: если бы стоп остался геометрией лонга, выхода не случилось бы."""
    inv = asyncio.run(_run({"invert": 1}))
    assert len(inv) >= 2, inv
    entry, exit_ = inv[0], inv[1]
    assert entry[0] == "sell" and exit_[0] == "buy"
    assert exit_[2] > entry[2], (entry, exit_)


def test_side_gate_applies_to_the_actual_side():
    """Запрет шорта гасит инвертированный вход по сигналу ЛОНГА."""
    assert asyncio.run(_run({"invert": 1, "allow_short": 0})) == []
    # а запрет лонга ему не мешает: робот идёт в шорт
    assert asyncio.run(_run({"invert": 1, "allow_long": 0}))


def test_invert_off_changes_nothing():
    assert asyncio.run(_run({})) == asyncio.run(_run({"invert": 0}))


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_no_crash_in_every_entry_mode(mode):
    asyncio.run(_run({"invert": 1, "entry_mode": mode}))
