"""Оракул сессии MOEX — по СВЕЖЕСТИ/ДВИЖЕНИЮ последней сделки, НЕ по TRADE_SESSION_DATE.

Урок 2026-07-26 (дорогая ошибка): TRADE_SESSION_DATE — дата КЛИРИНГА (T+1). Во время
активных торгов она ВСЕГДА завтрашняя. Прошлая логика `session_date > today → закрыто`
говорила «биржа закрыта, курите до понедельника» ПРЯМО ВО ВРЕМЯ ТОРГОВ на миллиарды.
Открытость решает: (1) сдвинулся ли TIME между опросами, (2) свежесть с поправкой на
задержку публичного ISS ~16 мин. session_date в решении НЕ участвует.
"""
from __future__ import annotations

from trader.market_session import _parse_dt_ms, classify, codes_from_feed


def _ms(day: str, hms: str) -> int:
    return _parse_dt_ms(day, hms)


def test_clearing_date_tomorrow_does_not_mean_closed():
    """РЕГРЕСС дня 2026-07-26: торгуют СЕЙЧАС, TRADE_SESSION_DATE уже завтрашнее (клиринг
    T+1). Свежая сделка (lag ~16 мин из-за задержки ISS) => ОТКРЫТО, несмотря на дату."""
    is_open, phase, _ = classify(
        today="2026-07-26", session_date="2026-07-27",
        systime_ms=_ms("2026-07-26", "12:10:00"), trade_ms=_ms("2026-07-26", "11:54:00"))
    assert is_open is True
    assert phase == "trading"


def test_trading_via_time_advancement():
    # TIME сдвинулся с прошлого опроса — сделки идут, даже если lag не мелкий.
    is_open, phase, _ = classify(
        today="2026-07-26", session_date="2026-07-27",
        systime_ms=_ms("2026-07-26", "12:11:00"), trade_ms=_ms("2026-07-26", "11:55:00"),
        prev_trade_ms=_ms("2026-07-26", "11:54:00"))
    assert (is_open, phase) == (True, "trading")


def test_closed_detected_by_frozen_stale_time_not_session_date():
    """Субботний прокол наоборот: после закрытия TIME ЗАМЕР (== прошлому опросу) и lag
    большой — ЗАКРЫТО. Определяется по застывшей сделке, а НЕ по session_date."""
    frozen = _ms("2026-07-25", "19:00:00")
    is_open, phase, _ = classify(
        today="2026-07-25", session_date="2026-07-27",
        systime_ms=_ms("2026-07-25", "19:35:00"), trade_ms=frozen, prev_trade_ms=frozen)
    assert is_open is False
    assert phase == "done"


def test_clearing_break_is_not_open_but_not_done():
    # Клиринг: TIME замер, lag умеренный (в пределах получаса) — пауза, не торги, не закрытие.
    frozen = _ms("2026-07-24", "13:44:00")
    is_open, phase, _ = classify(
        today="2026-07-24", session_date="2026-07-25",
        systime_ms=_ms("2026-07-24", "14:04:00"), trade_ms=frozen, prev_trade_ms=frozen)
    assert is_open is False
    assert phase == "break"


def test_pre_open_morning():
    # Утро торгового дня: сделок ещё не было.
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
        today="2026-07-26", session_date="", systime_ms=0, trade_ms=0)
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
