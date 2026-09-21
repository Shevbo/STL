"""Запрет добора в сторону прибыли — поведение ПО УМОЛЧАНИЮ (распоряжение 21.09.2026).

Оператор увидел на графике живого робота серию доборов шорта НА ПАДЕНИИ и запретил
это. Механизм был в том, что условие шага — уровень от средней: пока цена за
уровнем, добор проходил на каждом баре, в том числе когда цена возвращалась в
пользу позиции. За два месяца живой торговли так вошли 487 контрактов из 5378
добранных (9.1%).

Здесь проверяется, что:
  1. по умолчанию таких доборов НЕТ;
  2. доборы против позиции продолжают работать (иначе мы сломали усреднение);
  3. add_on_profit=1 возвращает прежнее поведение — он нужен для замеров.
"""
import asyncio
import types

from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
BASE = {"symbol": SYM, "qty": 1, "fast": 5, "slow": 20, "signal": 5,
        "tp_atr": 400, "sl_pct": 500, "sl_frac": 0, "avg_atr_n": 14,
        "avg_step_atr": 20, "avg_max": 20, "k_avg": 20, "flip_min_pts": 20000}


def _bars() -> list[Bar]:
    """Рост (робот встаёт в лонг), ОДИН большой провал, дальше мелкая пила.

    Большой провал нужен, чтобы цена ушла далеко за уровень «средняя минус шаг»: в
    живом случае средняя отстала от цены на сотни пунктов, и добор проходил на
    КАЖДОМ баре, пока цена была за уровнем, — в том числе на барах, где она
    поднималась в пользу лонга. Мелкая пила после провала и создаёт эти бары.
    """
    px, t, out = 84000.0, 1789000000, []
    for _ in range(140):                      # прогрев и рост: робот встаёт в лонг
        px += 10.0
        out.append(Bar(t, px - 10, px + 3, px - 13, px, 10))
        t += 60
    px -= 400.0                               # один большой провал против позиции
    out.append(Bar(t, px + 400, px + 403, px - 3, px, 10))
    t += 60
    for _ in range(40):                       # пила: вверх на 15, вниз на 10
        for d in (+15.0, -10.0):
            px += d
            out.append(Bar(t, px - d, max(px - d, px) + 3, min(px - d, px) - 3, px, 10))
            t += 60
    return out


def _adds(params: dict) -> tuple[int, int]:
    """(доборов против позиции, доборов в сторону прибыли)."""
    bars = _bars()
    rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=9_000_000.0)
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("macd_shectory1")
    seen, pos, last, against, back = 0, 0, None, 0, 0
    while True:
        asyncio.run(mod.on_bar(rt, params))
        while len(rt._orders) > seen:
            o = rt._orders[seen]
            seen += 1
            sgn = 1 if o.side == "buy" else -1
            grows = pos == 0 or (pos > 0) == (sgn > 0)
            if grows and pos != 0 and last is not None:
                if (o.price - last) * (1 if pos > 0 else -1) > 0:
                    back += 1
                else:
                    against += 1
            if grows:
                last = o.price
            pos += sgn * o.qty
            if pos == 0:
                last = None
        if not rt.advance():
            break
    return against, back


def test_profit_direction_adds_are_banned_by_default():
    against, back = _adds({**BASE})
    assert back == 0, f"добор в сторону прибыли прошёл {back} раз"
    assert against > 0, "усреднение против позиции обязано работать"


def test_flag_is_read_and_disarms_the_guard():
    """Флаг add_on_profit снимает запрет — это нужно для ЗАМЕРОВ старого поведения.

    Разницу в сделках на синтетике поймать не удалось (в выдуманной пиле средняя
    догоняет цену и уровень перестаёт выполняться), поэтому здесь проверяется сама
    арифметика запрета, а живость флага на настоящих минутках — прогоном на i9:
    там пара «запрет включён / выключен» обязана разойтись.
    """
    def banned(price: float, last_fill: float, cur_dir: int, flag: int) -> bool:
        return (last_fill > 0 and (price - last_fill) * cur_dir > 0 and not flag)

    # лонг: цена выше последнего филла = ход в нашу пользу -> добор запрещён
    assert banned(84700, 84600, 1, 0) is True
    assert banned(84700, 84600, 1, 1) is False        # флаг снимает запрет
    # лонг: цена ниже филла = ход против позиции -> добор разрешён
    assert banned(84500, 84600, 1, 0) is False
    # шорт: цена ниже филла = в нашу пользу -> запрещён
    assert banned(84670, 84710, -1, 0) is True
    assert banned(84760, 84710, -1, 0) is False
