"""Выбор модели: цепочка профилей по уровню задачи.

Смысл проверки не в экономии, а в безопасности. Механическую задачу не жалко
отдать дешёвой модели: ошибка видна сразу. Задача, трогающая смысл, не должна
молча уехать на другую модель — правдоподобный результат, который некому
проверить, это ровно тот класс ошибки, который 17.08 стоил дня разбора.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from devworker import ALLOWED_TIERS, profile_chain  # noqa: E402

PROFILES = {"claude": {"cmd": "claude"}, "kimi": {"cmd": "claude", "model": "kimi"}}
TIERS = {"mechanical": ["claude", "kimi"], "standard": ["claude"]}


def test_mechanical_may_fall_back_but_standard_may_not():
    assert profile_chain("mechanical", PROFILES, TIERS) == ["claude", "kimi"]
    assert profile_chain("standard", PROFILES, TIERS) == ["claude"]


def test_unconfigured_profile_is_skipped_silently():
    """Профиль без команды означает «ещё не настроен», а не поломку: цепочка
    обязана продолжать работать на том, что есть."""
    half = {"claude": {"cmd": "claude"}, "kimi": {}}
    assert profile_chain("mechanical", half, TIERS) == ["claude"]


def test_forced_profile_wins_and_unknown_gives_nothing():
    assert profile_chain("standard", PROFILES, TIERS, only="kimi") == ["kimi"]
    assert profile_chain("standard", PROFILES, TIERS, only="нет-такого") == []


def test_unknown_tier_falls_to_claude_not_to_cheapest():
    """Неизвестный уровень — это опечатка. Она обязана вести к САМОЙ надёжной
    модели, а не к самой дешёвой."""
    assert profile_chain("опечатка", PROFILES, TIERS) == ["claude"]


def test_trading_tier_is_not_executable_at_all():
    assert "trading" not in ALLOWED_TIERS
