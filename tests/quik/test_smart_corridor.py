"""Коридор: торговля от стенок наклонного канала с переворотами.

Заказ оператора 29.09.2026. Верхняя граница — прямая через две точки, нижняя ей
параллельна; касание стенки открывает позицию ОТ неё, касание противоположной
переворачивает (сделка вдвое: закрыть и открыть), уход за стенку дальше стопа
закрывает позицию. Число переворотов ограничивается; исчерпав его, коридор
закрывает позицию и завершается.
"""
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, corridor_action, corridor_bounds, new_id

T0 = 1_790_700_000_000
HOUR = 3_600_000


def _corr(**kw):
    base = dict(so_id=new_id(), kind="corridor", code="RIZ6", side="sell", qty=10,
                c_qty=10, c_t1_ms=T0, c_p1=85000.0, c_t2_ms=T0 + HOUR, c_p2=85000.0,
                c_low=84000.0, c_stop_pts=200.0, created_ms=T0)
    base.update(kw)
    return SmartOrder(**base)


def test_horizontal_and_sloped_bounds():
    flat = _corr()
    assert corridor_bounds(flat, T0) == (84000.0, 85000.0)
    assert corridor_bounds(flat, T0 + 5 * HOUR) == (84000.0, 85000.0)
    # наклон: +1000 за час, ширина 1000 сохраняется — угол один на весь коридор
    up = _corr(c_p2=86000.0)
    assert corridor_bounds(up, T0) == (84000.0, 85000.0)
    low, top = corridor_bounds(up, T0 + HOUR)
    assert (low, top) == (85000.0, 86000.0)
    low, top = corridor_bounds(up, T0 + 2 * HOUR)   # продолжается за вторую точку
    assert (low, top) == (86000.0, 87000.0)


def test_walls_open_flip_and_do_not_re_enter():
    so = _corr()
    # внутри канала — тишина
    assert corridor_action(so, 84500, T0)[:2] == (0, 0)
    # верхняя стенка: продажа базовым объёмом
    side, qty, why = corridor_action(so, 85000, T0)
    assert (side, qty) == (-1, 10) and "верхней" in why
    so_mod.corridor_after_fire(so, side, qty, False)
    assert so.c_pos == -10 and so.c_flips == 0, "первый вход — не переворот"
    # повторное касание той же стенки не добавляет позицию
    assert corridor_action(so, 85010, T0)[:2] == (0, 0)
    # нижняя стенка: ПЕРЕВОРОТ, сделка вдвое
    side, qty, why = corridor_action(so, 84000, T0)
    assert (side, qty) == (1, 20) and "нижней" in why
    so_mod.corridor_after_fire(so, side, qty, False)
    assert so.c_pos == 10 and so.c_flips == 1


def test_stop_beyond_the_wall_closes_and_ends():
    so = _corr(c_pos=-10)
    assert corridor_action(so, 85150, T0)[:2] == (0, 0), "внутри допуска ещё держим"
    side, qty, why = corridor_action(so, 85200, T0)
    assert (side, qty) == (1, 10) and why.startswith("стоп")
    so_mod.corridor_after_fire(so, side, qty, True)
    assert so.c_pos == 0 and so.c_done is True
    assert corridor_action(so, 84000, T0)[:2] == (0, 0), "законченный коридор молчит"
    # лонг стерегут снизу
    so2 = _corr(c_pos=10, c_done=False)
    assert corridor_action(so2, 83800, T0)[:2] == (-1, 10)


def test_flip_limit_closes_the_position_and_finishes():
    so = _corr(c_flips_max=1, c_pos=-10)
    side, qty, _ = corridor_action(so, 84000, T0)      # первый переворот
    so_mod.corridor_after_fire(so, side, qty, False)
    assert so.c_flips == 1 and so.c_done is True and so.c_pos == 10
    # дальше коридор обязан ЗАКРЫТЬ позицию, а не бросить её в рынке
    assert so_mod.corridor_closeout(so) == (-1, 10)


def test_validation_guards_the_geometry():
    assert _corr(c_low=85500.0).validate() is not None      # нижняя выше верхней
    assert _corr(c_t2_ms=T0 - 1).validate() is not None     # вторая точка раньше первой
    assert _corr(c_p1=0).validate() is not None             # нет верхней границы
    assert _corr(c_low=0).validate() is not None            # нет нижней
    assert _corr().validate() is None
