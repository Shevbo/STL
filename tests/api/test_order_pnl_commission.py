"""Комиссия и чистый P&L заявки (заказ оператора 04.10.2026).

«Показывай кол-во сделок, размер удержанной комиссии и считай P&L за вычетом
комиссии». Главное, что здесь проверяется, — не арифметика, а ТРИ ЧЕСТНОСТИ:
сумма комиссий заявок равна комиссии блока ручной торговли (одна функция на двоих),
комиссия без ₽/пункт — это «неизвестно», а не ноль, и итог с живой позицией не
выдаётся, когда переоценить её нечем.
"""
import pytest

from trader.api.order_pnl import pnl_by_order
from trader.quik import manual_pnl
from trader.quik.manual_pnl import fill_commission

T0 = 1_790_000_000_000          # утро рабочего дня по МСК (будни: без выходной надбавки)
PV = {"RIZ6": 2.0}
LAST = {"RIZ6": 84_300.0}


def _fills(tag="stl-so-aaa", extra=()):
    return [
        {"tag": tag, "sec": "RIZ6", "side": "buy", "qty": 2, "price": 84_000, "ts_ms": T0},
        {"tag": tag, "sec": "RIZ6", "side": "sell", "qty": 2, "price": 84_100, "ts_ms": T0 + 1000},
        *extra,
    ]


def test_net_is_fix_minus_commission_and_fills_are_counted():
    a = pnl_by_order(_fills(), LAST, PV)["aaa"]
    assert a["fills"] == 2 and a["lots"] == 4
    assert a["commission_rub"] > 0
    assert a["net_rub"] == pytest.approx(a["fix_rub"] - a["commission_rub"], abs=0.01)
    # позиция закрыта: ВМ нет, итог равен чистому фиксу
    assert a["pos"] == 0 and a["total_rub"] == pytest.approx(a["net_rub"], abs=0.01)


def test_commission_of_orders_equals_commission_of_the_manual_block():
    """Сумма по заявкам совпадает с блоком: комиссию считает ОДНА функция.

    Иначе оператор видел бы в блоке одну комиссию, а в сумме по своим заявкам другую.
    """
    fills = _fills("stl-so-aaa") + [
        {"tag": "stl-so-bbb", "sec": "RIZ6", "side": "sell", "qty": 3, "price": 84_200, "ts_ms": T0 + 3000}]
    per_order = pnl_by_order(fills, LAST, PV)
    total_orders = sum(o["commission_rub"] for o in per_order.values())
    trades = [{**f, "channel": "smart", "order_num": f["tag"]} for f in fills]
    block = manual_pnl.summarize(trades, PV)
    assert total_orders == pytest.approx(block["commission_rub"], abs=0.02)


def test_scalper_discount_applies_to_a_closing_leg_inside_the_day():
    open_leg = fill_commission("RIZ6", 84_000, 2, 2.0, False, 0, T0)
    close_same_day = fill_commission("RIZ6", 84_100, 2, 2.0, True, T0, T0 + 1000)
    close_next_day = fill_commission("RIZ6", 84_100, 2, 2.0, True, T0 - 86_400_000, T0 + 1000)
    assert close_same_day < close_next_day          # биржевая часть вдвое меньше
    assert open_leg > 0


def test_no_point_value_means_unknown_not_zero():
    """Нет ₽/пункт — комиссия None, а не 0: ноль читался бы как «комиссии не было»."""
    a = pnl_by_order(_fills(), LAST, {})["aaa"]
    assert a["priced"] is False
    assert a["commission_rub"] is None
    assert a["net_rub"] is None and a["total_rub"] is None


def test_one_unpriced_fill_makes_the_whole_commission_unknown():
    """Заниженная комиссия делает чистый результат лучше, чем он есть."""
    fills = _fills() + [
        {"tag": "stl-so-aaa", "sec": "SiZ6", "side": "buy", "qty": 1, "price": 84_000, "ts_ms": T0 + 5000}]
    a = pnl_by_order(fills, LAST, PV)["aaa"]
    # филл другого инструмента заявкой не учитывается (одна заявка — один инструмент),
    # поэтому комиссия остаётся известной и равной двум первым филлам
    assert a["fills"] == 2 and a["commission_rub"] is not None


def test_open_position_total_adds_revaluation():
    fills = _fills(extra=[{"tag": "stl-so-aaa", "sec": "RIZ6", "side": "buy", "qty": 1,
                           "price": 84_050, "ts_ms": T0 + 2000}])
    a = pnl_by_order(fills, LAST, PV)["aaa"]
    assert a["pos"] == 1 and a["vm_rub"] == pytest.approx(500.0)        # (84 300 − 84 050) × 2 ₽
    assert a["total_rub"] == pytest.approx(a["net_rub"] + a["vm_rub"], abs=0.01)


def test_open_position_without_a_price_has_no_total():
    """Нечем переоценить живую позицию — итога нет: «фикс минус комиссия» выдавал бы
    себя за полный результат, в котором недостаёт целой ноги."""
    fills = _fills(extra=[{"tag": "stl-so-aaa", "sec": "RIZ6", "side": "buy", "qty": 1,
                           "price": 84_050, "ts_ms": T0 + 2000}])
    a = pnl_by_order(fills, {}, PV)["aaa"]
    assert a["pos"] == 1 and a["vm_rub"] is None
    assert a["net_rub"] is not None          # закрытая часть известна
    assert a["total_rub"] is None


def test_refactor_kept_the_block_commission_unchanged():
    """Вынос комиссии в fill_commission не должен сдвинуть число блока ни на копейку."""
    trades = [{"sec": "RIZ6", "side": "buy", "qty": 2, "price": 84_000, "ts_ms": T0, "channel": "quik", "order_num": "1"},
              {"sec": "RIZ6", "side": "sell", "qty": 2, "price": 84_100, "ts_ms": T0 + 1000, "channel": "quik", "order_num": "2"}]
    r = manual_pnl.summarize(trades, PV)
    expect = (fill_commission("RIZ6", 84_000, 2, 2.0, False, 0, T0)
              + fill_commission("RIZ6", 84_100, 2, 2.0, True, T0, T0 + 1000))
    assert r["commission_rub"] == pytest.approx(expect, abs=0.01)
