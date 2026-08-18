"""Сторож позиции: ловит расхождение веры робота с журналом сделок.

17.08.2026 раннер после перезапуска QUIK поднял веру из замёрзшего файла: считал
себя во флэте, а на бирже висел шорт 9 контрактов. Штатная сверка позицию не
сравнивает вовсе, и единственным детектором оказался человек с экселем.

Проверяем ровно то, что решает: расхождение видно, мгновенная гонка филла —
нет, бумажные роботы не мешают, а первый выход робота на биржу не считается
ошибкой.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from position_guard import believed_positions, compare  # noqa: E402


def test_paper_robots_are_ignored():
    """У бумажного робота нет биржи, сверять его позицию не с чем."""
    mirror = {"robots": [
        {"robot_id": "real1", "paper": False, "position": -9},
        {"robot_id": "paper1", "paper": True, "position": 4},
        {"robot_id": "real2", "paper": False},          # позиции нет = флэт
    ]}
    assert believed_positions(mirror) == {"real1": -9, "real2": 0}


def test_divergence_needs_two_consecutive_looks():
    """Между филлом и его попаданием в журнал есть секунды. Мгновенный снимок
    ловил бы эту гонку как ошибку, поэтому тревога — только на повторе."""
    believed, ledger = {"lxk22": 0}, {"lxk22": -9}
    first, state = compare(believed, ledger, {})
    assert first == [], "тревога с первого замера — это ложные срабатывания на гонке"
    second, _ = compare(believed, ledger, state)
    assert len(second) == 1
    key, text = second[0]
    assert key == "poscheck"
    assert "lxk22" in text and "-9" in text


def test_the_incident_numbers_are_in_the_alert():
    """Оператору нужны обе цифры и разница: по ним он правит веру, не считая
    в экселе заново."""
    state = {"bad": {"lxk22": [0, -9]}}
    problems, _ = compare({"lxk22": 0}, {"lxk22": -9}, state)
    text = problems[0][1]
    assert "верит +0" in text and "даёт -9" in text and "-9)" in text


def test_agreement_is_silent_and_clears_state():
    problems, state = compare({"a": 3}, {"a": 3}, {"bad": {"a": [0, 3]}})
    assert problems == []
    assert state["bad"] == {}, "расхождение ушло — состояние обязано очиститься"


def test_robot_without_ledger_rows_is_not_an_error():
    """Робот, который ещё не совершил ни одной сделки, не расхождение."""
    problems, _ = compare({"fresh": 0}, {}, {"bad": {"fresh": [0, 0]}})
    assert problems == []


def test_changed_divergence_restarts_the_confirmation():
    """Если расхождение ИЗМЕНИЛОСЬ, значит робот торгует и это не застывшая
    ошибка: подтверждение начинается заново."""
    problems, state = compare({"a": -1}, {"a": -9}, {"bad": {"a": [0, -9]}})
    assert problems == []
    assert state["bad"] == {"a": [-1, -9]}
