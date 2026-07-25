"""Оракул сессии MOEX v2 — по ОФИЦИАЛЬНЫМ полям ISS.

Закреплён урок 2026-07-25: SYSTIME — часы сервера ISS, они тикают и после
закрытия; открытость решает связка TRADE_SESSION_DATE + свежесть последней
сделки (TIME). Первая версия по «now vs SYSTIME» показывала «торги идут» в
субботу после закрытия выходной сессии в 19:00.
"""
from __future__ import annotations

from trader.market_session import _parse_dt_ms, classify, codes_from_feed


def _ms(day: str, hms: str) -> int:
    return _parse_dt_ms(day, hms)


def test_trading_now():
    # Сессия объявлена на сегодня, последняя сделка 20 секунд назад.
    is_open, phase, lag = classify(
        today="2026-07-25", session_date="2026-07-25",
        systime_ms=_ms("2026-07-25", "15:00:20"), trade_ms=_ms("2026-07-25", "15:00:00"))
    assert (is_open, phase, lag) == (True, "trading", 20)


def test_saturday_after_1900_close_is_done_even_with_fresh_systime():
    """Ровно сегодняшний прокол: 19:23 сб, SYSTIME свежий (сервер жив), но биржа
    уже объявила следующей сессией понедельник — торги ЗАКРЫТЫ."""
    is_open, phase, _ = classify(
        today="2026-07-25", session_date="2026-07-27",
        systime_ms=_ms("2026-07-25", "19:22:48"), trade_ms=_ms("2026-07-25", "18:59:55"))
    assert is_open is False
    assert phase == "done"


def test_clearing_break_reads_as_break_not_failure():
    # Сессия сегодня, сделок нет 4 минуты (клиринг 14:00-14:05): пауза, не торги.
    is_open, phase, lag = classify(
        today="2026-07-24", session_date="2026-07-24",
        systime_ms=_ms("2026-07-24", "14:04:00"), trade_ms=_ms("2026-07-24", "14:00:00"))
    assert is_open is False
    assert phase == "break"
    assert lag == 240


def test_pre_open_morning():
    # Утро торгового дня: сессия объявлена, сделок ещё нет.
    is_open, phase, _ = classify(
        today="2026-07-27", session_date="2026-07-27",
        systime_ms=_ms("2026-07-27", "06:45:00"), trade_ms=0)
    assert (is_open, phase) == (False, "pre_open")


def test_official_holiday_from_dailytable():
    # 01.08.2026 в dailytable помечен is_work_day=0 — официальный выходной.
    is_open, phase, _ = classify(
        today="2026-08-01", session_date="2026-08-03",
        systime_ms=_ms("2026-08-01", "12:00:00"), trade_ms=_ms("2026-07-31", "23:49:00"),
        holiday=True)
    assert is_open is False
    assert phase == "holiday"


def test_unknown_when_iss_silent():
    is_open, phase, _ = classify(
        today="2026-07-25", session_date="", systime_ms=0, trade_ms=0)
    assert is_open is None
    assert phase == "unknown"


def test_codes_follow_the_agent_feed():
    assert codes_from_feed([{"code": "RIU6"}, {"code": "BRU6"}]) == ["RIU6", "BRU6"]
    assert codes_from_feed([]) == ["RIU6", "SiU6"]
    assert codes_from_feed(None) == ["RIU6", "SiU6"]


def test_parse_dt_ms_rejects_junk():
    assert _parse_dt_ms("", "") == 0
    assert _parse_dt_ms("2026-07-25", "не время") == 0
    assert _parse_dt_ms("2026-07-25", "18:59:55") > 0
