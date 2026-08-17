"""Почта между окнами: выборка ящика и микропромпт.

Транспорт (Postgres/HTTP) не трогаем — вся логика доставки вынесена в чистую
`filter_inbox`, и проверяется именно она: письмо, ушедшее не тому окну, никто
никогда не прочитает, а отправитель будет считать работу переданной.
"""
import pytest

from trader.api.dev_msg import (AGENTS, _STALE_MS, box_state, filter_inbox,
                                inbox_prompt, stale_note)


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


def test_box_state_counts_unread_and_measures_the_oldest():
    now = 100 * _STALE_MS
    rows = [_m("ui-ux", "real-trade", now - _STALE_MS * 2),      # просрочено
            _m("ui-ux", "real-trade", now - 1000),               # свежее
            _m("ui-ux", "real-trade", now - _STALE_MS * 9, read=now)]  # прочитано
    s = box_state(rows, "real-trade", now)
    assert s["unread"] == 2                    # прочитанное не считается
    assert s["stale"] is True                  # судим по САМОМУ СТАРОМУ
    assert s["oldest_age_h"] == pytest.approx(8.0, abs=0.1)


def test_box_state_is_quiet_when_nothing_waits():
    """Пустой и свежий ящик не должны выглядеть тревожно: иначе предупреждение
    обесценится и его перестанут читать."""
    now = 100 * _STALE_MS
    assert box_state([], "real-trade", now) == {
        "agent": "real-trade", "unread": 0, "oldest_age_h": 0.0, "stale": False}
    fresh = box_state([_m("ui-ux", "real-trade", now - 1000)], "real-trade", now)
    assert fresh["unread"] == 1 and fresh["stale"] is False
    assert stale_note(fresh) == ""


def test_stale_note_escalates_to_the_operator_and_forbids_doing_it_yourself():
    """Смысл тревоги: софт НЕ МОЖЕТ открыть окно Claude, поэтому единственный
    рабочий выход — сказать оператору. И прямо запретить обход: 11.08.2026
    письмо пролежало 7 часов, а реакцией было «сделаю сам» — это прячет сбой."""
    now = 100 * _STALE_MS
    s = box_state([_m("ui-ux", "backtests", now - _STALE_MS * 2)], "backtests", now)
    note = stale_note(s)
    assert "backtests" in note
    assert "ОПЕРАТОРУ" in note
    assert "молча" in note                     # запрет тихо сделать чужую работу


def test_stale_letter_is_not_hidden_by_a_fresher_one():
    """Ящик судится по САМОМУ СТАРОМУ письму, а не по последнему. Иначе окно,
    которому только что написали, выглядит здоровым, хотя внизу лежит блокер."""
    now = 100 * _STALE_MS
    rows = [_m("ui-ux", "real-trade", now - _STALE_MS * 8),
            _m("backtests", "real-trade", now - 60_000)]
    s = box_state(rows, "real-trade", now)
    assert s["unread"] == 2 and s["stale"] is True


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_prompt_names_the_zone_and_the_ack_step(agent):
    """Микропромпт едет внутри ответа: свежая сессия узнаёт протокол из него,
    а не из документации, которую могла не прочитать."""
    p = inbox_prompt(agent)
    assert agent in p
    assert AGENTS[agent][:20] in p          # своя зона названа
    assert "ack" in p                        # подтверждение обязательно
    assert "чуж" in p                        # правило про чужую зону


# ── предохранитель от почтовой петли (17.08.2026) ────────────────────────────

def _auto(frm, to, ts):
    return {"id": str(ts), "from": frm, "to": to, "topic": "получено автоответчиком",
            "body": "принято", "created_ms": ts, "read_ms": 0}


def test_auto_topic_is_recognised():
    from trader.api.dev_msg import is_auto_topic
    assert is_auto_topic("получено автоответчиком")
    assert is_auto_topic("  Получено Автоответчиком ")
    assert is_auto_topic("auto: доставлено")
    assert not is_auto_topic("ИНЦИДЕНТ: раннер потерял 3 контракта")
    assert not is_auto_topic("")


def test_second_receipt_to_the_same_window_is_dropped():
    """Петля 17.08 крутилась по письму каждые 2.9 минуты — окно дедупа 10 минут
    гасит её, не мешая редким настоящим квитанциям."""
    from trader.api.dev_msg import auto_is_duplicate
    now = 10_000_000
    rows = [_auto("backtests", "real-trade", now - 60_000)]
    assert auto_is_duplicate(rows, "backtests", "real-trade", now)
    # встречное направление — своя пара, гасится отдельно
    assert not auto_is_duplicate(rows, "real-trade", "backtests", now)
    # старая квитанция петлёй не считается
    assert not auto_is_duplicate([_auto("backtests", "real-trade", now - 3_600_000)],
                                 "backtests", "real-trade", now)


def test_real_letters_are_never_treated_as_receipts():
    """Главный риск предохранителя — проглотить настоящее письмо."""
    from trader.api.dev_msg import auto_is_duplicate
    now = 10_000_000
    real = {"id": "1", "from": "backtests", "to": "real-trade",
            "topic": "ОПЕРАТОР РАЗРЕШИЛ АРМИНГ", "body": "x",
            "created_ms": now - 60_000, "read_ms": 0}
    assert not auto_is_duplicate([real], "backtests", "real-trade", now)


def test_receipt_never_becomes_fuel():
    """Квитанция кладётся ПРОЧИТАННОЙ: бот пропускает прочитанное, и следующего
    круга не будет. Это и есть точка, где петля умирает."""
    from trader.api.dev_msg import filter_inbox, is_auto_topic
    now = 10_000_000
    receipt = dict(_auto("backtests", "real-trade", now), read_ms=now)
    assert is_auto_topic(receipt["topic"])
    assert filter_inbox([receipt], "real-trade", unread_only=True) == []
