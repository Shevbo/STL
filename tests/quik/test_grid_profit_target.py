"""Выход из «радиации» по цели прибыли (требование оператора 02.10.2026).

Прибыль сетки = денежный поток (продажи плюс, покупки минус) + открытая позиция по
рынку, умножить на рубли за пункт. Цель достигнута — уровни сняты, позиция закрыта
рыночной, сетка закончена.

Тесты держат ЗАПРЕТЫ, а не примеры: прибыль обязана совпадать с независимым
пересчётом на любой последовательности филлов, а цель не имеет права сработать там,
где прибыль не посчитать честно (нет ₽/пункт, нет базы у живой позиции).
"""
import random

import pytest

from trader.api.quik_smart_orders import _grid_count_fill, _grid_sync
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, GSTEPS, _gstore

COEF = 2.0     # ₽ за пункт (шаг 10 стоит 20 ₽)


def _grid(**kw):
    base = dict(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                g_step=100.0, g_buys=5, g_sells=5, g_lot=1, g_base=85000.0,
                g_stop_pts=0.0, created_ms=GNOW, status="armed", g_cash_on=True)
    base.update(kw)
    return SmartOrder(**base)


@pytest.mark.parametrize("seed", range(40))
def test_profit_equals_independent_recount_on_any_fill_sequence(seed):
    """Прибыль по потоку обязана совпасть с пересчётом «с нуля» на ЛЮБОЙ
    последовательности филлов: частичные сокращения, перевороты через ноль,
    наращивание. Средняя после частичного сокращения прибыль не описывает —
    поэтому и поток."""
    rnd = random.Random(seed)
    so = _grid()
    live: dict = {}
    cash, pos = 0.0, 0
    for i in range(rnd.randint(1, 30)):
        side = rnd.choice(("buy", "sell"))
        qty = rnd.randint(1, 3)
        px = 85000.0 + rnd.randint(-10, 10) * 10
        _grid_count_fill(so, live, rnd.randint(-5, 5), side, qty, GNOW + i, price=px)
        cash += px * qty if side == "sell" else -px * qty
        pos += qty if side == "buy" else -qty
    mark = 85000.0 + rnd.randint(-20, 20) * 10
    want = (cash + pos * mark) * COEF
    assert so.g_pos == pos
    assert so_mod.grid_profit_rub(so, mark, COEF) == pytest.approx(want), (
        f"seed {seed}: прибыль по потоку разошлась с пересчётом")


@pytest.mark.parametrize("pos0,avg0", [(3, 84900.0), (-2, 85100.0), (0, 0.0)])
def test_grid_from_before_the_feature_counts_from_the_moment_counting_began(pos0, avg0):
    """Сетка, взведённая до механики: потока нет, есть позиция по средней. Прибыль
    считается от неё, а не приписывается заработанное до начала счёта."""
    so = _grid(g_cash_on=False, g_pos=pos0, g_avg=avg0)
    assert so_mod.ensure_cash_basis(so) is True
    mark = 85200.0
    assert so_mod.grid_profit_rub(so, mark, COEF) == pytest.approx(
        pos0 * (mark - avg0) * COEF)


def test_live_position_without_an_average_never_triggers_the_target():
    """Позиция есть, средней нет: база не заводится, цель молчит. Иначе вся позиция
    по рынку сошла бы за прибыль и закрылась бы рыночной на пустом месте."""
    so = _grid(g_cash_on=False, g_pos=10, g_avg=0.0, g_tp_rub=1.0)
    assert so_mod.ensure_cash_basis(so) is False
    assert so_mod.grid_tp_hit(so, 85000.0, COEF) == ""


@pytest.mark.parametrize("coef", [0.0, -1.0])
def test_no_point_value_means_no_target(coef):
    """Без ₽/пункт прибыль в рублях не посчитать: пункт не рубль."""
    so = _grid(g_pos=5, g_avg=84000.0, g_cash_pts=-5 * 84000.0, g_tp_rub=1.0)
    assert so_mod.grid_tp_hit(so, 85000.0, coef) == ""


def test_zero_target_is_off():
    so = _grid(g_pos=5, g_avg=84000.0, g_cash_pts=-5 * 84000.0, g_tp_rub=0.0)
    assert so_mod.grid_tp_hit(so, 99000.0, COEF) == ""


def _store_with_params(px):
    base = _gstore(px)

    class S(type(base)):
        def params(self, agent=None):
            return {"rows": [{"code": "RIZ6", "price_step": 10.0, "step_cost": 20.0}]}
    return S()


def _book(tmp_path, **kw):
    b = SmartOrderBook(str(tmp_path / "g.json"))
    so = _grid(**kw)
    b.orders.append(so)
    return b, so


def test_target_reached_closes_the_position_at_market_and_finishes(tmp_path):
    # лонг 4 по 84900, рынок 85000: (85000-84900)*4*2 = 800 ₽ при цели 800
    book, so = _book(tmp_path, g_pos=4, g_avg=84900.0, g_cash_pts=-4 * 84900.0,
                     g_tp_rub=800.0)
    srv = GSrv()
    _grid_sync(book, _store_with_params(85000.0), GOst(), srv, GLim(), "9618",
               GSTEPS, {}, GNOW, True)
    mkt = [m.place_order for m in srv.sent
           if m.WhichOneof("payload") == "place_order" and m.place_order.market]
    assert len(mkt) == 1 and mkt[0].side == 2 and mkt[0].quantity == 4, (
        "цель достигнута: лонг 4 закрывается продажей 4 рыночной")
    assert so.g_done is True and so.status == "closing"
    assert "цель прибыли" in so.note, "повод выхода назван своим именем, а не «стоп»"


def test_below_target_nothing_closes(tmp_path):
    book, so = _book(tmp_path, g_pos=4, g_avg=84900.0, g_cash_pts=-4 * 84900.0,
                     g_tp_rub=801.0)
    srv = GSrv()
    _grid_sync(book, _store_with_params(85000.0), GOst(), srv, GLim(), "9618",
               GSTEPS, {}, GNOW, True)
    assert not [m for m in srv.sent
                if m.WhichOneof("payload") == "place_order" and m.place_order.market]
    assert so.g_done is False


def test_without_params_feed_the_target_stays_silent(tmp_path):
    """Фид параметров не пришёл: цель не срабатывает даже при огромной прибыли."""
    book, so = _book(tmp_path, g_pos=4, g_avg=80000.0, g_cash_pts=-4 * 80000.0,
                     g_tp_rub=1.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618",
               GSTEPS, {}, GNOW, True)
    assert so.g_done is False
