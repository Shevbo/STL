"""Слои DeskBot в make_on_bar (13.09.2026): тейк %, трейлинг по сторонам, тейк по RSI,
шаг усреднения в пунктах по сторонам. Все выключены по умолчанию."""
import pytest

from tests.lab.test_shectory1 import step
from tests.lab.test_strategies import _FakeRT

BASE = {"symbol": "SIM6", "qty": 1, "avg_max": 1}


def sells(rt):
    return [o for o in rt.orders if o[0] == "sell"]


@pytest.mark.asyncio
async def test_tp_pct_exits_at_percent_of_avg():
    p = {**BASE, "tp_pct": 120}                     # 1.20%
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, p)
    assert rt.signed == 1
    await step(rt, 1, 120, 101.1, p)
    assert not sells(rt)
    await step(rt, 1, 180, 101.3, p)
    assert sells(rt) and sells(rt)[0][2] == 101.3


@pytest.mark.asyncio
async def test_long_trail_needs_activation_then_exits_on_pullback():
    p = {**BASE, "trail_act_l": 100, "trail_back_l": 50}   # 1% активация, 0.5% откат
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, p)
    await step(rt, 1, 120, 100.9, p)                # +0.9% — не активирован
    await step(rt, 1, 180, 100.3, p)                # откат 0.6%, но трейла ещё нет
    assert not sells(rt)
    await step(rt, 1, 240, 102.0, p)                # активация
    await step(rt, 1, 300, 101.6, p)                # -0.39% от пика
    assert not sells(rt)
    await step(rt, 1, 360, 101.4, p)                # -0.59% — выход
    assert sells(rt)[0][2] == 101.4


@pytest.mark.asyncio
async def test_short_trail_uses_its_own_side_params():
    p = {**BASE, "trail_act_l": 1000, "trail_back_l": 500,   # лонговый трейл не должен мешать
         "trail_act_s": 100, "trail_back_s": 50}
    rt = _FakeRT()
    await step(rt, -1, 60, 100.0, p)
    assert rt.signed == -1
    await step(rt, -1, 120, 98.0, p)                # активация шортового
    await step(rt, -1, 180, 98.6, p)                # +0.61% от лучшей — выход
    assert rt.orders[-1] == ("buy", 1, 98.6)


@pytest.mark.asyncio
async def test_trail_peak_resets_on_new_position():
    p = {**BASE, "trail_act_l": 100, "trail_back_l": 50}
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, p)
    await step(rt, 1, 120, 110.0, p)                # пик 110 в первой позиции
    await step(rt, -1, 180, 109.9, p)               # разворот в шорт
    await step(rt, 1, 240, 105.0, p)                # новый лонг @105
    await step(rt, 1, 300, 105.5, p)                # старый пик 110 не должен стрелять
    assert rt.signed == 1


@pytest.mark.asyncio
async def test_rsi_tp_closes_long_on_overbought():
    p = {**BASE, "rsi_tp_n": 3, "rsi_tp_lvl": 70}
    rt = _FakeRT()
    for i, px in enumerate([100.0, 99.0, 100.0, 99.0, 100.0]):   # RSI около 50
        await step(rt, 1, 60 * (i + 1), px, p)
    assert rt.signed == 1 and not sells(rt)
    for i, px in enumerate([101.0, 102.0, 103.0]):               # RSI -> 100
        await step(rt, 1, 600 + 60 * i, px, p)
    assert sells(rt)


@pytest.mark.asyncio
async def test_avg_step_pts_per_side_without_atr():
    p = {**BASE, "avg_max": 3, "avg_step_pts_l": 5, "avg_step_pts_s": 50}
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, p)
    await step(rt, 1, 120, 97.0, p)                 # -3 пункта — рано
    assert rt.signed == 1
    await step(rt, 1, 180, 94.9, p)                 # -5.1 — добор
    assert rt.signed == 2


@pytest.mark.asyncio
async def test_layers_off_hold_position():
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, BASE)
    for i, px in enumerate([130.0, 70.0, 101.0]):
        await step(rt, 1, 120 + 60 * i, px, BASE)
    assert rt.orders == [("buy", 1, 100.0)]
