"""Швы склейки: позиция не должна их переживать, а рынок не должен быть принят за шов.

Склейка непрерывного контракта сшивает разные серии без выравнивания базиса. На
RI за 2025-11..2026-08 таких швов три, худший 01.07.2026 на −13 690 пунктов: это
22 946 ₽ на контракт и 550 705 ₽ на позиции в 24 контракта — больше, чем итог
всего девятимесячного прогона. Позиция, пережившая шов, делает результат
вымыслом.
"""
import pytest
from types import SimpleNamespace

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar
from trader.lab.splice import find_seam_indices, seam_plan

DAY = 86_400
MIN = 60


def _bars(spec):
    """spec: [(смещение_секунд, цена), ...] -> список баров."""
    return [Bar(time=t, open=p, high=p, low=p, close=p, volume=1) for t, p in spec]


def test_seam_needs_both_a_jump_and_a_day_boundary():
    """Скачок внутри одних суток — это рынок. Склейка переключает контракт только
    на границе дня, и без этого условия резкое движение читалось бы как шов."""
    intraday = _bars([(0, 100_000), (MIN, 100_000), (2 * MIN, 88_000)])
    assert find_seam_indices(intraday) == set()

    across = _bars([(0, 100_000), (MIN, 100_000), (DAY, 88_000)])
    assert find_seam_indices(across) == {2}


def test_small_overnight_gap_is_not_a_seam():
    """Обычный утренний гэп не должен закрывать позицию: порог 1.5%."""
    small = _bars([(0, 100_000), (DAY, 100_500)])          # +0.5%
    assert find_seam_indices(small) == set()


def test_seam_plan_closes_two_bars_early_because_fills_land_next_bar():
    """Движок наливает заявку по открытию СЛЕДУЮЩЕГО бара. Выход, отданный на
    последнем баре старого контракта, исполнился бы уже по цене нового — то есть
    ровно по шву. Поэтому закрываемся за два бара."""
    bars = _bars([(0, 100_000), (MIN, 100_000), (DAY, 88_000),
                  (DAY + MIN, 88_100), (DAY + 2 * MIN, 88_200)])
    close_at, mute = seam_plan(bars, warmup=2)
    assert close_at == {0}                 # заявка здесь -> налив на баре 1, до шва
    assert mute == {2, 3}                  # первые два бара новой серии


def test_broken_bars_never_raise():
    assert find_seam_indices([]) == set()
    assert find_seam_indices(_bars([(0, 0), (DAY, 100)])) == set()


class _Buyer:
    """Берёт лонг при первой возможности и закрывает его через `hold` баров.

    Круговая сделка нужна именно замкнутая: метрики считаются по ЗАКРЫТЫМ
    round-trip'ам, и вечно открытая позиция дала бы net 0 — то самое молчание,
    в котором фантом шва и прячется.
    """

    @staticmethod
    async def on_bar(stl, params):
        sym = params["symbol"]
        pos = await stl.get_position(sym)
        bars = await stl.get_bars(sym, tf=1, n=1)
        n = int(stl.get_state("n", 0) or 0) + 1
        stl.set_state("n", n)
        if pos.quantity == 0 and not stl.get_state("done"):
            if not stl.get_state("in"):
                stl.set_state("in", n)
                await stl.place_order(sym, "buy", 1, bars[-1].close)
        elif pos.quantity > 0 and n - int(stl.get_state("in", 0) or 0) >= int(params.get("hold", 6)):
            stl.set_state("done", 1)
            await stl.place_order(sym, "sell", pos.quantity, bars[-1].close)


@pytest.mark.asyncio
async def test_position_does_not_survive_the_seam():
    """С флагом склейки движок закрывает позицию ДО шва, и фантом не попадает в
    результат. Без флага — прежнее поведение, фантом на месте."""
    # Курсор движка стартует с 5-го бара (BacktestRuntime._cursor = min(4, ...)),
    # поэтому до шва нужен запас, иначе прогон начнётся уже за ним.
    spec = [(i * MIN, 100_000.0) for i in range(12)]
    spec += [(DAY + i * MIN, 88_000.0) for i in range(12)]
    bars = _bars(spec)
    # hold подобран так, чтобы круговая сделка ПЕРЕСЕКАЛА шов: иначе
    # проверять нечего — позиция и так закрылась бы до него.
    p = {"symbol": "RI", "splice_rolls": 1, "splice_warmup": 2, "hold": 10}

    with_roll = await run_single_backtest(_Buyer, bars, "RI", p, point_value=1.0)
    without = await run_single_backtest(_Buyer, bars, "RI", dict(p, splice_rolls=0),
                                        point_value=1.0)
    # Без обработки шва робот проносит лонг через −12 000 пунктов и фиксирует их.
    assert without["net_profit"] < -10_000
    # С обработкой — тот же прогон не наказан за смену контракта.
    assert with_roll["net_profit"] > without["net_profit"] + 10_000


@pytest.mark.asyncio
async def test_flag_off_is_bit_for_bit_the_old_behaviour():
    """Прогоны по КОНКРЕТНОМУ контракту швов не имеют, и включать им ничего не
    надо: без флага движок обязан вести себя ровно как раньше."""
    bars = _bars([(i * MIN, 100_000.0 + i) for i in range(6)])
    a = await run_single_backtest(_Buyer, bars, "RI", {"symbol": "RI"}, point_value=1.0)
    b = await run_single_backtest(_Buyer, bars, "RI",
                                  {"symbol": "RI", "splice_rolls": 0}, point_value=1.0)
    assert a["net_profit"] == b["net_profit"]
    assert a["total_trades"] == b["total_trades"]


@pytest.mark.asyncio
async def test_warmup_comes_from_the_strategy_when_not_given():
    """Окно молчания после шва берём У САМОЙ стратегии, если она его объявляет:
    бланкетное число врало бы в обе стороны (у SMA-семейства прогрев короткий, у
    pivot — 2200 баров)."""
    mod = SimpleNamespace(on_bar=_Buyer.on_bar, STRATEGY_WARMUP=lambda p: 3)
    spec = [(i * MIN, 100_000.0) for i in range(3)]
    spec += [(DAY + i * MIN, 88_000.0) for i in range(6)]
    bars = _bars(spec)
    r = await run_single_backtest(mod, bars, "RI",
                                  {"symbol": "RI", "splice_rolls": 1}, point_value=1.0)
    # Первые три бара новой серии молчат -> вход не раньше четвёртого.
    entries = [t for t in r["trades"] if t["side"] == "buy"]
    assert entries, "после окна молчания робот обязан снова торговать"
    assert entries[-1]["time"] >= bars[3 + 3].time
