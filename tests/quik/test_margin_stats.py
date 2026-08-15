"""Множитель брокера над биржевым ГО: мерим, а не угадываем.

Константа 2.4 сидит в отборе кандидатов, в отчёте компаньона и в карточке
робота. Если она врёт, врут все три — поэтому замер обязан либо быть честным,
либо не появляться вовсе.
"""
from trader.margin_stats import compute, summarize

MSK = 3 * 3600 * 1000
RI = 22187.11


def test_plain_multiplier():
    s = compute(1_786_000_000_000, 133590.0, {"RIU6": 3}, {"RIU6": RI})
    assert s is not None
    assert s.multiplier == round(133590.0 / (3 * RI), 4)
    assert s.positions == {"RIU6": 3}


def test_short_position_counts_by_absolute_size():
    """ГО берут и за шорт: знак позиции на требование не влияет."""
    a = compute(1, 100000.0, {"RIU6": 2}, {"RIU6": RI})
    b = compute(1, 100000.0, {"RIU6": -2}, {"RIU6": RI})
    assert a.multiplier == b.multiplier


def test_unknown_instrument_kills_the_sample():
    """Одна бумага без известного ГО занижает знаменатель, и множитель взлетает
    на ровном месте. Такой замер не пишем вовсе."""
    assert compute(1, 200000.0, {"RIU6": 2, "GDU6": 1}, {"RIU6": RI}) is None


def test_no_position_no_sample():
    assert compute(1, 0.0, {"RIU6": 3}, {"RIU6": RI}) is None
    assert compute(1, 133590.0, {}, {"RIU6": RI}) is None
    assert compute(1, 133590.0, {"RIU6": 0}, {"RIU6": RI}) is None


def test_absurd_values_are_dropped():
    """Меньше единицы счёт платить не может, десятикратное — заведомый абсурд.
    Одна кривая точка портит медиану сильнее, чем её отсутствие."""
    assert compute(1, 1000.0, {"RIU6": 3}, {"RIU6": RI}) is None       # 0.015
    assert compute(1, 9_000_000.0, {"RIU6": 3}, {"RIU6": RI}) is None  # 135


def test_broken_input_never_raises():
    assert compute(1, None, {"RIU6": 3}, {"RIU6": RI}) is None
    assert compute(1, "x", {"RIU6": 3}, {"RIU6": RI}) is None
    assert compute(1, 133590.0, {"RIU6": "три"}, {"RIU6": RI}) is None
    assert compute(1, 133590.0, {"RIU6": 3}, {"RIU6": "нет"}) is None


def test_summary_uses_median_not_mean():
    """Один выброс в момент клиринга не должен двигать ответ на вопрос
    «гуляет ли множитель по времени дня»."""
    rows = [{"ts_ms": 0, "multiplier": 2.4} for _ in range(9)]
    rows.append({"ts_ms": 0, "multiplier": 9.0})
    s = summarize(rows)
    assert s["n"] == 10 and s["median"] == 2.4 and s["max"] == 9.0


def test_summary_groups_by_msk_hour():
    """Час считается по МСК: вопрос был про время торгового дня, а не UTC."""
    rows = [{"ts_ms": 10 * 3600 * 1000 - MSK, "multiplier": 2.0},   # 10:00 МСК
            {"ts_ms": 10 * 3600 * 1000 - MSK + 60_000, "multiplier": 3.0},
            {"ts_ms": 20 * 3600 * 1000 - MSK, "multiplier": 4.0}]   # 20:00 МСК
    s = summarize(rows)
    assert set(s["by_hour"]) == {10, 20}
    assert s["by_hour"][10] == {"n": 2, "median": 2.5}
    assert s["by_hour"][20] == {"n": 1, "median": 4.0}


def test_empty_summary_is_not_a_zero():
    """Пусто — это «не мерили», а не «множитель ноль»."""
    s = summarize([])
    assert s["n"] == 0 and s["median"] is None and s["by_hour"] == {}
