"""Гейт кандидата ловит ровно то, что трижды заворачивало окно real-trade.

Каждый тест — реальный отказ, а не выдуманный случай.
"""
import importlib.util
import os

import pytest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts", "candidate_gate.py")
_spec = importlib.util.spec_from_file_location("candidate_gate", _PATH)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


@pytest.mark.parametrize("params,expect", [
    ({"ema1": 23, "ema2": 23}, True),        # 5 бумажных роботов в вечном шорте
    ({"ema1": 60, "ema2": 20}, False),       # инверсия — законный режим, не резать
    ({"ema1": 10, "ema2": 140}, False),
    ({"fast": 57, "slow": 48}, True),        # живой lxk22: 778 лонгов, ноль шортов
    ({"fast": 16, "slow": 16}, True),
    ({"fast": 12, "slow": 26}, False),
])
def test_degenerate_period_pairs(params, expect):
    assert (gate.degenerate(params) is not None) is expect


def test_warmup_comes_from_the_registry_not_a_formula():
    """4*max(...) завышал бы SMA-семейству и вовсе не видел стратегий без
    ключей периодов. Берём формулу самой стратегии."""
    # 2ema: 4 * max(ema1, ema2) — 400 не влезает в 600-барный персист, 20 влезает
    assert gate.warmup_bars("shectory_2ema", {"ema1": 60, "ema2": 400}) > gate.RUNNER_BAR_TAIL
    assert gate.warmup_bars("shectory_2ema", {"ema1": 60, "ema2": 20}) <= gate.RUNNER_BAR_TAIL
    # triple_sma: SMA определена на своём окне, ей хватает max+2 — бланкетная
    # формула отсеяла бы её зря
    w = gate.warmup_bars("triple_sma", {"fast": 5, "mid": 20, "slow": 200})
    assert w is not None and w <= gate.RUNNER_BAR_TAIL
    # суффикс __inv срезается: контр-стратегия считается по базовой
    assert gate.warmup_bars("shectory_2ema__inv", {"ema1": 60, "ema2": 20}) is not None
    # стратегия-модуль вне реестра не судится вовсе
    assert gate.warmup_bars("us_open_fvg", {}) is None


def test_grid_edge_flags_only_real_edges():
    """Лидер GDU6 стоял на максимуме ПЯТИ осей сразу — это обрыв, не оптимум.
    Ось из одного значения (закреплённая пином) краем не считается."""
    axes = {"mult": [10.0, 20.0, 30.0, 40.0], "period": [5.0, 23.0, 41.0, 59.0],
            "qty": [1.0]}
    assert gate.on_grid_edge({"mult": 40, "period": 5, "qty": 1}, axes) == \
        ["mult=40", "period=5"]
    assert gate.on_grid_edge({"mult": 20, "period": 23, "qty": 1}, axes) == []


def test_instruments_the_agent_cannot_trade_are_out():
    """GDU6 и MXU6 нет ни в фиде агента, ни в белом списке лимитов: сколько бы
    они ни показывали в бэктесте, запустить их нельзя."""
    assert "GDU6" not in gate.TRADABLE and "MXU6" not in gate.TRADABLE
    assert "BRU6" in gate.TRADABLE and "RIU6" in gate.TRADABLE


# ── пункты, добавленные окном real-trade 12.08.2026 ──────────────────────────

def test_robot_that_cannot_close_is_rejected():
    """Выход всей книгой — ОДНА заявка. Потолок лестницы больше капа на заявку
    означает робота, который открывается усреднением и не закрывается никогда
    (било вживую 21.07.2026)."""
    assert gate.caps_ok({"qty": 1, "avg_max": 40}, per_order=34, working=70) is not None
    assert gate.caps_ok({"qty": 1, "avg_max": 17}, per_order=34, working=70) is None
    # разворот держит в воздухе выход И новый вход
    assert gate.caps_ok({"qty": 5, "avg_max": 34}, per_order=34, working=35) is not None
    # ставочная система поднимает АБСОЛЮТНЫЙ потолок выше avg_max
    assert gate.max_position({"qty": 1, "avg_max": 4, "bet_max": 30}) == 31


def test_ladder_that_does_not_fit_the_account_is_rejected():
    """Считать по ГО СЧЁТА (×2.4), а не биржевому, и оставлять половину
    свободного на просадку: иначе принудительное закрытие в худшей точке."""
    # RIU6: биржевое ГО 22 416 -> счёт 53 798 за контракт
    fits = gate.margin_ok({"qty": 1, "avg_max": 10}, 22_416, 1_529_640, 2.4, 0.5)
    over = gate.margin_ok({"qty": 1, "avg_max": 34}, 22_416, 1_529_640, 2.4, 0.5)
    assert fits is None and over is not None
    # без известной экономики инструмента не судим
    assert gate.margin_ok({"qty": 1, "avg_max": 99}, 0, 1_529_640, 2.4, 0.5) is None


def test_spread_is_charged_because_the_backtester_ignores_it():
    """Комиссия в бэктесте есть, перехода спреда нет, а заявки робота
    маркетабельны. На BR шаг стоит 8.22 ₽ на контракт на сторону."""
    cost = gate.spread_cost({"qty": 1}, trades=291, step_value=8.21665)
    assert round(cost) == round(291 * 2 * 8.21665)      # обе стороны
    assert gate.spread_cost({"qty": 4}, 100, 8.21665) == 4 * gate.spread_cost({"qty": 1}, 100, 8.21665)
    assert gate.spread_cost({"qty": 1}, 291, 0) == 0.0  # шаг неизвестен — не штрафуем
