"""Shaving RI 1 — опорная серия и выкуп отклонения.

Три вещи, которые ломают стратегию молча и потому закреплены тестом:
  1. корзина не должна заглядывать в будущее (иначе бэктест рисует прибыль,
     которой нет);
  2. отклонение вверх должно ПРОДАВАТЬ RI, вниз — покупать (знак);
  3. возврат к оси должен закрывать позицию, иначе «догона до оси» нет и
     сделка превращается в голую направленную ставку.
"""
import asyncio

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies import shaving_ri

BASE = 1_700_000_000


def _bar(i: int, price: float, vol: int = 100) -> Bar:
    return Bar(time=BASE + i * 60, open=price, high=price, low=price, close=price, volume=vol)


def test_extra_series_never_looks_ahead():
    main = [_bar(i, 100 + i) for i in range(10)]
    ref = [_bar(i, 1000 + i) for i in range(10)]
    rt = BacktestRuntime(bars=main, symbol="MAIN", initial_equity=1.0, extra={"REF": ref})
    rt._cursor = 3
    got = asyncio.run(rt.get_bars("REF", 1, 5))
    assert [b.time for b in got] == [BASE + i * 60 for i in range(4)]
    assert got[-1].close == 1003          # ровно бар текущей минуты, не следующий
    # свой символ по-прежнему идёт основным путём
    assert asyncio.run(rt.get_bars("MAIN", 1, 1))[-1].close == 103


def _series(n: int, spike_from: int, spike_to: int, spike: float):
    """RI = корзина×100 с ±5 пунктов шума; на участке [spike_from, spike_to) RI
    искусственно дороже корзины на `spike` пунктов."""
    ri, bk = [], []
    for i in range(n):
        b = 900.0 + 0.05 * i
        px = b * 100 + (5 if i % 2 else -5)
        if spike_from <= i < spike_to:
            px += spike
        bk.append(_bar(i, b))
        ri.append(_bar(i, px))
    return ri, bk


_P = {"symbol": "RIU6", "basket": "RTSI", "ema_slow": 30, "ema_fast": 1, "z_win": 30,
      "entry_z_x10": 20, "exit_z_x10": 5, "min_dev_pts": 30, "sl_pts": 0,
      "max_hold_min": 0, "min_vol": 5, "max_stale_min": 3, "qty": 1}


def _run(spike: float):
    ri, bk = _series(200, 150, 170, spike)
    return asyncio.run(run_single_backtest(shaving_ri, ri, "RIU6", dict(_P),
                                           extra={"RTSI": bk}))


def test_rich_ri_is_sold_and_closed_on_return_to_axis():
    res = _run(+300.0)
    trades = res["trades"]
    assert trades, "отклонение +300 пунктов должно дать сделку"
    assert trades[0]["side"] == "sell", "RI дороже корзины -> продаём RI"
    assert len(trades) >= 2, "возврат к оси обязан закрыть позицию"
    assert trades[1]["side"] == "buy"
    assert res["total_trades"] >= 1


def test_cheap_ri_is_bought():
    res = _run(-300.0)
    assert res["trades"] and res["trades"][0]["side"] == "buy"


def test_no_trades_without_basket():
    """Без опорной серии сигнала нет вообще — робот обязан молчать, а не
    торговать по одной ноге вслепую."""
    ri, _ = _series(200, 150, 170, +300.0)
    res = asyncio.run(run_single_backtest(shaving_ri, ri, "RIU6", dict(_P), extra=None))
    assert res["trades"] == []


if __name__ == "__main__":
    test_extra_series_never_looks_ahead()
    test_rich_ri_is_sold_and_closed_on_return_to_axis()
    test_cheap_ri_is_bought()
    test_no_trades_without_basket()
    print("ok")
