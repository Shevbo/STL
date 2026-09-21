"""Убыточный флип закрывается на ВОЗВРАТЕ, а не на экстремуме (flip_back_pct).

Заведено 21.09.2026 после дня двух горок: живой lxk22 дважды набрал полную лестницу
против хода и дважды сбросил её ровно в крайней точке — шорт −20 контрактов по 84640
на вершине (−10.4 тыс) и лонг +22 по 84320 на дне (−9.9 тыс), а к 12:03 цена
вернулась на 84030.

Тест гоняет РЕАЛЬНЫЙ движок на синтетической пиле и проверяет ПОВЕДЕНИЕ, а не итог
(итог на одной выдуманной кривой ничего не значит — приговор выносит прогон на i9):

  1. выход происходит позже, чем сброс на экстремуме, и ровно на заданной доле
     отыгранного хода;
  2. позиция ОБЯЗАТЕЛЬНО закрывается — первая версия правки только снимала
     удержание, и если сигнал успевал вернуться, позиция висела против него до
     конца окна (инцидент «долины» 08.08.2026);
  3. без стопа ось не армится;
  4. по умолчанию поведение не меняется.
"""
import asyncio
import types

from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
# flip_close_loss=1 — ПРЕЖНЕЕ поведение (сброс убытка по развороту сигнала). С
# 21.09.2026 по умолчанию это запрещено распоряжением оператора, поэтому базу для
# сравнения приходится задавать явно: иначе «как было» и «с осью» совпадают.
BASE = {"symbol": SYM, "qty": 1, "fast": 5, "slow": 20, "signal": 5,
        "tp_atr": 400, "avg_atr_n": 14, "avg_max": 1, "sl_pct": 300,
        "flip_close_loss": 1}


def _bars() -> list[Bar]:
    """Рост, провал, отскок, возврат: шорт со дна получает и ход против, и возврат."""
    px, t, out = 84000.0, 1789000000, []
    for n, d in ((60, +8.0), (25, -40.0), (40, +20.0), (40, -20.0)):
        for _ in range(n):
            nxt = px + d
            out.append(Bar(t, px, max(px, nxt) + 3, min(px, nxt) - 3, nxt, 10))
            px = nxt
            t += 60
    return out


def _run(params: dict) -> list[tuple]:
    """Список (номер бара, сторона, объём, цена)."""
    bars = _bars()
    rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=5_000_000.0)
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("macd_cross")
    out, seen = [], 0
    while True:
        asyncio.run(mod.on_bar(rt, params))
        while len(rt._orders) > seen:
            o = rt._orders[seen]
            out.append((rt._cursor, o.side, o.qty, o.price))
            seen += 1
        if not rt.advance():
            break
    return out


def test_exit_waits_for_the_retrace_and_happens():
    base = _run({**BASE})
    back = _run({**BASE, "flip_back_pct": 50})
    # Первая сделка у обоих одна и та же — расходятся они на ВЫХОДЕ.
    assert base[0] == back[0], (base[0], back[0])
    entry_bar, side, _q, entry_px = back[0]
    d = -1 if side == "sell" else 1
    base_exit = next(o for o in base if o[0] > entry_bar)
    back_exit = next(o for o in back if o[0] > entry_bar)
    assert back_exit[0] > base_exit[0], "выход не стал позже сброса на экстремуме"
    # Доля возврата: от экстремума хода против позиции до цены выхода.
    bars = _bars()
    seg = [b.close for b in bars[entry_bar:back_exit[0] + 1]]
    ext = min(seg) if d > 0 else max(seg)
    adverse = (entry_px - ext) * d
    got = (back_exit[3] - ext) * d
    assert adverse > 0, "в сценарии нет хода против позиции"
    assert got >= adverse * 0.5 - 1e-6, (got, adverse)
    # И выход действительно ЗАКРЫВАЕТ позицию: обратная сторона тем же объёмом.
    # (Дальше робот может открыться заново по своему сигналу — это его право.)
    assert back_exit[1] != side and back_exit[2] == back[0][2]


def test_not_armed_without_a_stop():
    """Инвариант «долины»: удержать выход можно только когда есть пол по убытку."""
    no_stop = {**BASE, "sl_pct": 0, "sl_frac": 0}
    assert _run({**no_stop, "flip_back_pct": 50}) == _run(no_stop)


def test_off_by_default():
    assert _run({**BASE}) == _run({**BASE, "flip_back_pct": 0})
