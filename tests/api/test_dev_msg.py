"""Почта между окнами: выборка ящика и микропромпт.

Транспорт (Postgres/HTTP) не трогаем — вся логика доставки вынесена в чистую
`filter_inbox`, и проверяется именно она: письмо, ушедшее не тому окну, никто
никогда не прочитает, а отправитель будет считать работу переданной.
"""
import pytest

from trader.api.dev_msg import AGENTS, filter_inbox, inbox_prompt


def _m(frm, to, i, read=0):
    return {"id": str(i), "from": frm, "to": to, "body": "x",
            "created_ms": i, "read_ms": read}


def test_inbox_takes_personal_and_broadcast_but_not_own_broadcast():
    rows = [
        _m("ui-ux", "real-trade", 1),        # лично мне
        _m("backtests", "all", 2),           # всем — мне тоже
        _m("real-trade", "all", 3),          # МОЙ же бродкаст — не мне
        _m("ui-ux", "backtests", 4),         # чужое личное
    ]
    got = [m["id"] for m in filter_inbox(rows, "real-trade", unread_only=True)]
    assert got == ["1", "2"]


def test_unread_filter_and_time_order():
    rows = [_m("ui-ux", "real-trade", 30), _m("ui-ux", "real-trade", 10, read=99),
            _m("ui-ux", "real-trade", 20)]
    assert [m["id"] for m in filter_inbox(rows, "real-trade", unread_only=True)] == ["20", "30"]
    assert [m["id"] for m in filter_inbox(rows, "real-trade", unread_only=False)] == ["10", "20", "30"]


def test_case_insensitive_addressing():
    rows = [_m("UI-UX", "Real-Trade", 1)]
    assert len(filter_inbox(rows, "real-trade", unread_only=True)) == 1


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_prompt_names_the_zone_and_the_ack_step(agent):
    """Микропромпт едет внутри ответа: свежая сессия узнаёт протокол из него,
    а не из документации, которую могла не прочитать."""
    p = inbox_prompt(agent)
    assert agent in p
    assert AGENTS[agent][:20] in p          # своя зона названа
    assert "ack" in p                        # подтверждение обязательно
    assert "чуж" in p                        # правило про чужую зону
