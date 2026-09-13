"""rich_fool_hold: позиция переживает ночь, утром ставится новая лестница, книги суммируются."""
import asyncio

from tests.lab.test_rich_fool import BASE, DAY, PRIOR, SYM, WD_OPEN, _bar, _day
from trader.lab.runtime import BacktestRuntime
from trader.lab.strategies.rich_fool_hold import on_bar

P = {**BASE, "step_count": 1, "max_contracts": 1, "sl_beyond_pts": 500,
     "tp_arm_pts": 9999, "tp_back_pts": 9999}


def _run(bars):
    async def go():
        rt = BacktestRuntime(bars=PRIOR + bars, symbol=SYM, initial_equity=1_000_000.0)
        while True:
            await on_bar(rt, P)
            if not rt.advance():
                break
        return rt
    return asyncio.run(go())


def test_position_survives_night_and_next_ladder_adds_second_book():
    """День 2: шорт по первой ступени 107.5, цена стоит до вечера. День 3: позицию
    никто не закрывает, новая лестница от 107.5 (amp 9 -> ступень 112) даёт второй шорт."""
    d2, d3 = _day(2), _day(3)
    bars = [_bar(d2, WD_OPEN - 5, 100, 100.1, 99.9, 100)]
    bars += [_bar(d2, WD_OPEN, 100, 108, 100, 107.5)]
    bars += [_bar(d2, WD_OPEN + m, 107.5, 107.6, 107.4, 107.5) for m in range(1, 60)]
    bars += [_bar(d3, WD_OPEN, 107.5, 113, 107.5, 112.8)]
    bars += [_bar(d3, WD_OPEN + m, 112.8, 112.9, 112.7, 112.8) for m in range(1, 10)]
    rt = _run(bars)
    sides = [(o.side, int(o.qty), (o.fill_time or 0) // DAY) for o in rt._orders]
    assert [s for s, _, _ in sides] == ["sell", "sell"], f"ночью закрыли или второй книги нет: {sides}"
    assert sides[0][2] != sides[1][2], sides
    assert rt._state["max_gross"] == 2 and rt._state["max_books"] == 2, rt._state
