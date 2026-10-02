"""«Только на выход» у сетки — выход СУММОЙ ровно на позицию.

02.10.2026, GZZ6: защита включила режим при позиции +5, и сетка оставила все 22
продажи выше средней по 5 — 110 контрактов против +5. Каждый уровень был законен
поштучно, их сумма на росте открыла бы шорт −105. Свип по числу стоящих в
терминале закрывающих заявок; утверждение — запрет: после прохода закрывающая
сторона в сумме не больше позиции, открывающей нет вовсе.
"""
import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, _gstore, _gterm_row

STEPS = {"GZZ6": 1.0}


def _book(tmp_path, pos, avg):
    b = SmartOrderBook(str(tmp_path / "g.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="GZZ6", side="sell", qty=5,
                    g_step=10.0, g_buys=20, g_sells=20, g_lot=5, g_base=9877.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed",
                    g_pos=pos, g_avg=avg, exit_only=True, g_trig_ms=GNOW - 1)
    b.orders.append(so)
    return b, so


def _rows(so, prices, side="sell"):
    out = []
    for i, px in enumerate(prices):
        r = _gterm_row(f"{1000 + i}", side, px, qty=5, tag=f"stl-so-{so.so_id}:gp")
        r["sec"] = "GZZ6"
        out.append(r)
    return out


def _store(px, rows):
    st = _gstore(px, rows)
    base = type(st)

    class S(base):
        def tick(self, code, agent=None):
            return {"last": px, "bid": px - 1, "ask": px + 1}
    return S()


@pytest.mark.parametrize("standing", [0, 1, 2, 5, 22])
def test_exit_side_never_exceeds_the_position(tmp_path, standing):
    book, so = _book(tmp_path, pos=5, avg=9857.0)
    prices = [9867.0 + 10 * i for i in range(standing)]
    srv = GSrv()
    _grid_sync(book, _store(9851.0, _rows(so, prices)), GOst(), srv, GLim(), "9618",
               STEPS, {}, GNOW, True)
    sent = [m for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    killed = {m.cancel_order.order_id for m in srv.sent
              if m.WhichOneof("payload") == "cancel_order"}
    kept = [px for i, px in enumerate(prices) if f"{1000 + i}" not in killed]
    total = 5 * len(kept) + sum(m.place_order.quantity for m in sent)
    assert total <= 5, (f"стояло {standing}: на выходе {total} при позиции +5 — "
                        "лишнее открыло бы шорт")
    assert all(m.place_order.side == 2 for m in sent), "открывающих заявок быть не может"
    if standing:
        assert kept == [9867.0], "остаётся ближайшая к рынку закрывающая"


def test_exit_only_without_a_terminal_table_places_nothing(tmp_path):
    """Таблицы терминала нет — сколько выхода уже стоит, неизвестно: не ставим."""
    book, so = _book(tmp_path, pos=5, avg=9857.0)
    st = _store(9851.0, [])

    class Blind(type(st)):
        def agent_status(self, agent=None):
            return {}
    srv = GSrv()
    _grid_sync(book, Blind(), GOst(), srv, GLim(), "9618", STEPS, {}, GNOW, True)
    assert not [m for m in srv.sent if m.WhichOneof("payload") == "place_order"]
