"""Снятый с торговли робот забывается целиком.

Баг: stop_robot убирал только задачу. Кэши состояния, скомпилированного кода и
последнего бара оставались, и повторный деплой поднимал робота с памятью прошлой
жизни:
  • сброс робота «на старт» не срабатывал — состояние стратегии бралось из памяти,
    а не перечитывалось из базы;
  • защита «первый тик только запоминает бар» на редеплое не работала, потому что
    робот числился уже виденным (03.08.2026, сброс «2EMA · MXU6»).
"""
import asyncio

from trader.lab.scheduler import RobotScheduler


def test_stop_clears_every_cache():
    s = RobotScheduler(db_pool=None)
    s._robot_states["r1"] = {"trend": 1}
    s._compiled["r1"] = (123, object())
    s._bar_gate("r1", 1000)
    assert s._last_bar.get("r1") == 1000

    asyncio.run(s.stop_robot("r1"))

    assert "r1" not in s._robot_states
    assert "r1" not in s._compiled
    assert "r1" not in s._last_bar


def test_after_stop_first_tick_only_remembers_again():
    # Ровно то, ради чего чистим: редеплой не должен исполняться сразу.
    s = RobotScheduler(db_pool=None)
    s._bar_gate("r1", 1000)
    assert s._bar_gate("r1", 1060) is True      # обычный ход
    asyncio.run(s.stop_robot("r1"))
    assert s._bar_gate("r1", 1120) is False     # снова первая встреча
    assert s._bar_gate("r1", 1180) is True


def test_stop_of_unknown_robot_is_quiet():
    s = RobotScheduler(db_pool=None)
    asyncio.run(s.stop_robot("нет такого"))     # не должно падать
