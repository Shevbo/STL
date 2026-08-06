"""ГО отчёта = биржевое × множитель брокера. На биржевом значении доходность врёт.

Биржевое ГО из QLua/ISS — это ГО БИРЖИ, брокер списывает кратно (30.07.2026, RIU6:
биржа 22 375 ₽, счёт 53 672 ₽ = 2.4x). Бэктест считал `margin_used` из биржевого,
поэтому «% год (ГО)», доходность и доля просадки в истории прогонов были завышены
ровно во столько же раз — то есть врало главное число, по которому выбирают робота
(замечено оператором 06.08.2026).
"""
from types import SimpleNamespace

from trader.util import account_margin

RIU6_EXCHANGE = 22_375.0
MULT = 2.4


def test_account_margin_applies_the_broker_multiple():
    s = SimpleNamespace(quik_margin_multiplier=MULT)
    assert account_margin(s, RIU6_EXCHANGE) == RIU6_EXCHANGE * MULT


def test_default_is_no_op():
    """Множитель не задан — платформа работает как раньше, без сюрпризов."""
    assert account_margin(SimpleNamespace(), RIU6_EXCHANGE) == RIU6_EXCHANGE
    assert account_margin(SimpleNamespace(quik_margin_multiplier=None), 100) == 100


def test_missing_margin_never_raises():
    s = SimpleNamespace(quik_margin_multiplier=MULT)
    assert account_margin(s, None) == 0.0
    assert account_margin(None, None) == 0.0


def test_return_is_overstated_without_it():
    """Смысл поправки в одном числе: та же прибыль на честном ГО даёт доходность
    в 2.4 раза меньше. Робот, выглядевший как +100% год, делает +42%."""
    s = SimpleNamespace(quik_margin_multiplier=MULT)
    net, contracts = 500_000.0, 10
    naive = net / (contracts * RIU6_EXCHANGE)
    honest = net / (contracts * account_margin(s, RIU6_EXCHANGE))
    assert round(naive / honest, 6) == MULT
