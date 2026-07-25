"""Оракул сессии MOEX: SYSTIME -> открыт/закрыт. Пограничные случаи закреплены,
потому что от этого зависит, паузить ли вотчеру РЕАЛЬНЫХ роботов."""
from __future__ import annotations

from trader.market_session import (
    _parse_systime_ms,
    classify,
    codes_from_feed,
)

MSK_OFFSET = 3 * 3600 * 1000


def _ms(s: str) -> int:
    return _parse_systime_ms(s)


def test_fresh_systime_is_open():
    now = _ms("2026-07-24 15:00:00")
    # SYSTIME отстаёт на 10с — торги идут.
    is_open, lag = classify(_ms("2026-07-24 14:59:50"), now)
    assert is_open is True
    assert lag == 10


def test_stale_systime_is_closed():
    now = _ms("2026-07-25 07:46:00")
    # SYSTIME замер на Пт 23:50 — почти 8ч, рынок закрыт (ровно сегодняшний случай).
    is_open, lag = classify(_ms("2026-07-24 23:50:15"), now)
    assert is_open is False
    assert 7 * 3600 < lag < 9 * 3600


def test_clearing_pause_reads_as_closed_and_that_is_correct():
    # Внутридневной клиринг заморозил SYSTIME на 5 мин: сделок нет, «закрыто» —
    # верное чтение, тревогу по замершей ленте в клиринг глушим осознанно.
    now = _ms("2026-07-24 14:03:00")
    is_open, _ = classify(_ms("2026-07-24 13:58:00"), now)
    assert is_open is False


def test_clock_ahead_still_open():
    # Часы биржи ушли вперёд на 20с (дрейф) — модуль по-прежнему «открыто»,
    # а не «закрыто» из-за знака.
    now = _ms("2026-07-24 15:00:00")
    is_open, lag = classify(_ms("2026-07-24 15:00:20"), now)
    assert is_open is True
    assert lag == 20


def test_unknown_systime_is_none():
    is_open, lag = classify(0, _ms("2026-07-24 15:00:00"))
    assert is_open is None
    assert lag == 0


def test_bad_systime_string_parses_to_zero():
    assert _parse_systime_ms("") == 0
    assert _parse_systime_ms("не дата") == 0
    assert _parse_systime_ms("2026-07-24 23:50:15") > 0


def test_codes_follow_the_agent_feed():
    feed = [{"code": "RIU6"}, {"code": "BRU6"}, {"code": ""}]
    assert codes_from_feed(feed) == ["RIU6", "BRU6"]
    # Нет фида -> запасные, а не пусто (иначе оракул не по чему спрашивать).
    assert codes_from_feed([]) == ["RIU6", "SiU6"]
    assert codes_from_feed(None) == ["RIU6", "SiU6"]
