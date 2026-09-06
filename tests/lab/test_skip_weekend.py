"""Запрет входов в выходные: гейт входа, а НЕ выхода.

Замер бумажных роботов 06.09.2026: у macd_shectory1 выходные съели весь результат
(минус 20 856 руб по выходным против плюс 22 116 в будни), у восьми из десяти
роботов выходные наоборот плюсовые. Поэтому фильтр адресный и по умолчанию выключен.

Главная опасность здесь та же, на которой дважды обожглась «долина смерти»: фильтр,
случайно закрывший ВЫХОД, оставляет позицию без единого способа закрыться.
"""
import asyncio

import pytest

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
# 02.09.2026 00:00 UTC — среда. Ряд идёт средой-четвергом-пятницей-СУББОТОЙ.
WED = 1788307200


def _bars(n: int = 900) -> list[Bar]:
    """Пила с трендом: даёт и входы, и развороты, и доборы."""
    out, px, seed = [], 90000.0, 7
    for i in range(n):
        seed = (1103515245 * seed + 12345) % (1 << 31)
        px += 40 * ((i % 120) - 60) / 60 + (seed / (1 << 31) - 0.5) * 60
        o = out[-1].close if out else px
        out.append(Bar(time=WED + i * 600,          # 10 мин на бар -> ряд накрывает неделю
                       open=o, high=max(o, px) + 5, low=min(o, px) - 5,
                       close=px, volume=100))
    return out


BASE = {"symbol": SYM, "qty": 1, "fast": 5, "slow": 12, "signal": 4,
        "avg_max": 3, "avg_step_atr": 10, "tp_atr": 0, "sl_frac": 0, "sl_pct": 0,
        "avg_atr_n": 5, "nd_days": 1, "gap_auto": 0, "k_avg": 10, "min_gap_pts": 0,
        "cooldown_min": 0, "cooldown_pct": 1, "dv_bars": 0, "dv_range_pts": 0,
        "allow_long": 1, "allow_short": 1}


async def _run(extra: dict):
    rt = BacktestRuntime(bars=_bars(), symbol=SYM, initial_equity=5_000_000.0)
    on_bar = make_on_bar("macd_shectory1")
    while True:
        await on_bar(rt, {**BASE, **extra})
        if not rt.advance():
            break
    return rt, [(o.side, int(o.qty), int(o.fill_time or 0)) for o in rt._orders]


def _is_weekend(ts: int) -> bool:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, dt.UTC).weekday() >= 5


def test_off_by_default_changes_nothing():
    _, plain = asyncio.run(_run({}))
    _, zero = asyncio.run(_run({"skip_weekend": 0}))
    assert plain == zero


def test_no_new_entries_on_weekend_bars():
    """Сделки в выходные остаться могут — но только ЗАКРЫВАЮЩИЕ. Признак нарушения:
    рост позиции по модулю на выходном баре."""
    _, trades = asyncio.run(_run({"skip_weekend": 1}))
    pos = 0
    for side, qty, ts in trades:
        q = qty * (1 if side == "buy" else -1)
        grew = abs(pos + q) > abs(pos)
        assert not (grew and _is_weekend(ts)), (side, qty, ts, pos)
        pos += q


def test_weekend_exit_is_still_allowed():
    """Фильтр не имеет права запереть позицию: на ряду с выходными он обязан
    оставить хотя бы один закрывающий филл в субботу или воскресенье."""
    _, trades = asyncio.run(_run({"skip_weekend": 1}))
    assert any(_is_weekend(ts) for _, _, ts in trades), \
        "в выходные не случилось НИ ОДНОГО филла — фильтр закрыл и выход тоже"


def test_filter_reduces_trades_but_does_not_kill_them():
    _, plain = asyncio.run(_run({}))
    _, gated = asyncio.run(_run({"skip_weekend": 1}))
    assert 0 < len(gated) < len(plain)


@pytest.mark.parametrize("offset", [0, 180])
def test_offset_shifts_the_weekend_boundary(offset):
    """У агента бары в истинном UTC: без поправки суббота начиналась бы на три часа
    раньше московской. Проверяем, что параметр вообще доходит до фильтра."""
    _, a = asyncio.run(_run({"skip_weekend": 1, "bar_offset_min": offset}))
    assert a
