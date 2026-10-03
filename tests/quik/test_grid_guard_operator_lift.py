"""Оператор снял «только на выход» — защита не возвращает режим через проход.

03.10.2026 кнопка «Обычный режим» снималась на один проход: позиция −24 при лоте 7
(3.43 уровня при пороге 3) оставалась выше порога, и сторож включал режим обратно за
6 секунд; оператор жал её трижды за день. Решение оператора выше автоматики, но
защита не отключается насовсем: после снятия она считает набор ЗАНОВО от этой точки.
"""
import random

import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, GSTEPS, _gstore


def _so(**kw):
    base = dict(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                g_step=100.0, g_buys=5, g_sells=5, g_lot=7, g_base=85000.0,
                g_stop_pts=0.0, created_ms=GNOW, status="armed",
                g_trig_fills=3, g_trig_move_pct=0.0)
    base.update(kw)
    return SmartOrder(**base)


def test_lift_at_a_position_above_the_threshold_silences_the_guard():
    so = _so(g_pos=-24, g_avg=9909.0, exit_only=True, g_trig_ms=1)
    assert so_mod.grid_guard_hit(so, 85000.0), "до снятия защита держит"
    so_mod.grid_guard_rebase(so, 85000.0)
    for _ in range(5):
        assert so_mod.grid_guard_hit(so, 85000.0) == "", (
            "после снятия оператором защита вернулась на том же наборе")
    assert so.g_trig_ms == 0 and so.g_rearm_at_ms == 0


@pytest.mark.parametrize("side", [-1, 1])
def test_guard_fires_again_only_after_a_fresh_threshold_of_levels(side):
    so = _so(g_pos=24 * side)
    so_mod.grid_guard_rebase(so, 85000.0)
    so.g_pos = 24 * side + 7 * 2 * side           # ещё два уровня: мало
    assert so_mod.grid_guard_hit(so, 85000.0) == ""
    so.g_pos = 24 * side + 7 * 3 * side           # три уровня сверх точки: срабатывает
    assert so_mod.grid_guard_hit(so, 85000.0)


@pytest.mark.parametrize("seed", range(40))
def test_guard_after_lift_matches_an_independent_running_minimum_model(seed):
    """Независимая модель: набор = |поз| минус бегущий минимум |поз| с момента снятия
    (включая саму точку). Путь остаётся на одной стороне (шорт)."""
    rnd = random.Random(seed)
    start = -7 * rnd.randint(3, 8)
    so = _so(g_pos=start)
    so_mod.grid_guard_rebase(so, 85000.0)
    low = abs(start)
    pos = start
    for i in range(80):
        pos = max(-7 * 30, min(-7, pos + 7 * rnd.choice((-1, 1, 1))))
        so.g_pos = pos
        low = min(low, abs(pos))
        want = (abs(pos) - low) / 7 >= 3
        got = so_mod.grid_guard_hit(so, 85000.0) != ""
        assert got == want, f"seed {seed}, шаг {i}: позиция {pos}, минимум {low}, защита {got}"


def test_lift_at_zero_position_changes_nothing():
    so = _so(g_pos=0)
    so_mod.grid_guard_rebase(so, 85000.0)
    so.g_pos = -21
    assert so_mod.grid_guard_hit(so, 85000.0), "от нуля считается, как и раньше"


def test_position_flip_clears_the_reference_point():
    so = _so(g_pos=-24)
    so_mod.grid_guard_rebase(so, 85000.0)
    so.g_pos = 21                                  # перешла в лонг на три уровня
    assert so_mod.grid_guard_hit(so, 85000.0), "после разворота считается от нуля"
    assert so.g_guard_base == 0


def test_price_already_beyond_the_threshold_is_not_a_new_touch():
    so = _so(g_trig_fills=0, g_trig_move_pct=0.25, g_trig_touches=1, g_pos=-3)
    price = 84700.0                                # ниже базы на 0.35%
    assert so_mod.grid_guard_hit(so, price), "до снятия цена держит защиту"
    so_mod.grid_guard_rebase(so, price)
    for _ in range(4):
        assert so_mod.grid_guard_hit(so, price) == "", "цена за порогом не новое касание"
    assert so_mod.grid_guard_hit(so, 85000.0) == ""        # вернулась к базе
    assert so_mod.grid_guard_hit(so, price), "новый заход в зону считается заново"


def test_a_full_pass_does_not_bring_exit_only_back_after_the_operator_lift(tmp_path):
    book = SmartOrderBook(str(tmp_path / "g.json"))
    so = _so(g_pos=-24, g_avg=85000.0, exit_only=True, g_trig_ms=GNOW - 1)
    book.orders.append(so)
    so.exit_only = False                           # как делает ручка
    so_mod.grid_guard_rebase(so, 85000.0)
    for i in range(4):
        _grid_sync(book, _gstore(85000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
                   GNOW + i * 1000, True)
        assert so.exit_only is False, f"проход {i}: защита вернула режим"
