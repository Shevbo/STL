"""Дыра на мини-графике робота закрывается кэшем — и честно помечается.

25.09.2026 сбор данных стоял восемь торговых часов, раннер за них не построил ни
одной свечи, и график робота получил провал, который его собственными данными
закрыть нечем: их не существует."""

from unittest.mock import patch

from trader.api.quik_companion import _fill_chart_gaps

M = 60  # минута в секундах


def _bar(t, c=100.0):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": 1}


def _rows(times, price=200.0):
    return [[t, price, price, price, price, 7] for t in times]


def test_inner_gap_is_filled_from_the_cache_and_marked():
    tail = [_bar(0), _bar(M), _bar(4 * M), _bar(5 * M)]
    with patch("trader.api.quik_companion._agent_bars_rows_safe",
               return_value=_rows([2 * M, 3 * M])):
        out = _fill_chart_gaps(tail, "RIZ6", 10)
    assert [b["t"] for b in out] == [0, M, 2 * M, 3 * M, 4 * M, 5 * M]
    restored = [b for b in out if b.get("restored")]
    assert [b["t"] for b in restored] == [2 * M, 3 * M]
    # Восстановленные — это бары ИНСТРУМЕНТА: цена приходит из кэша, не из робота.
    assert restored[0]["c"] == 200.0


def test_robot_own_bars_are_never_overwritten():
    tail = [_bar(0), _bar(M, c=111.0), _bar(2 * M)]
    with patch("trader.api.quik_companion._agent_bars_rows_safe",
               return_value=_rows([M])):          # кэш знает ту же минуту
        out = _fill_chart_gaps(tail, "RIZ6", 10)
    assert len(out) == 3
    assert [b for b in out if b["t"] == M][0]["c"] == 111.0
    assert not any(b.get("restored") for b in out)


def test_history_outside_the_tail_is_not_invented():
    """Достраивать хвост за пределы того, что робот видел, значит выдумывать."""
    tail = [_bar(5 * M), _bar(6 * M)]
    with patch("trader.api.quik_companion._agent_bars_rows_safe",
               return_value=_rows([M, 2 * M, 9 * M, 10 * M])):
        out = _fill_chart_gaps(tail, "RIZ6", 10)
    assert [b["t"] for b in out] == [5 * M, 6 * M]


def test_no_cache_leaves_the_chart_as_is():
    tail = [_bar(0), _bar(3 * M)]
    with patch("trader.api.quik_companion._agent_bars_rows_safe", return_value=[]):
        assert _fill_chart_gaps(tail, "RIZ6", 10) == tail


def test_empty_tail_stays_empty():
    """Пустой график не превращаем в график инструмента: робот ничего не видел."""
    with patch("trader.api.quik_companion._agent_bars_rows_safe",
               return_value=_rows([M, 2 * M])):
        assert _fill_chart_gaps([], "RIZ6", 10) == []


def test_window_limit_is_respected():
    tail = [_bar(0), _bar(9 * M)]
    with patch("trader.api.quik_companion._agent_bars_rows_safe",
               return_value=_rows([i * M for i in range(1, 9)])):
        out = _fill_chart_gaps(tail, "RIZ6", 4)
    assert len(out) == 4 and out[-1]["t"] == 9 * M
