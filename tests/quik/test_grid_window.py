"""Окно «радиации»: в QUIK только ближайшие уровни, остальные ждут в STL.

Оператор 02.10.2026: «в радиации не надо выставлять все заявки сразу. только пять
сверху и пять снизу. остальные в STL». Свип по положению рынка внутри сетки;
утверждения — запреты: в QUIK не уходит больше N заявок на сторону и ни одной не
из N ближайших; стоящее в пределах N+2 не снимается (иначе цена у одного уровня
гоняла бы край окна снятием и постановкой — каждая транзакция стоит денег).
"""
import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, GSTEPS, _gstore, _gterm_row


def _book(tmp_path, window=5):
    b = SmartOrderBook(str(tmp_path / "w.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                    g_step=100.0, g_buys=20, g_sells=20, g_lot=1, g_base=85000.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed", g_window=window)
    b.orders.append(so)
    return b, so


def _placed(srv):
    return [m.place_order for m in srv.sent if m.WhichOneof("payload") == "place_order"]


@pytest.mark.parametrize("market", range(83500, 86501, 150))
def test_only_the_nearest_levels_go_to_quik(tmp_path, market):
    book, so = _book(tmp_path)
    srv = GSrv()
    _grid_sync(book, _gstore(float(market)), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    placed = _placed(srv)
    assert placed, "окно не должно глушить сетку целиком"
    above = sorted(p.price for p in placed if p.price > market)
    below = sorted((p.price for p in placed if p.price <= market), reverse=True)
    assert len(above) <= 5 and len(below) <= 5, (
        f"рынок {market}: выставлено {len(above)} сверху и {len(below)} снизу")
    lv_above = sorted(so_mod.grid_price(so, x) for x in so_mod.grid_levels(so)
                      if so_mod.grid_price(so, x) > market)[:5]
    lv_below = sorted((so_mod.grid_price(so, x) for x in so_mod.grid_levels(so)
                       if so_mod.grid_price(so, x) < market), reverse=True)[:5]
    assert set(above) <= set(lv_above) and set(below) <= set(lv_below), (
        "в QUIK ушёл уровень не из ближайших")


@pytest.mark.parametrize("market", [84050, 85000, 85950])
def test_far_resting_levels_are_withdrawn_near_ones_kept(tmp_path, market):
    book, so = _book(tmp_path)
    rows = []
    for i, lvl in enumerate(so_mod.grid_levels(so)):
        px = so_mod.grid_price(so, lvl)
        if px == market:
            continue
        rows.append(_gterm_row(f"{5000 + i}", "sell" if px > market else "buy", px,
                               tag=f"stl-so-{so.so_id}:g"))
    srv = GSrv()
    _grid_sync(book, _gstore(float(market), rows), GOst(), srv, GLim(), "9618",
               GSTEPS, {}, GNOW, True)
    killed = {m.cancel_order.order_id for m in srv.sent
              if m.WhichOneof("payload") == "cancel_order"}
    by_num = {r["num"]: r["price"] for r in rows}
    up = sorted(p for p in by_num.values() if p > market)
    dn = sorted((p for p in by_num.values() if p < market), reverse=True)
    keep = set(up[:7] + dn[:7])
    for num, px in by_num.items():
        if px in keep:
            assert num not in killed, f"рынок {market}: снят ближний уровень {px}"
        else:
            assert num in killed, f"рынок {market}: дальний уровень {px} остался в QUIK"


def test_zero_window_places_everything_as_before(tmp_path):
    book, so = _book(tmp_path, window=0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert len(_placed(srv)) == 40, "окно 0 = выставлять все уровни (кроме цены рынка)"
