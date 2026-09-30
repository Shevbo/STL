"""Коридор и треугольник держат заявки В СТАКАНЕ, на обеих стенках.

До 30.09.2026 сторож ждал касания и стрелял в тот же миг — то есть вставал в
очередь последним ровно там, где важна очередь. Оператор потребовал держать
заявки заранее. Стенки движутся, поэтому заявка не ставится заново, а
ПЕРЕСТАВЛЯЕТСЯ вслед за линией одной транзакцией: между «снять» и «поставить»
есть тик, в который защиты нет.
"""
from trader.api.quik_smart_orders import _walls_sync
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_800_000_000
HOUR = 3_600_000
STEPS = {"RIZ6": 10.0}


class FakeSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)

    def kinds(self):
        return [m.WhichOneof("payload") for m in self.sent]


class FakeOst:
    def __init__(self, recs=()):
        self.recs = list(recs)

    def working_orders(self, agent=None):
        return self.recs

    def working_contracts(self, agent=None):
        return 0

    def placed_today(self, agent=None):
        return 0

    def register_pending(self, *a):
        pass

    def record_placement(self, agent):
        pass


class FakeStore:
    def tick(self, code, agent=None):
        return {"last": 84500.0, "bid": 84490.0, "ask": 84510.0}


class Lim:
    price_collar_frac = 0.002
    trading_enabled = True
    instrument_whitelist = ("RIZ6",)
    max_contracts_per_order = 100
    max_working_contracts = 200
    daily_order_cap = 500


def _book(tmp_path, **kw):
    b = SmartOrderBook(str(tmp_path / "b.json"))
    args = dict(so_id=new_id(), kind="corridor", code="RIZ6", side="sell", qty=10,
                c_qty=10, c_t1_ms=NOW, c_p1=85000.0, c_t2_ms=NOW + HOUR,
                c_p2=85000.0, c_low=84000.0, created_ms=NOW)
    args.update(kw)
    so = SmartOrder(**args)
    b.orders.append(so)
    return b, so


def _run(book, ost, srv, now=NOW, limits=None):
    return _walls_sync(book, FakeStore(), ost, srv, Lim(), "9618", STEPS,
                       limits or {}, now)


def test_both_walls_are_placed_up_front(tmp_path):
    book, so = _book(tmp_path)
    srv = FakeSrv()
    assert _run(book, FakeOst(), srv) is True
    assert srv.kinds() == ["place_order", "place_order"]
    got = {(m.place_order.side, round(m.place_order.price)) for m in srv.sent}
    assert got == {(2, 85000), (1, 84000)}, "верх продаёт, низ покупает"
    assert set(so.c_live) == {"top", "low"}
    assert all(m.place_order.quantity == 10 for m in srv.sent)


def test_moving_wall_is_replaced_not_recreated(tmp_path):
    """Наклонный коридор: стенка уехала — двигаем одной транзакцией."""
    book, so = _book(tmp_path, c_p2=86000.0)          # +1000 за час
    so.c_live = {"top": "so:x:top:1", "low": "so:x:low:1"}
    recs = [{"client_id": "so:x:top:1", "order_id": "11", "state": "active",
             "remaining": 10, "price": 85000.0},
            {"client_id": "so:x:low:1", "order_id": "12", "state": "active",
             "remaining": 10, "price": 84000.0}]
    srv = FakeSrv()
    assert _run(book, FakeOst(recs), srv, NOW + HOUR // 2) is True
    assert srv.kinds() == ["replace_order", "replace_order"]
    prices = sorted(round(m.replace_order.new_price) for m in srv.sent)
    assert prices == [84500, 85500], "обе линии сдвинулись на полшага наклона"


def test_wall_we_already_traded_from_is_cancelled(tmp_path):
    """В шорте от верхней стенки заявки наверху быть не должно: она нарастила бы
    позицию там, где коридор только ждёт противоположную стенку."""
    book, so = _book(tmp_path, c_pos=-10)
    so.c_live = {"top": "so:x:top:1"}
    recs = [{"client_id": "so:x:top:1", "order_id": "11", "state": "active",
             "remaining": 10, "price": 85000.0}]
    srv = FakeSrv()
    assert _run(book, FakeOst(recs), srv) is True
    assert "cancel_order" in srv.kinds()
    assert "top" not in so.c_live
    # а внизу стоит заявка НА ПЕРЕВОРОТ — вдвое
    low = [m.place_order for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    assert low and low[0].quantity == 20


def test_level_beyond_exchange_limit_is_not_placed(tmp_path):
    book, _ = _book(tmp_path)
    srv = FakeSrv()
    # верхняя стенка 85000 за планкой 84800 — её не выставляем, нижнюю ставим
    assert _run(book, FakeOst(), srv, limits={"RIZ6": (83000.0, 84800.0)}) is True
    sides = [m.place_order.side for m in srv.sent]
    assert sides == [1], "только покупка снизу"
