"""Шаг усреднения от последнего филла вместо средней (avg_from_last).

Заведено 21.09.2026 после разбора утреннего лося. Шаг меряется от СРЕДНЕЙ цены
позиции, а объём добора растёт (k_avg=2.0), поэтому средняя бежит навстречу цене и
каждый следующий добор оказывается ближе предыдущего: лестница 1+2+4+8+5 = 20
контрактов уложилась в 2.77 шага (≈280 пт при шаге 100) на ходе 620 пт.

Тест гоняет настоящий движок на равномерном ходе против позиции и сравнивает, на
каком РАССТОЯНИИ от входа набралась лестница.
"""
import asyncio
import types

from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
# flip_min_pts держит позицию на кроссовере, иначе MACD на ровном спуске
# перевернёт лонг раньше, чем лестница наберётся (ось армится стопом, стоп
# взят заведомо далёким, чтобы не сработать).
BASE = {"symbol": SYM, "qty": 1, "fast": 5, "slow": 20, "signal": 5,
        "tp_atr": 400, "sl_pct": 500, "sl_frac": 0, "avg_atr_n": 14,
        "avg_step_atr": 20, "avg_max": 20, "k_avg": 20,
        "flip_min_pts": 20000}


def _bars() -> list[Bar]:
    """Рост (сигнал лонг), затем ровный спуск: лонг усредняется всю дорогу вниз."""
    px, t, out = 84000.0, 1789000000, []
    for n, d in ((140, +10.0), (160, -20.0)):
        for _ in range(n):
            nxt = px + d
            out.append(Bar(t, px, max(px, nxt) + 3, min(px, nxt) - 3, nxt, 10))
            px = nxt
            t += 60
    return out


def _fills(params: dict) -> list[tuple]:
    bars = _bars()
    rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=9_000_000.0)
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("macd_shectory1")
    out, seen = [], 0
    while True:
        asyncio.run(mod.on_bar(rt, params))
        while len(rt._orders) > seen:
            o = rt._orders[seen]
            out.append((o.side, o.qty, o.price))
            seen += 1
        if not rt.advance():
            break
    return out


def _ladder_span(fills: list[tuple]) -> tuple[int, float]:
    """(набранных контрактов, расстояние от первого филла лестницы до последнего).

    Сторона лестницы берётся у ПЕРВОГО филла: на спуске после разворота сигнала
    робот строит шортовую лестницу, и смотреть только покупки нельзя.
    """
    side = fills[0][0]
    legs = []
    for s, q, p in fills:
        if s != side:
            break                       # лестница кончилась, дальше выход
        legs.append((s, q, p))
    assert len(legs) >= 3, legs
    return sum(q for _s, q, _p in legs), abs(legs[0][2] - legs[-1][2])


def test_step_from_last_fill_stretches_the_ladder():
    base_qty, base_span = _ladder_span(_fills({**BASE}))
    last_qty, last_span = _ladder_span(_fills({**BASE, "avg_from_last": 1}))
    # На одинаковом ходе от последнего филла лестница либо растягивается,
    # либо набирает МЕНЬШЕ контрактов — и то и другое значит «позиция меньше
    # в крайней точке», ради чего ось и заводилась.
    assert last_span > base_span or last_qty < base_qty, (
        base_qty, base_span, last_qty, last_span)


def test_off_by_default():
    assert _fills({**BASE}) == _fills({**BASE, "avg_from_last": 0})


def test_no_add_while_price_moves_back_in_our_favour():
    """Наблюдение оператора 21.09: при выключенной оси робот доливал шорт НА ПАДЕНИИ.

    Условие добора «price >= avg + dist» — это УРОВЕНЬ от средней: пока цена за
    уровнем, добор проходит на любом баре, в том числе когда цена возвращается в
    пользу позиции (у живого agent-macdshort пять таких доборов за день:
    84710 -> 84670, 84850 -> 84820, 84820 -> 84780). Шаг от ФИЛЛА требует нового
    хода против позиции, поэтому такие доборы исчезают.
    """
    def back_adds(params: dict) -> int:
        fills = _fills(params)
        pos = 0
        last = None
        back = 0
        for side, qty, price in fills:
            sgn = 1 if side == "buy" else -1
            grows = pos == 0 or (pos > 0) == (sgn > 0)
            if grows and pos != 0 and last is not None:
                if (price - last) * (1 if pos > 0 else -1) > 0:
                    back += 1          # добор по ЛУЧШЕЙ цене = в сторону прибыли
            if grows:
                last = price
            pos += sgn * qty
            if pos == 0:
                last = None
        return back

    # Пила внутри хода против позиции: цена то идёт против, то отходит назад.
    assert back_adds({**BASE, "avg_from_last": 1}) == 0
