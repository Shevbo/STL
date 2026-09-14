"""cross_only: вход из флэта только на баре смены сигнала (DeskBot 2EMA)."""
import pytest

from tests.lab.test_shectory1 import step
from tests.lab.test_strategies import _FakeRT

BASE = {"symbol": "SIM6", "qty": 1, "avg_max": 1, "tp_pct": 100}   # тейк 1% выводит во флэт


@pytest.mark.asyncio
async def test_after_take_profit_waits_for_next_cross():
    p = {**BASE, "cross_only": 1}
    rt = _FakeRT()
    await step(rt, -1, 60, 100.0, p)        # первый бар: смены сигнала ещё не было
    assert rt.signed == 0
    await step(rt, 1, 120, 100.0, p)        # пересечение вверх — лонг
    assert rt.signed == 1
    await step(rt, 1, 180, 101.5, p)        # тейк
    assert rt.signed == 0
    await step(rt, 1, 240, 101.5, p)        # сигнал всё ещё лонг — без пересечения не входим
    await step(rt, 1, 300, 101.0, p)
    assert rt.signed == 0
    await step(rt, -1, 360, 100.0, p)       # пересечение вниз — шорт
    assert rt.signed == -1


@pytest.mark.asyncio
async def test_level_signal_reenters_without_flag():
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, BASE)
    await step(rt, 1, 120, 101.5, BASE)     # тейк
    await step(rt, 1, 180, 101.5, BASE)     # уровень держится — вход сразу
    assert rt.signed == 1
