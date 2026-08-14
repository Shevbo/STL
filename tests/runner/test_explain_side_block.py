"""Консоль обязана объяснять ЗАПРЕТ СТОРОНЫ, а не обещать заявку, которой не будет.

14.08.2026, живой шортовый робот agent-macdshort-RIU6-v1: сигнал ЛОНГ, в плане
«купить 1 контракт», а купить он не может по построению (allow_long=0). Оператор
решил, что робот сломался и не пошёл в торговлю. Тот же класс ложного следа, из-за
которого 05.08 разножку перестали вешать на разворот: карточка объявляет план,
который гейт не пустит, и разбор уходит не туда.
"""
from types import SimpleNamespace

from robot_runner.explain import side_block

DAY0 = 1786579200          # 2026-08-13 00:00 UTC


def _bars(n=5, t0=DAY0):
    return [SimpleNamespace(open=100.0, high=101.0, low=99.0, close=100.0,
                            volume=1, time=t0 + i * 60) for i in range(n)]


def test_no_block_when_both_sides_allowed():
    assert side_block(1, {}, DAY0) == ""
    assert side_block(-1, {"allow_long": 1, "allow_short": 1}, DAY0) == ""


def test_long_signal_on_a_short_only_robot():
    msg = side_block(1, {"allow_long": 0, "allow_short": 1}, DAY0)
    assert "только шортит" in msg


def test_short_signal_on_a_long_only_robot():
    assert "только лонгует" in side_block(-1, {"allow_short": 0}, DAY0)


def test_flat_and_absent_signals_are_never_blocked():
    for want in (0, None):
        assert side_block(want, {"allow_long": 0, "allow_short": 0}, DAY0) == ""


def test_schedule_window_is_named_in_the_reason():
    p = {"tod_m1": 671, "tod_m2": 1140, "tod_s1": 1, "tod_s2": 2, "tod_s3": 3}
    # 10:00 — первое окно, разрешён только лонг.
    assert side_block(-1, p, DAY0 + 600 * 60).startswith("расписание: в 1-м окне шорт")
    assert side_block(1, p, DAY0 + 600 * 60) == ""
    # 12:00 — второе окно, только шорт.
    assert "в 2-м окне лонг" in side_block(1, p, DAY0 + 720 * 60)
    # 20:00 — третье окно, обе стороны.
    assert side_block(1, p, DAY0 + 1200 * 60) == ""
    assert side_block(-1, p, DAY0 + 1200 * 60) == ""


def test_schedule_uses_bar_offset_like_the_strategy():
    """Бары раннера истинно UTC: без смещения окно поедет на три часа."""
    p = {"tod_m1": 671, "tod_m2": 1140, "tod_s1": 1, "tod_s2": 2, "tod_s3": 3,
         "bar_offset_min": 180}
    # Бар 09:00 UTC = 12:00 МСК: со смещением это ВТОРОЕ окно (только шорт).
    assert "в 2-м окне лонг" in side_block(1, p, DAY0 + 540 * 60)
    # Без смещения тот же бар считался бы первым окном, где лонг разрешён.
    assert side_block(1, {k: v for k, v in p.items() if k != "bar_offset_min"},
                      DAY0 + 540 * 60) == ""


def test_day_ban_wins_over_the_schedule():
    """Запрет на весь день сильнее любого окна — как и в самой стратегии."""
    p = {"allow_long": 0, "tod_m1": 671, "tod_m2": 1140,
         "tod_s1": 3, "tod_s2": 3, "tod_s3": 3}
    assert "только шортит" in side_block(1, p, DAY0 + 600 * 60)


def test_broken_schedule_is_ignored():
    """Границы не по возрастанию = расписание выключено, как в make_on_bar."""
    p = {"tod_m1": 1140, "tod_m2": 671, "tod_s1": 2, "tod_s2": 2, "tod_s3": 2}
    assert side_block(1, p, DAY0 + 600 * 60) == ""
