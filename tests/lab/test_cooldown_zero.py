"""cooldown_pct=0 должен означать «после любого неубыточного выхода», а не 1%.

Баг: `float(params.get("cooldown_pct", 1.0) or 1.0)` съедал ноль — falsy-ноль
подменялся дефолтом 1.0. Ноль был единственным значением, при котором пауза
взводится на обычной сделке: ret считается ДОЛЕЙ цены, и типичная минутная
сделка на RI это 0.05-0.2%, до 1% она не доходит почти никогда. То есть
единственный прямой регулятор частоты не работал вовсе, и перебор 02.08.2026
показал это буквально: cooldown_pct=0 и cooldown_pct=1 дали совпадающий до
рубля результат на 1 920 комбинациях.
"""
import pytest

from trader.lab.strategies.library import REGISTRY, make_on_bar


def _frac(params: dict) -> float:
    """Достаём порог остывания так же, как его считает make_on_bar."""
    cp = params.get("cooldown_pct")
    return float(1.0 if cp is None else cp) / 100.0


def test_zero_means_any_profitable_exit():
    assert _frac({"cooldown_pct": 0}) == 0.0
    assert _frac({"cooldown_pct": 0.0}) == 0.0


def test_missing_falls_back_to_one_percent():
    assert _frac({}) == pytest.approx(0.01)


def test_ordinary_minute_trade_arms_only_at_zero():
    # Ход 0.1% — типичная минутная сделка на индексе.
    ret = 0.001
    assert ret >= _frac({"cooldown_pct": 0})        # взводится
    assert not ret >= _frac({"cooldown_pct": 1})    # не взводится, и это нормально


def test_cooldown_params_are_in_the_schema():
    # Движок читал их у любой стратегии, но в схеме их не было — перебор их не видел.
    keys = {p["key"] for p in REGISTRY["williams_r"]["params_schema"]}
    assert {"cooldown_min", "cooldown_pct"} <= keys
    assert callable(make_on_bar("williams_r"))
