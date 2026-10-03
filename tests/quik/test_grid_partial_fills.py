"""Частичное исполнение уровня сетки попадает в позицию СРАЗУ.

02.10.2026, GZZ6: заявка уровня «продать 5 по 9914» налилась на 4 и стояла с
остатком 1; сторож считал позицию только по полному исполнению, сетку сняли раньше —
в книге +10, по сделкам +6. Свип по последовательностям частичных наливов и по
финалу (долилась / остаток снят / так и стоит); утверждение — запрет: позиция сетки
на каждом шаге равна сумме налитого, без пропусков и без двойного счёта.
"""
import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, GSTEPS, _gstore


class _Ost(GOst):
    def __init__(self):
        self.recs = []

    def working_orders(self, agent=None):
        return list(self.recs)


def _book(tmp_path):
    b = SmartOrderBook(str(tmp_path / "p.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="RIZ6", side="sell", qty=1,
                    g_step=100.0, g_buys=3, g_sells=3, g_lot=5, g_base=85000.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed", g_cash_on=True)
    so.g_live = {"1": "cidX"}          # уровень +1 (85100) стоит заявкой cidX
    b.orders.append(so)
    return b, so


@pytest.mark.parametrize("steps", [[1], [4], [2, 4], [1, 2, 3, 4], [3, 3, 4], [5], [2, 5], [1, 4, 5]])
@pytest.mark.parametrize("final", ["filled", "cancelled", "still"])
def test_position_tracks_every_partial_fill(tmp_path, steps, final):
    book, so = _book(tmp_path)
    ost = _Ost()
    for i, f in enumerate(steps):
        state = "filled" if f == 5 else "partial"
        ost.recs = [{"client_id": "cidX", "order_id": "N1", "state": state,
                     "remaining": 5 - f, "filled": f, "side": "sell", "price": 85100.0}]
        _grid_sync(book, _gstore(85000.0), ost, GSrv(), GLim(), "9618", GSTEPS, {},
                   GNOW + i * 1000, True)
        assert so.g_pos == -f, f"налито {f}: позиция {so.g_pos}, ждали {-f}"
    last = steps[-1]
    if final == "cancelled" and last < 5:
        ost.recs = [{"client_id": "cidX", "order_id": "N1", "state": "cancelled",
                     "remaining": 0, "filled": last, "side": "sell", "price": 85100.0}]
    for j in range(2):                       # повторные проходы не считают второй раз
        _grid_sync(book, _gstore(85000.0), ost, GSrv(), GLim(), "9618", GSTEPS, {},
                   GNOW + 100_000 + j * 1000, True)
        assert so.g_pos == -last, f"повторный проход {j}: позиция {so.g_pos}, ждали {-last}"
