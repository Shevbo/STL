"""Сторож молчания агента.

20.08.2026 агент лежал с 02:04 до 09:09, из них два часа при открытой бирже, и
ни одной тревоги не ушло: рекон следит за расхождением книг и молчит, когда книг
нет вовсе. Эти проверки прибивают оба конца — и что тревога поднимается при
открытом рынке, и что она НЕ поднимается при закрытом."""

from trader.quik.agent_watch import silence_verdict

NOW = 1_787_000_000_000


def agent(age_sec):
    return [{"agent_id": "9618", "last_seen_ms": NOW - age_sec * 1000}]


def test_silence_while_market_trades_is_an_alarm():
    bad, age = silence_verdict(agent(600), True, now_ms=NOW)
    assert bad and age == 600


def test_fresh_link_is_quiet():
    bad, _ = silence_verdict(agent(30), True, now_ms=NOW)
    assert not bad


def test_closed_market_never_alarms():
    bad, _ = silence_verdict(agent(7 * 3600), False, now_ms=NOW)
    assert not bad


def test_unknown_market_state_alarms_protectively():
    """ISS недоступен: неизвестность не повод считать биржу закрытой."""
    bad, _ = silence_verdict(agent(600), None, now_ms=NOW)
    assert bad


def test_no_agents_at_all_is_silence():
    bad, _ = silence_verdict([], True, now_ms=NOW)
    assert bad


def test_freshest_agent_wins():
    two = agent(600) + [{"agent_id": "x", "last_seen_ms": NOW - 10_000}]
    bad, age = silence_verdict(two, True, now_ms=NOW)
    assert not bad and age == 10
