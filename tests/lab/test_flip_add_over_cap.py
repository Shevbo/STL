"""Финальная доливка на убыточном кроссовере: режим 2 идёт СВЕРХ потолка.

Заказ оператора 24.09.2026. Повод: замер того же дня показал, что режим 1 почти
ничего не менял — к моменту убыточного кроссовера позиция уже упирается в avg_max,
остаток до потолка ноль, и доливать нечем ровно в тех эпизодах, ради которых ось
заводилась (при живом расписании режим 1 был хуже базы 8/8 на RIH6, 6/8 на RIM6,
8/8 на RIU6).

Тест зеркалит арифметику из make_on_bar, как соседние тесты движка.
"""
from trader.lab.strategies.library import REGISTRY


def final_add(step: int, k_avg: float, cur: int, avg_max: int, mode: int) -> int:
    """Сколько контрактов долить на убыточном развороте (0 = не доливаем)."""
    if not mode:
        return 0
    room = avg_max - abs(cur) if mode != 2 else 10 ** 9
    if room <= 0:
        return 0
    return max(0, min(int(step * k_avg + 0.5), room))


LIVE_K = 2.0          # k_avg=20 в живой спеке хранится x10


def test_mode1_cannot_add_at_the_ceiling():
    """Позиция на потолке — режим 1 бессилен, это и была причина нулевого эффекта."""
    assert final_add(8, LIVE_K, cur=20, avg_max=20, mode=1) == 0


def test_mode2_adds_the_next_step_over_the_ceiling():
    """Следующая ступень = предыдущая x k_avg: при 8 и k=2.0 доливается 16."""
    assert final_add(8, LIVE_K, cur=20, avg_max=20, mode=2) == 16


def test_mode1_still_capped_by_the_room_below_the_ceiling():
    assert final_add(8, LIVE_K, cur=14, avg_max=20, mode=1) == 6
    assert final_add(8, LIVE_K, cur=14, avg_max=20, mode=2) == 16


def test_off_means_off():
    assert final_add(8, LIVE_K, cur=20, avg_max=20, mode=0) == 0


def test_schema_allows_the_new_value():
    spec = next(s for s in REGISTRY["macd_shectory1"]["params_schema"]
                if s["key"] == "flip_add_max")
    assert spec["max"] >= 2, spec
