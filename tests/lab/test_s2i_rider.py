"""Райдер «пропустить N сигналов после крупной сделки» (signals2ignor_*).

Заказ оператора 17.08.2026. Проверяем поведение по ФАКТУ сделок робота на
синтетическом ряду, а не наличие ветки в коде.

Три вещи, каждая из которых уже стоила денег в соседних фильтрах этого файла:

1. ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ, БЕЗУСЛОВНО. Ключи входят в AVG_PARAMS, то есть в
   КАЖДУЮ стратегию реестра, по которому посчитаны миллионы строк лидерборда.
2. ФИЛЬТР ГЕЙТИТ ВХОД, А НЕ ВЫХОД. Тот же урок, что с «долиной смерти»: фильтр,
   заморозивший выход, оставил живой шорт на 290 пунктов без единого способа
   закрыться.
3. СЧЁТЧИК ТРАТИТСЯ ЗА СИГНАЛ, А НЕ ЗА БАР. Оператор просил пропустить N
   сигналов; списание за бар превратило бы фильтр в паузу на N минут.
"""
import math

import pytest

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
DAY0 = 1786579200

# Ряд НЕПЕРИОДИЧЕСКИЙ намеренно. На чистой пиле фильтр проверить нельзя: там
# пропущенный вход возвращается следующим циклом один в один, и последовательность
# сделок совпадает с базовой, хотя счётчик пропусков честно тикает. Волна плюс
# воспроизводимый шум ломает это совпадение, оставаясь детерминированной.
BASE = {"symbol": SYM, "qty": 1, "fast": 3, "slow": 8, "signal": 3,
        "avg_max": 1, "avg_step_atr": 0, "tp_atr": 0, "sl_frac": 0, "sl_pct": 0,
        "avg_atr_n": 5, "nd_days": 1, "gap_auto": 0, "k_avg": 10,
        "min_gap_pts": 0, "cooldown_min": 0, "cooldown_pct": 1,
        "dv_bars": 0, "dv_range_pts": 0, "allow_long": 1, "allow_short": 1}


def _bars(n=1200):
    out, px, s = [], 90000.0, 12345
    for i in range(n):
        s = (1103515245 * s + 12345) % (1 << 31)      # LCG: без random, воспроизводимо
        px += (900 * math.sin(2 * math.pi * i / 140)
               - 900 * math.sin(2 * math.pi * (i - 1) / 140)
               + (s / (1 << 31) - 0.5) * 90)
        o = out[-1].close if out else px
        out.append(Bar(time=DAY0 + i * 60, open=o, high=max(o, px) + 8,
                       low=min(o, px) - 8, close=px, volume=100))
    return out


async def _run(params: dict) -> list[tuple[str, int]]:
    """Прогон. Возвращает список сделок (сторона, объём)."""
    rt = BacktestRuntime(bars=_bars(), symbol=SYM, initial_equity=5_000_000.0)
    on_bar = make_on_bar("macd_shectory1")
    while True:
        await on_bar(rt, params)
        if not rt.advance():
            break
    return [(o.side, int(o.qty)) for o in rt._orders]


@pytest.mark.asyncio
async def test_disabled_by_default_is_a_no_op():
    """Ключи есть у каждой стратегии реестра — их появление не должно двигать
    НИ ОДНУ существующую строку лидерборда."""
    plain = await _run(dict(BASE))
    zeroed = await _run(dict(BASE, signals2ignor_win=0, signals2ignor_lose=0,
                             signals2ignor_value=0))
    assert plain == zeroed
    # Планка без счётчиков и счётчики без планки — тоже выключено.
    assert await _run(dict(BASE, signals2ignor_value=300)) == plain
    assert await _run(dict(BASE, signals2ignor_win=3, signals2ignor_lose=3)) == plain


@pytest.mark.asyncio
async def test_skipping_reduces_the_number_of_entries():
    on = await _run(dict(BASE, signals2ignor_win=2, signals2ignor_lose=2,
                         signals2ignor_value=100))
    off = await _run(dict(BASE))
    assert len(on) < len(off), "фильтр не отсеял ни одного входа"


@pytest.mark.asyncio
async def test_win_and_lose_counters_are_independent():
    """Пауза после победы и пауза после потери — разные гипотезы; если бы код
    читал один счётчик, эти два прогона совпали бы."""
    after_win = await _run(dict(BASE, signals2ignor_win=3, signals2ignor_lose=0,
                                signals2ignor_value=100))
    after_lose = await _run(dict(BASE, signals2ignor_win=0, signals2ignor_lose=3,
                                 signals2ignor_value=100))
    assert after_win != after_lose


@pytest.mark.asyncio
async def test_high_bar_never_arms_the_filter():
    """Планка выше любого хода ряда = ни одна сделка не «крупная» = поведение базы."""
    huge = await _run(dict(BASE, signals2ignor_win=5, signals2ignor_lose=5,
                           signals2ignor_value=10_000_000))
    assert huge == await _run(dict(BASE))


@pytest.mark.asyncio
async def test_filter_never_holds_a_position():
    """ГЛАВНОЕ. Фильтр не пускает ВХОД, но выход обязан исполняться всегда.

    Признак нарушения — незакрытый хвост: если бы фильтр заморозил выход, робот
    остался бы в позиции, и число покупок разошлось бы с числом продаж больше,
    чем на одну открытую позицию.
    """
    trades = await _run(dict(BASE, signals2ignor_win=4, signals2ignor_lose=4,
                             signals2ignor_value=50))
    bought = sum(q for s, q in trades if s == "buy")
    sold = sum(q for s, q in trades if s == "sell")
    assert abs(bought - sold) <= 1, (
        f"позиция зависла: куплено {bought}, продано {sold} — фильтр держит выход")
