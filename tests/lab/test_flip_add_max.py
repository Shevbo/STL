"""Финальное усреднение на кроссовере вместо выхода в убыток (flip_add_max).

Заказ оператора 21.09.2026: «сигналы замедления хода — это сигналы как раз усилить
позицию, а не выйти в убыток». На убыточном развороте робот один раз доливает
максимальной ступенью лестницы и дальше только держит.

Охраняем ограничители, без которых это было бы безумием:
  1. доливка ОДНА на позицию;
  2. она не пробивает потолок лестницы avg_max;
  3. ось армится только со стопом (пол по убытку обязателен);
  4. по умолчанию выключена.
"""
from trader.lab.strategies.library import REGISTRY


def final_add(cur: int, avg_max: int, prev_step: int, k_avg: float,
              already: bool, armed: bool, loss: bool, flag: int) -> int:
    """Зеркало арифметики из make_on_bar: сколько контрактов дольёт кроссовер."""
    if not (flag and armed and loss) or already or abs(cur) >= avg_max:
        return 0
    return max(0, min(int(prev_step * k_avg + 0.5), avg_max - abs(cur)))


def test_adds_the_next_ladder_step_once():
    # лестница дошла до шага 8 при k_avg=2.0 -> финальная доливка 16, но потолок 20
    assert final_add(cur=-15, avg_max=20, prev_step=8, k_avg=2.0,
                     already=False, armed=True, loss=True, flag=1) == 5
    # места ещё много -> доливает полную следующую ступень
    assert final_add(cur=-8, avg_max=40, prev_step=8, k_avg=2.0,
                     already=False, armed=True, loss=True, flag=1) == 16


def test_only_once_per_position():
    assert final_add(cur=-8, avg_max=40, prev_step=8, k_avg=2.0,
                     already=True, armed=True, loss=True, flag=1) == 0


def test_respects_the_ladder_cap():
    assert final_add(cur=-20, avg_max=20, prev_step=8, k_avg=2.0,
                     already=False, armed=True, loss=True, flag=1) == 0


def test_needs_a_stop_and_a_loss():
    assert final_add(cur=-8, avg_max=40, prev_step=8, k_avg=2.0,
                     already=False, armed=False, loss=True, flag=1) == 0
    assert final_add(cur=-8, avg_max=40, prev_step=8, k_avg=2.0,
                     already=False, armed=True, loss=False, flag=1) == 0


def test_off_by_default():
    assert final_add(cur=-8, avg_max=40, prev_step=8, k_avg=2.0,
                     already=False, armed=True, loss=True, flag=0) == 0
    assert REGISTRY["macd_shectory1"]["default_params"]["flip_add_max"] == 0
