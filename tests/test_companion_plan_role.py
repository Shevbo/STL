"""Роль уровня на мини-графике панели.

Панель рисовала тейк, стоп и усреднение одинаково и подписывала «план»: на
графике робота в шорте тейк и стоп оба «B 15», и различить их было нельзя
(оператор 26.09.2026). Причина — текст раннера, роль — наше суждение о нём,
поэтому разбор закреплён тестом.
"""
from trader.api.quik_companion import PLAN_ROLE_RU, plan_role


def test_роли_берутся_из_причины_раннера():
    assert plan_role("тейк-профит: avg 84 500 − 1.5×ATR(14)=120") == "tp"
    assert plan_role("усреднение: avg + 1×ATR(14)=120") == "avg"
    assert plan_role("закрытие позиции (смена/снятие сигнала)") == "flip"


def test_вход_отличается_от_прочего_плана():
    assert plan_role("вход по сигналу", entry=True) == "entry"
    assert plan_role("если подтвердится бычий сигнал") == "plan"


def test_пустая_причина_не_роняет_и_не_выдумывает_роль():
    assert plan_role("") == "plan"
    assert plan_role(None) == "plan"


def test_у_каждой_роли_есть_подпись_для_экрана():
    for role in ("tp", "sl", "avg", "flip", "entry", "plan"):
        assert PLAN_ROLE_RU[role]
    assert PLAN_ROLE_RU["tp"] == "TP"
    assert PLAN_ROLE_RU["sl"] == "SL"
