"""Бумажный робот исполняется ТОЛЬКО на новом баре.

Баг: планировщик Лаборатории тикает по настенным часам раз в минуту и запускал
on_bar просто потому, что время внутри окна. Когда торгов нет, get_bars отдаёт
последний доступный бар, стратегия пересчитывалась на нём снова и снова и могла
выставить заявку. 02.08.2026 бумажный «2EMA · MXU6» продал в воскресенье 19:23
МСК по бару сорокачасовой давности — биржа в те выходные MXU6 не торговала.

Судим ПО ДАННЫМ, а не по календарю: FORTS торгует и в выходные (25-26.07 по MXU6
было 475 и 533 бара), поэтому день недели ничего не значит, а отсутствие нового
бара значит всё.
"""
from trader.lab.scheduler import RobotScheduler, _unwrap_json


def _sched() -> RobotScheduler:
    return RobotScheduler(db_pool=None)


def test_first_sight_only_remembers():
    # Иначе рестарт STL в выходной сразу отработал бы по пятничному бару.
    s = _sched()
    assert s._bar_gate("r1", 1000) is False


def test_same_bar_never_runs_twice():
    s = _sched()
    s._bar_gate("r1", 1000)
    assert s._bar_gate("r1", 1000) is False
    assert s._bar_gate("r1", 1000) is False


def test_new_bar_runs():
    s = _sched()
    s._bar_gate("r1", 1000)
    assert s._bar_gate("r1", 1060) is True
    assert s._bar_gate("r1", 1120) is True


def test_older_bar_never_runs():
    # Откат ленты назад (переподключение к ISS) не должен переисполнять историю.
    s = _sched()
    s._bar_gate("r1", 1000)
    s._bar_gate("r1", 1060)
    assert s._bar_gate("r1", 1000) is False


def test_no_bars_means_no_trading():
    s = _sched()
    s._bar_gate("r1", 1000)
    assert s._bar_gate("r1", None) is False       # ISS молчит -> не торгуем
    assert s._bar_gate("r1", 1060) is True        # данные вернулись -> работаем


def test_robots_are_independent():
    s = _sched()
    s._bar_gate("r1", 1000)
    s._bar_gate("r2", 1000)
    assert s._bar_gate("r1", 1060) is True
    assert s._bar_gate("r2", 1060) is True


class TestUnwrapJson:
    """Параметры робота читаются, сколько бы слоёв кодировки на них ни налипло."""

    def test_plain_dict(self):
        assert _unwrap_json({"symbol": "RIU6"}) == {"symbol": "RIU6"}

    def test_one_layer(self):
        assert _unwrap_json('{"symbol": "RIU6"}') == {"symbol": "RIU6"}

    def test_three_layers(self):
        # Ровно то, что накопили два ролла подряд: строка в строке в строке.
        assert _unwrap_json('"{\\"symbol\\": \\"BMQ6\\"}"') == {"symbol": "BMQ6"}

    def test_garbage_is_empty_not_crash(self):
        assert _unwrap_json("не json") == {}
        assert _unwrap_json(None) == {}
        assert _unwrap_json(42) == {}
