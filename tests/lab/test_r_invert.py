"""Хаос R (13.09.2026): каждая R-я сделка открывается против сигнала."""
import pytest

from tests.lab.test_shectory1 import step
from tests.lab.test_strategies import _FakeRT

BASE = {"symbol": "SIM6", "qty": 1, "avg_max": 1}


@pytest.mark.asyncio
async def test_r3_on_alternating_signal_skips_flips_without_round_trips():
    """База +,-,+,-,+,- при R=3: стороны +,-,-,-,+,+ ; сделки 3,4 и 6 — удержание,
    без круга «закрыть-открыть» в ту же сторону."""
    p = {**BASE, "r_inv_every": 3}
    rt = _FakeRT()
    sides = []
    for i, w in enumerate([1, -1, 1, -1, 1, -1]):
        await step(rt, w, 60 * (i + 1), 100.0 + i, p)
        sides.append(rt.signed)
    assert sides == [1, -1, -1, -1, 1, 1]
    assert rt.get_state("r_count") == 6
    assert len(rt.orders) == 5            # buy; sell+sell; buy+buy


@pytest.mark.asyncio
async def test_r2_inverts_second_entry_from_flat_and_holds_it():
    p = {**BASE, "r_inv_every": 2}
    rt = _FakeRT()
    await step(rt, 1, 60, 100.0, p)       # сделка 1 — по сигналу
    assert rt.signed == 1
    await step(rt, 0, 120, 101.0, p)      # выход во флэт
    assert rt.signed == 0
    await step(rt, 1, 180, 101.0, p)      # сделка 2 — против сигнала
    assert rt.signed == -1
    await step(rt, 1, 240, 100.5, p)      # базовый сигнал держится — держим инверсную
    assert rt.signed == -1
    await step(rt, 0, 300, 100.0, p)      # базовый сигнал пропал — закрываем
    assert rt.signed == 0
    await step(rt, 1, 360, 100.0, p)      # сделка 3 — снова по сигналу
    assert rt.signed == 1


@pytest.mark.asyncio
async def test_r_off_is_plain_flip():
    rt = _FakeRT()
    sides = []
    for i, w in enumerate([1, -1, 1]):
        await step(rt, w, 60 * (i + 1), 100.0, BASE)
        sides.append(rt.signed)
    assert sides == [1, -1, 1]
    assert rt.get_state("r_count") is None
