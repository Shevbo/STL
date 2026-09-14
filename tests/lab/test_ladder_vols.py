"""Лестница объёмов DeskBot/TSLab (avg_vols), стоп от первого входа, flatten_end."""
import types

import pytest

from tests.lab.test_shectory1 import step
from tests.lab.test_strategies import _FakeRT
from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

BASE = {"symbol": "RIM6", "qty": 1, "avg_max": 1}


@pytest.mark.asyncio
async def test_ladder_targets_whole_position_from_first_entry():
    """1,1,32 шорт шаг 260: уровень 1 на +260 — цель 1 (добора нет), уровень 2 на +520 — до 32."""
    p = {**BASE, "avg_vols": "1,1,32", "avg_step_pts_s": 260}
    rt = _FakeRT()
    await step(rt, -1, 60, 100000.0, p)
    assert rt.signed == -1
    await step(rt, -1, 120, 100200.0, p)
    await step(rt, -1, 180, 100260.0, p)            # уровень 1: цель 1
    assert rt.signed == -1 and len(rt.orders) == 1
    await step(rt, -1, 240, 100400.0, p)            # от первого входа +400 < 520
    assert rt.signed == -1
    await step(rt, -1, 300, 100530.0, p)            # уровень 2: до 32
    assert rt.signed == -32 and rt.orders[-1] == ("sell", 31, 100530.0)
    await step(rt, -1, 360, 101500.0, p)            # уровней больше нет
    assert rt.signed == -32


@pytest.mark.asyncio
async def test_sl_first_measures_stop_from_first_entry_not_avg():
    p = {**BASE, "avg_vols": "1,1,5", "avg_step_pts_l": 1, "sl_pct": 300, "sl_first": 1}
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, p)
    await step(rt, 1, 120, 99.0, p)                 # уровень 1: цель 1
    await step(rt, 1, 180, 98.0, p)                 # уровень 2: до 5, средняя 98.4
    assert rt.signed == 5
    await step(rt, 1, 240, 96.9, p)                 # -3.1% от первого входа (от средней -1.5%)
    assert rt.signed == 0


@pytest.mark.asyncio
async def test_flatten_end_books_open_position():
    async def on_bar(stl, params):
        pos = await stl.get_position(params["symbol"])
        if pos.side == "flat" and not stl.get_state("done", 0):
            await stl.place_order(params["symbol"], "buy", 1, (await stl.get_bars(params["symbol"], 1, 1))[-1].close)
            stl.set_state("done", 1)
    mod = types.ModuleType("m")
    mod.on_bar = on_bar
    # 9 баров: курсор стартует с 4 (BacktestRuntime), вход на 4 -> филл 5, закрытие на 7 -> филл 8.
    bars = [Bar(time=60 * i, open=100.0 + i, high=100.0 + i, low=100.0 + i, close=100.0 + i, volume=1)
            for i in range(9)]
    kept = await run_single_backtest(mod, bars, "RIM6", {"symbol": "RIM6"})
    flat = await run_single_backtest(mod, bars, "RIM6", {"symbol": "RIM6", "flatten_end": 1})
    assert kept["total_trades"] == 0
    assert flat["total_trades"] == 1 and flat["trades"][-1]["side"] == "sell"
