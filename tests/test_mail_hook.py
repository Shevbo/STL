"""Хук почты окна: показывает, но не генерирует лишнего.

Проверяем то, что ломается незаметно и дорого: письмо печатается ровно один раз,
напоминание не спамит на каждый ввод, пустой ящик стоит 0 токенов, сломанный транспорт
не выглядит как пустой ящик, и подсказка про запрет ответа на ответ всегда при письме.
"""
from scripts.mail_hook import NO_LOOP, REMIND_EVERY_S, render

W = "backtests"


def _inbox(*ids):
    return {"prompt": "ты окно", "messages": [
        {"id": i, "from": "real-trade", "topic": "t", "body": "b", "read_ms": None} for i in ids]}


def test_empty_inbox_is_silent():
    assert render(_inbox(), {}, "UserPromptSubmit", 1000.0, W) == []


def test_new_letter_shown_once_with_no_loop_rule():
    st = {}
    first = render(_inbox("a"), st, "UserPromptSubmit", 1000.0, W)
    assert any("[id a]" in x for x in first) and NO_LOOP in first
    assert render(_inbox("a"), st, "UserPromptSubmit", 1010.0, W) == []


def test_unacked_reminder_is_throttled():
    st = {}
    render(_inbox("a"), st, "UserPromptSubmit", 1000.0, W)
    assert render(_inbox("a"), st, "UserPromptSubmit", 1000.0 + REMIND_EVERY_S - 1, W) == []
    later = render(_inbox("a"), st, "UserPromptSubmit", 1000.0 + REMIND_EVERY_S, W)
    assert len(later) == 1 and "не подтверждено" in later[0]


def test_session_start_reshows_everything_unread():
    st = {}
    render(_inbox("a"), st, "UserPromptSubmit", 1000.0, W)
    again = render(_inbox("a"), st, "SessionStart", 1001.0, W)
    assert any("[id a]" in x for x in again)


def test_dead_transport_is_not_silent_but_throttled():
    st = {}
    out = render(None, st, "UserPromptSubmit", 1000.0, W, err="TimeoutExpired")
    assert out and "НЕ проверен" in out[0]
    assert render(None, st, "UserPromptSubmit", 1001.0, W, err="TimeoutExpired") == []


def test_acked_letter_forgotten():
    st = {}
    render(_inbox("a"), st, "UserPromptSubmit", 1000.0, W)
    render(_inbox(), st, "UserPromptSubmit", 1001.0, W)
    assert st["shown"] == []
