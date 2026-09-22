"""Детектор дублей сетки: мёртвое значение оси видно, живое — нет."""
from scripts.grid_degeneracy import dead_values


def test_dead_and_live_axis_values():
    sets = []
    for step in range(6):
        for k in (10, 12, 15):
            # k=12 при qty=1 не меняет ничего (дубль k=10), k=15 меняет
            net = 100.0 * step + (7.0 if k == 15 else 0.0)
            sets.append(({"step": step, "k": k}, (net, 10)))
    dead = dead_values(sets)
    assert dead["k"] == [(10, 12, 6)], dead
    assert "step" not in dead
