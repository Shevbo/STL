"""Расписание сторон не может уехать на три часа.

18.08.2026: lxk22 торговал с tod_* без bar_offset_min. Бары раннера в истинном
UTC, окно «после 18:00 только лонг» стояло на 15:00 МСК, и робот набрал в
запрещённое окно 19 контрактов шорта против роста. Стоп отработал, цена
ошибки 28 939 рублей. Правило жило в документации — теперь в коде."""
from robot_runner.host import fix_wall_clock


def test_schedule_without_offset_is_repaired():
    p = fix_wall_clock({"tod_m1": 600, "tod_m2": 1080})
    assert p["bar_offset_min"] == 180


def test_us_open_hour_counts_as_wall_clock():
    assert fix_wall_clock({"open_hour": 16})["bar_offset_min"] == 180


def test_explicit_value_is_never_overwritten():
    p = fix_wall_clock({"tod_m1": 600, "bar_offset_min": 0})
    assert p["bar_offset_min"] == 0


def test_strategy_without_clock_gets_nothing():
    assert "bar_offset_min" not in fix_wall_clock({"fast": 57, "slow": 48})


def test_callback_fires_only_on_repair():
    seen = []
    fix_wall_clock({"tod_m1": 600}, lambda: seen.append(1))
    fix_wall_clock({"fast": 5}, lambda: seen.append(1))
    assert len(seen) == 1
