"""Налив считается ОДИН раз, даже если таблица терминала отстаёт.

03.10.2026, GZZ6: заявка уровня исполнилась в 18:55:45 и была засчитана складом;
таблица ещё секунды показывала её АКТИВНОЙ, сторож подхватил эту строку как свежую
стоящую, а когда она обновилась на «исполнена», засчитал тот же налив ВТОРОЙ раз.
Позиция сетки ушла на 7 контрактов (+3 в книге против +10 по сделкам).

Свип по тому, сколько проходов таблица отстаёт; утверждение — запрет: позиция сетки
равна налитому ровно один раз на каждом шаге и после.
"""
import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, GSTEPS, _gstore, _gterm_row


class _Ost(GOst):
    def __init__(self):
        self.recs = []

    def working_orders(self, agent=None):
        return list(self.recs)


def _book(tmp_path):
    b = SmartOrderBook(str(tmp_path / "l.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="RIZ6", side="sell", qty=1,
                    g_step=100.0, g_buys=3, g_sells=3, g_lot=7, g_base=85000.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed", g_cash_on=True)
    b.orders.append(so)
    return b, so


@pytest.mark.parametrize("lag", [0, 1, 2, 5, 12])
@pytest.mark.parametrize("final", ["inactive", "gone"])
def test_a_fill_is_counted_once_while_the_table_lags(tmp_path, lag, final):
    """Условия боя 18:55: заявка уровня 0 (N1) и заявка соседнего уровня +1 (N2)
    исполнились почти одновременно. Склад учёл оба налива, соседний филл РАЗБУДИЛ
    уровень 0 — и на следующем проходе его собственная заявка N1, которую таблица
    ещё показывает активной, выглядела бы свежей стоящей на этом уровне."""
    book, so = _book(tmp_path)
    p0, p1 = so_mod.grid_price(so, 0), so_mod.grid_price(so, 1)     # 85000, 85100
    so.g_live = {"0": "cidA", "1": "cidB"}
    ost = _Ost()
    ost.recs = [
        {"client_id": "cidA", "order_id": "N1", "state": "filled", "remaining": 0,
         "filled": 7, "side": "sell", "price": p0},
        {"client_id": "cidB", "order_id": "N2", "state": "filled", "remaining": 0,
         "filled": 7, "side": "sell", "price": p1}]
    tag = f"stl-so-{so.so_id}:gp"

    def table(stage):
        rows = []
        for num, px in (("N1", p0), ("N2", p1)):
            if stage == "lag":                      # таблица отстаёт: «стоят»
                rows.append(_gterm_row(num, "sell", px, qty=7, tag=tag))
            elif stage == "inactive":               # догнала: исполнены
                r = _gterm_row(num, "sell", px, qty=7, tag=tag, active=False)
                r["balance"] = 0
                rows.append(r)
        return _gstore(84900.0, rows)               # рынок ниже обоих уровней

    t = GNOW
    for i in range(lag + 1):
        _grid_sync(book, table("lag"), ost, GSrv(), GLim(), "9618", GSTEPS, {}, t, True)
        t += 1000
        assert so.g_pos == -14, f"проход {i}: позиция {so.g_pos}, ждали -14 (два налива)"
    for i in range(3):
        _grid_sync(book, table(final), ost, GSrv(), GLim(), "9618", GSTEPS, {}, t, True)
        t += 1000
        assert so.g_pos == -14, (f"таблица догнала ({final}), проход {i}: позиция "
                                 f"{so.g_pos}, налив засчитан второй раз")


@pytest.mark.parametrize("lag", [0, 1, 2, 5])
@pytest.mark.parametrize("window", [0, 5])
def test_a_filled_order_is_never_cancelled(tmp_path, lag, window):
    """05.10.2026: после каждого филла уровня QUIK писал «Вы не можете снять данную
    заявку» дважды. Первое снятие слал гейт погасшего уровня в том же проходе,
    второе — запрет повтора по отстающей строке таблицы, и журнал врал «снята».
    Запрет: снятие исполненной заявки не уходит ни на одном проходе."""
    book, so = _book(tmp_path)
    so.g_window = window
    p0 = so_mod.grid_price(so, 0)
    so.g_live = {"0": "cidA"}
    ost = _Ost()
    ost.recs = [{"client_id": "cidA", "order_id": "N1", "state": "filled", "remaining": 0,
                 "filled": 7, "side": "sell", "price": p0}]
    tag = f"stl-so-{so.so_id}:gp"
    lagging = _gstore(84900.0, [_gterm_row("N1", "sell", p0, qty=7, tag=tag)])
    t = GNOW
    for i in range(lag + 1):
        srv = GSrv()
        _grid_sync(book, lagging, ost, srv, GLim(), "9618", GSTEPS, {}, t, True)
        t += 31_000                                   # за пределом «раз в 30 с»
        killed = [m.cancel_order.order_id for m in srv.sent
                  if m.WhichOneof("payload") == "cancel_order"]
        assert "N1" not in killed, f"проход {i}: снимаем уже исполненную заявку"
    assert so.g_pos == -7


@pytest.mark.parametrize("after", ["filled", "standing", "cancelled"])
def test_a_fill_while_stl_was_down_is_caught_by_number(tmp_path, after):
    """05.10.2026 10:29:41: покупка 7 GZZ6 уровня -4 исполнилась, пока STL
    перезапускался. После старта записи склада нет, исполненную строку подхват не
    берёт — филл пропадал, и на уровень вставала вторая покупка по той же цене."""
    book, so = _book(tmp_path)
    px = so_mod.grid_price(so, -1)                                   # 84900, покупка
    so.g_live = {"-1": "cidL"}
    tag = f"stl-so-{so.so_id}:gm"
    ost = _Ost()
    ost.recs = [{"client_id": "cidL", "order_id": "N7", "state": "active", "remaining": 7,
                 "filled": 0, "side": "buy", "price": px}]
    standing = _gstore(84950.0, [_gterm_row("N7", "buy", px, qty=7, tag=tag)])
    _grid_sync(book, standing, ost, GSrv(), GLim(), "9618", GSTEPS, {}, GNOW, True)
    assert so.g_pos == 0

    ost.recs = []                                    # рестарт STL: склад пуст
    row = _gterm_row("N7", "buy", px, qty=7, tag=tag, active=(after == "standing"))
    if after != "standing":
        row["balance"] = 0 if after == "filled" else 7
    t = GNOW + 60_000
    for i in range(3):
        srv = GSrv()
        _grid_sync(book, _gstore(84950.0, [row]), ost, srv, GLim(), "9618", GSTEPS, {},
                   t, True)
        t += 1000
        again = [m for m in srv.sent if m.WhichOneof("payload") == "place_order"
                 and abs(m.place_order.price - px) < 1]
        if after == "cancelled":
            continue                                 # снята без налива: уровень свободен
        assert not again, f"проход {i}: вторая заявка на уровень {px:g}"
    assert so.g_pos == (7 if after == "filled" else 0), so.g_pos


def test_a_restart_adopted_fill_is_still_counted_by_the_table(tmp_path):
    """Обратная сторона: если склад налив НЕ засчитывал (связь снята при подхвате),
    таблица обязана его посчитать — иначе пропал бы настоящий филл."""
    book, so = _book(tmp_path)
    px = so_mod.grid_price(so, 1)
    so.g_live = {"adopt:1": {"num": "N9"}}
    r = _gterm_row("N9", "sell", px, qty=7, tag=f"stl-so-{so.so_id}:gp", active=False)
    r["balance"] = 0
    _grid_sync(book, _gstore(85200.0, [r]), _Ost(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.g_pos == -7
