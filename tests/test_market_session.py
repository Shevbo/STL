"""Оракул сессии MOEX FORTS — по ОФИЦИАЛЬНЫМ окнам ISS session_schedule.

Единый источник правды — расписание биржи (moex.com/s1167 / ISS session_schedule).
Решение об открытости — ТОЛЬКО попадание часов биржи (SYSTIME) в торговое окно, плюс
праздники dailytable. Никаких дней недели, эвристик или TRADE_SESSION_DATE (это дата
клиринга T+1 — на ней я дважды ошибся, см. историю).
"""
from __future__ import annotations

from trader.market_session import _parse_dt_ms, classify, codes_from_feed, parse_schedule


def _ms(day: str, hms: str) -> int:
    return _parse_dt_ms(day, hms)


def _win(day: str, f: str, t: str, typ: str) -> tuple[int, int, str]:
    return (_ms(day, f), _ms(day, t), typ)


# Реальная форма от ISS: выходной день = только weekend_session 10:00-19:00; будни =
# morning 07:00-10:00 + main 10:00-19:00 + evening 19:00-23:50.
_WEEKEND = {"sessions": [_win("2026-07-26", "10:00:00", "19:00:00", "weekend_session")],
            "holidays": set()}
_WEEKDAY = {"sessions": [
    _win("2026-07-27", "07:00:00", "10:00:00", "morning_session"),
    _win("2026-07-27", "10:00:00", "19:00:00", "main_session"),
    _win("2026-07-27", "19:00:00", "23:50:00", "evening_session"),
], "holidays": set()}


def test_weekend_session_is_open_midday():
    """РЕГРЕСС дня 2026-07-26 (вс): официально weekend_session 10:00-19:00, полдень => ОТКРЫТО.
    Раньше говорил «выходной, до понедельника» ПРЯМО ВО ВРЕМЯ торгов."""
    is_open, phase, stype, _ = classify(now_ms=_ms("2026-07-26", "12:10:00"), schedule=_WEEKEND)
    assert is_open is True
    assert phase == "trading"
    assert stype == "weekend"


def test_weekend_closed_after_1900():
    """РЕГРЕСС субботы: доп. сессия выходного закрывается 19:00 — в 19:23 уже ЗАКРЫТО
    (по расписанию, не по свежести SYSTIME)."""
    is_open, phase, _, _ = classify(now_ms=_ms("2026-07-26", "19:23:00"), schedule=_WEEKEND)
    assert is_open is False
    assert phase == "done"


def test_weekend_pre_open_before_1000():
    is_open, phase, _, nxt = classify(now_ms=_ms("2026-07-26", "08:30:00"), schedule=_WEEKEND)
    assert (is_open, phase) == (False, "pre_open")
    assert nxt == _ms("2026-07-26", "10:00:00")


def test_weekday_sessions_open_and_break():
    # Утро — открыто.
    o, ph, st, _ = classify(now_ms=_ms("2026-07-27", "08:00:00"), schedule=_WEEKDAY)
    assert (o, ph, st) == (True, "trading", "morning")
    # Основная — открыто.
    o, ph, st, _ = classify(now_ms=_ms("2026-07-27", "15:00:00"), schedule=_WEEKDAY)
    assert (o, ph, st) == (True, "trading", "main")
    # Вечерняя — открыто.
    o, ph, st, _ = classify(now_ms=_ms("2026-07-27", "22:00:00"), schedule=_WEEKDAY)
    assert (o, ph, st) == (True, "trading", "evening")
    # После 23:50 — закрыто.
    o, ph, _, _ = classify(now_ms=_ms("2026-07-27", "23:55:00"), schedule=_WEEKDAY)
    assert (o, ph) == (False, "done")


def test_holiday_from_dailytable_overrides():
    sched = {"sessions": _WEEKEND["sessions"], "holidays": {"2026-07-26"}}
    is_open, phase, _, _ = classify(now_ms=_ms("2026-07-26", "12:00:00"), schedule=sched)
    assert (is_open, phase) == (False, "holiday")


def test_holiday_check_uses_wall_clock_not_frozen_systime():
    """РЕГРЕСС 2026-08-01: биржа не торговала с вечера пятницы, SYSTIME (now_ms) всех
    инструментов синхронно застрял на пятнице 23:50:05 (момент клиринга) — а на дворе
    суббота, MOEX-исключение из сессии выходного дня (dailytable holiday). Без wall_ms
    holiday-чек сверяет ВЧЕРАШНЮЮ дату (не праздник) и молча проваливается в общий
    'done' вместо 'holiday' — тот же open=False здесь совпадает случайно, но при ином
    наборе окон next_open_ms посчитался бы неверно."""
    sched = {"sessions": [_win("2026-08-03", "07:00:00", "10:00:00", "morning_session")],
             "holidays": {"2026-08-01"}}
    is_open, phase, _, _ = classify(
        now_ms=_ms("2026-07-31", "23:50:05"),
        wall_ms=_ms("2026-08-01", "09:51:00"),
        schedule=sched)
    assert (is_open, phase) == (False, "holiday")


def test_unknown_without_schedule_or_time():
    assert classify(now_ms=0, schedule=_WEEKEND)[:2] == (None, "unknown")
    assert classify(now_ms=_ms("2026-07-26", "12:00:00"), schedule={"sessions": []})[:2] == (None, "unknown")


def test_parse_schedule_picks_trading_windows_and_holidays():
    # ФАКТИЧЕСКАЯ дата окна — в time_from/time_till, НЕ в trade_session_date (клиринг T+1).
    cols = ["trade_session_date", "boardid", "secid", "type", "time_from", "time_till", "updatetime"]
    rows = [
        ["2026-07-27", "", "", "weekend_session", "2026-07-26 10:00:00", "2026-07-26 19:00:00", ""],
        ["2026-07-27", "RFUD", "", "oa_booking", "2026-07-26 09:50:00", "2026-07-26 09:59:00", ""],
        ["2026-07-27", "", "", "clearing_session", "2026-07-26 23:50:00", None, ""],
    ]
    dcols = ["date", "is_work_day", "start_time", "stop_time"]
    drows = [["2026-08-01", 0, None, None], ["2026-07-27", 1, "07:00:00", "23:50:00"]]
    sch = parse_schedule(rows, cols, drows, dcols)
    assert sch["sessions"] == [_win("2026-07-26", "10:00:00", "19:00:00", "weekend_session")]
    assert sch["holidays"] == {"2026-08-01"}   # is_work_day=0; рабочая суббота (=1) НЕ праздник


def test_codes_follow_the_agent_feed():
    assert codes_from_feed([{"code": "RIU6"}, {"code": "BRU6"}]) == ["RIU6", "BRU6"]
    assert codes_from_feed([]) == ["RIU6", "SiU6"]
    assert codes_from_feed(None) == ["RIU6", "SiU6"]
