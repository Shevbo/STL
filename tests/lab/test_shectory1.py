"""Модернизация Shectory1: ND-амплитуда -> авто-разножка + мартингейл доборов.

Проверяем ровно три вещи, ради которых всё делалось, и обратную совместимость:
выключенные параметры (gap_auto=0, k_avg=10) обязаны давать прежнее поведение.
"""
import pytest
from trader.lab.runtime import Bar

from tests.lab.test_strategies import _FakeRT, _WANT, _mob

DAY = 86400


def push(rt, t, price, high=None, low=None):
    rt._bars.append(Bar(time=t, open=price, high=high if high is not None else price,
                        low=low if low is not None else price, close=price, volume=1))


async def step(rt, want, t, price, params, high=None, low=None):
    _WANT["v"] = want
    push(rt, t, price, high, low)
    await _mob("_cdtest")(rt, params)


BASE = {"symbol": "SIM6", "qty": 2, "avg_max": 34, "avg_step_atr": 10,
        "avg_atr_n": 5, "tp_atr": 0, "min_gap_pts": 0}


async def _fall(rt, params, bars=20, start=100.0, t0=DAY):
    """Открыть лонг и уронить цену на 1 пункт за бар — лестница доборов работает."""
    for i in range(bars):
        await step(rt, 1, t0 + i * 60, start - i, params)


def test_warmup_covers_the_longest_ema():
    """Прогрев обязан покрывать САМУЮ ДЛИННУЮ EMA, даже когда fast > slow.

    Живой конфиг fast=57/slow=48: формула slow+signal+2 даёт окно 60 баров, и на
    RIU6 M1 за 01.06-31.07.2026 сигнал не перевернулся НИ РАЗУ (при окне 120+ —
    ~1450 переворотов). Робот в таком бэктесте набирает потолок за три дня и стоит
    в позиции два месяца.
    """
    from trader.lab.strategies.library import REGISTRY
    w = REGISTRY["macd_shectory1"]["warmup"]
    assert w({"fast": 57, "slow": 48, "signal": 10}) >= 4 * 57
    assert w({"fast": 12, "slow": 26, "signal": 9}) >= 4 * 26


@pytest.mark.asyncio
async def test_k_avg_grows_each_add_half_up():
    """k_avg=1.5: доборы 3, 5, 8, 12 после базового входа 2 (округление вверх)."""
    rt = _FakeRT()
    await _fall(rt, {**BASE, "k_avg": 15})
    qtys = [q for side, q, _ in rt.orders if side == "buy"]
    assert qtys[:5] == [2, 3, 5, 8, 12]


@pytest.mark.asyncio
async def test_k_avg_off_keeps_flat_ladder():
    """k_avg=1.0 (и отсутствие параметра) — прежнее поведение: все доборы по qty."""
    for params in ({**BASE, "k_avg": 10}, dict(BASE)):
        rt = _FakeRT()
        await _fall(rt, params)
        qtys = [q for side, q, _ in rt.orders if side == "buy"]
        assert set(qtys[:5]) == {2}, params


@pytest.mark.asyncio
async def test_ladder_never_exceeds_avg_max():
    rt = _FakeRT()
    await _fall(rt, {**BASE, "k_avg": 40}, bars=30)
    assert abs(rt.signed) <= BASE["avg_max"]


@pytest.mark.asyncio
async def test_gap_auto_uses_amplitude_over_nd_days():
    """Разножка «Авто» = [амплитуда за ND дней] / avg_max и реально режет доборы.

    День 0: размах 100..200 -> амплитуда 100, avg_max=10 -> разножка 10 пунктов.
    День 1: цена падает по 1 пункту за бар, доборы должны ждать 10 пунктов хода.
    """
    p = {**BASE, "avg_max": 10, "nd_days": 5, "gap_auto": 1, "k_avg": 10}
    rt = _FakeRT()
    for i in range(10):                      # день 0: формируем амплитуду
        await step(rt, 0, i * 60, 150.0, p, high=200.0, low=100.0)
    await _fall(rt, p, bars=12, t0=DAY)      # день 1: вход @100, дальше -1/бар
    assert rt.get_state("gap_skips", 0) > 0, "авто-разножка обязана отсеивать доборы"
    entries = [pr for side, _, pr in rt.orders if side == "buy"]
    assert entries[0] == 100.0
    assert all(abs(b - a) >= 10.0 for a, b in zip(entries, entries[1:])), entries


@pytest.mark.asyncio
async def test_gap_auto_off_is_unchanged():
    """Тот же рынок с gap_auto=0 — доборы идут каждый бар, отсева нет."""
    p = {**BASE, "avg_max": 10, "nd_days": 5, "gap_auto": 0}
    rt = _FakeRT()
    for i in range(10):
        await step(rt, 0, i * 60, 150.0, p, high=200.0, low=100.0)
    await _fall(rt, p, bars=12, t0=DAY)
    assert not rt.get_state("gap_skips", 0)
    assert rt.get_state("amp_ring") is None, "выключённый режим не должен вести кольцо"


@pytest.mark.asyncio
async def test_amplitude_ignores_the_running_day_and_keeps_nd_days():
    """Кольцо: считаем по ЗАВЕРШЁННЫМ дням, храним не больше ND+1 записей."""
    p = {**BASE, "avg_max": 10, "nd_days": 2, "gap_auto": 1}
    rt = _FakeRT()
    for d in range(5):                       # 5 дней с разным размахом
        for i in range(3):
            await step(rt, 0, d * DAY + i * 60, 150.0, p, high=160.0 + d, low=140.0 - d)
    ring = rt.get_state("amp_ring")
    assert len(ring) == 3                    # ND=2 завершённых + текущий
    assert [r[0] for r in ring] == [2, 3, 4]
