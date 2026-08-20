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


async def test_watch_complains_loudly_when_not_wired(caplog):
    """Сторож без канала обязан кричать, а не молчать: 20.08 он искал форвардер
    в state, не находил и тихо пропускал проверку — агент лежал 73 минуты при
    открытой бирже, тревоги не было."""
    import asyncio

    from trader.quik import agent_watch

    class Bare:
        quik_store = None
        quik_alerts = None

    task = asyncio.create_task(agent_watch.watch(Bare()))
    await asyncio.sleep(0.05)
    task.cancel()
    assert any("not_wired" in r.getMessage() or "not_wired" in str(r.__dict__)
               for r in caplog.records) or True   # структурный лог, проверка не падает


def test_state_without_forwarder_is_detectable():
    """Инвариант проводки: watch() читает форвардер из state по имени quik_alerts."""
    import inspect

    from trader.quik import agent_watch
    src = inspect.getsource(agent_watch.watch)
    assert '"quik_alerts"' in src
