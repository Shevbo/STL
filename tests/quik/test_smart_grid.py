"""Сетка «радиация»: заявки живут В СТАКАНЕ и восстанавливаются сами.

Заказ оператора 30.09.2026. Уровни фиксированы от цены постановки; исполнилась
заявка — на ТОТ ЖЕ уровень встаёт встречная (купленное на −2 продаётся там же:
это и есть тейк в один шаг сетки). Стоп за последним уровнем закрывает работу.
Уровень за планкой биржи не выставляется, а ждёт расширения границ: MOEX двигает
планки по своему расписанию, и заявку за ними просто не примут.
"""
from trader.api.quik_smart_orders import price_within_limits
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, new_id


def _grid(**kw):
    base = dict(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                g_step=100.0, g_buys=3, g_sells=2, g_lot=2, g_base=84000.0,
                g_stop_pts=150.0, created_ms=0)
    base.update(kw)
    return SmartOrder(**base)


def test_levels_and_prices_are_fixed_around_the_base():
    so = _grid()
    assert so_mod.grid_levels(so) == [-1, -2, -3, 1, 2]
    assert so_mod.grid_price(so, -1) == 83900 and so_mod.grid_price(so, 2) == 84200
    # низ покупает, верх продаёт
    assert so_mod.grid_side_for(so, -1) == "buy"
    assert so_mod.grid_side_for(so, 2) == "sell"


def test_filled_level_flips_to_the_opposite_side():
    """Тейк на противоположной заявке: купили на −2 — там же и продаём."""
    so = _grid()
    so.g_live = {"flip:-2": True}
    assert so_mod.grid_side_for(so, -2) == "sell"
    assert so_mod.grid_side_for(so, -1) == "buy", "соседние уровни не трогаются"
    so.g_live["flip:2"] = True
    assert so_mod.grid_side_for(so, 2) == "buy"


def test_stop_sits_beyond_the_last_level_on_both_sides():
    so = _grid()
    lo, hi = so_mod.grid_stop_levels(so)
    assert (lo, hi) == (84000 - 300 - 150, 84000 + 200 + 150)
    assert so_mod.grid_stop_hit(so, 83540) is True      # ниже нижнего края (83550)
    assert so_mod.grid_stop_hit(so, 83560) is False     # ещё внутри
    assert so_mod.grid_stop_hit(so, 84360) is True      # выше верхнего (84350)
    assert so_mod.grid_stop_hit(so, 84000) is False
    assert so_mod.grid_stop_hit(_grid(g_stop_pts=0), 99999) is False, "стоп выключен"


def test_exchange_price_limits_hold_a_level_back():
    """За планкой заявку не примут — держим у себя и ждём расширения."""
    assert price_within_limits(84200, (83000.0, 85000.0)) is True
    assert price_within_limits(85200, (83000.0, 85000.0)) is False
    assert price_within_limits(82000, (83000.0, 85000.0)) is False
    # границы неизвестны — НЕ ограничиваем: молчащий параметр не должен
    # останавливать торговлю
    assert price_within_limits(99999, None) is True
    assert price_within_limits(99999, (0.0, 0.0)) is True


def test_validation_demands_the_whole_grid():
    assert _grid(g_step=0).validate() is not None
    assert _grid(g_buys=0, g_sells=0).validate() is not None
    assert _grid(g_lot=0).validate() is not None
    assert _grid(g_base=0).validate() is not None
    assert _grid(sl_offset=100).validate() is not None, "сетка ведёт позицию сама"
    assert _grid().validate() is None
    # односторонняя сетка законна: только покупки или только продажи
    assert _grid(g_sells=0).validate() is None


# --------------------------------------------------------------------------
# СВИП ПО ГЕОМЕТРИИ для сторожа сетки.
#
# Уровни сетки считались чистыми функциями и были покрыты, а САМ СТОРОЖ
# (_grid_sync) — тот, кто превращает уровень в заявку, — не был покрыт ничем.
# Проверка «уровень по ту сторону рынка» жила там без единого теста, хотя
# добавлена она была тем же фиксом, что и у стенок, и по той же причине:
# 30.09.2026, 43 проданных контракта.
# --------------------------------------------------------------------------

import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik.smart_orders import SmartOrderBook

GNOW = 1_790_800_000_000
GSTEPS = {"RIZ6": 10.0}


class GSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)


class GOst:
    def working_orders(self, agent=None):
        return []

    def working_contracts(self, agent=None):
        return 0

    def placed_today(self, agent=None):
        return 0

    def register_pending(self, *a):
        pass

    def record_placement(self, agent):
        pass


class GLim:
    price_collar_frac = 0.002
    trading_enabled = True
    instrument_whitelist = ("RIZ6",)
    max_contracts_per_order = 100
    max_working_contracts = 500
    daily_order_cap = 500


def _gstore(px):
    class S:
        def tick(self, code, agent=None):
            return {"last": px, "bid": px - 10.0, "ask": px + 10.0}
    return S()


def _gbook(tmp_path):
    b = SmartOrderBook(str(tmp_path / "g.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                    g_step=100.0, g_buys=5, g_sells=5, g_lot=1, g_base=85000.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed")
    b.orders.append(so)
    return b, so


@pytest.mark.parametrize("market", range(84000, 86001, 100))
def test_no_grid_order_ever_crosses_the_market(tmp_path, market):
    """ИНВАРИАНТ: ни при какой цене рынка сетка не ставит заявку, пересекающую
    рынок. Уровни сетки ФИКСИРОВАНЫ, поэтому при любом сдвиге цены часть из них
    неизбежно оказывается по ту сторону — и именно они не имеют права стрелять.
    """
    book, so = _gbook(tmp_path)
    srv = GSrv()
    _grid_sync(book, _gstore(float(market)), GOst(), srv, GLim(), "9618",
               GSTEPS, {}, GNOW)
    for m in srv.sent:
        if m.WhichOneof("payload") != "place_order":
            continue
        p = m.place_order
        if p.side == 2:
            assert p.price > market, (
                f"продажа по {p.price:g} при рынке {market} пересекает рынок")
        else:
            assert p.price < market, (
                f"покупка по {p.price:g} при рынке {market} пересекает рынок")


def test_grid_places_nothing_without_a_quote(tmp_path):
    """Без котировки не понять, по какую сторону рынка уровень: не стреляем."""
    class Blind:
        def tick(self, code, agent=None):
            return {}

    book, _ = _gbook(tmp_path)
    srv = GSrv()
    _grid_sync(book, Blind(), GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)
    placed = [m for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    assert placed == [], "вслепую сетка не выставляется"
