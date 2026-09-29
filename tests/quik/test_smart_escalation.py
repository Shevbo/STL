"""Гарантированный выход стопа за три фазы: постоять, догнать, ударить по рынку.

29.09.2026 родной стоп оператора на 70 RIZ6 сработал в 14:50:06 и выставил
лимит в 30 пунктов от уровня. Рынок за минуту прошёл 690 пунктов, заявка умерла
с нулём исполнения, позиция осталась открытой. Стоп, который срабатывает, но не
исполняется, защитой не является.
"""
from trader.api.quik_smart_orders import _escalate_protection
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_682_606_000
STEPS = {"RIZ6": 10.0}


class FakeSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)

    @property
    def prices(self):
        return [round(m.replace_order.new_price) for m in self.sent
                if m.WhichOneof("payload") == "replace_order"]


class FakeOst:
    def __init__(self, **rec):
        self.rec = {"client_id": "so:x", "order_id": "111", "state": "active",
                    "remaining": 70, "filled": 0}
        self.rec.update(rec)
        self.pending = []
        self.placements = 0

    def working_orders(self, agent=None):
        return [self.rec]

    def working_contracts(self, agent=None):
        return 0

    def placed_today(self, agent=None):
        return 0

    def register_pending(self, agent, client_id, code, side, price, qty):
        self.pending.append((client_id, side, price, qty))

    def record_placement(self, agent):
        self.placements += 1


class FakeStore:
    def __init__(self, bid=83500.0, ask=83510.0):
        self.q = {"last": bid, "bid": bid, "ask": ask}

    def tick(self, code, agent=None):
        return self.q


class Lim:
    price_collar_frac = 0.002
    trading_enabled = True
    instrument_whitelist = ("RIZ6",)
    max_contracts_per_order = 100
    max_working_contracts = 200
    daily_order_cap = 500


def _book(tmp_path, **kw):
    book = SmartOrderBook(str(tmp_path / "b.json"))
    so = SmartOrder(so_id=new_id(), kind="sl", code="RIZ6", side="sell", qty=70,
                    trigger_price=83700, status="fired", fired_client_id="so:x",
                    fired_ms=NOW, created_ms=NOW, **kw)
    book.orders.append(so)
    return book, so


def _run(book, store, ost, srv, now):
    return _escalate_protection(book, store, ost, srv, Lim(), "9618", STEPS, now)


def test_phase1_holds_then_phase2_chases_then_phase3_hits_market(tmp_path):
    book, so = _book(tmp_path)
    store, ost, srv = FakeStore(), FakeOst(), FakeSrv()
    # фаза 1: стоим у планки, ничего не трогаем
    assert _run(book, store, ost, srv, NOW + 9_000) is False and srv.sent == []
    # фаза 2: пошли за ценой
    assert _run(book, store, ost, srv, NOW + 10_500) is True
    assert len(srv.sent) == 1
    # ...но не чаще, чем раз в esc_chase_every_sec
    assert _run(book, store, ost, srv, NOW + 11_500) is False and len(srv.sent) == 1
    assert _run(book, store, ost, srv, NOW + 12_600) is True and len(srv.sent) == 2
    # фаза 3: снимаем лимит и шлём НАСТОЯЩУЮ рыночную — ровно один раз
    assert _run(book, store, ost, srv, NOW + 21_000) is True
    kinds = [m.WhichOneof("payload") for m in srv.sent]
    assert kinds[-2:] == ["cancel_order", "place_order"]
    mkt = srv.sent[-1].place_order
    assert mkt.market is True and mkt.price == 0.0 and mkt.quantity == 70
    assert _run(book, store, ost, srv, NOW + 30_000) is False
    assert so.esc_market is True and so.fired_client_id.endswith(":mkt")


def test_filled_or_dead_order_is_left_alone(tmp_path):
    for rec in ({"remaining": 0}, {"state": "cancelled"}, {"state": "rejected"},
                {"order_id": ""}):
        book, _ = _book(tmp_path)
        srv = FakeSrv()
        assert _run(book, FakeStore(), FakeOst(**rec), srv, NOW + 30_000) is False
        assert srv.sent == []


def test_take_profit_and_entries_are_never_rushed(tmp_path):
    """Опоздавший тейк — упущенная прибыль, опоздавший стоп — открытый убыток."""
    for kind in ("tp", "trail_tp", "on_fill"):
        book, so = _book(tmp_path)
        so.kind = kind
        srv = FakeSrv()
        assert _run(book, FakeStore(), FakeOst(), srv, NOW + 30_000) is False
        assert srv.sent == []


def test_phases_are_configurable_and_zero_means_straight_to_market(tmp_path):
    book, so = _book(tmp_path, esc_hold_sec=0, esc_chase_sec=0)
    srv = FakeSrv()
    assert _run(book, FakeStore(), FakeOst(), srv, NOW + 100) is True
    assert so.esc_market is True
    assert srv.sent[-1].place_order.market is True


def test_no_quote_blocks_chase_but_not_the_market_exit(tmp_path):
    """В преследовании без котировки не двигаем: цена вслепую хуже стоящей заявки.
    А рыночному выходу котировка не нужна вовсе — в этом весь его смысл."""
    book, _ = _book(tmp_path)
    srv = FakeSrv()
    assert _run(book, FakeStore(0.0, 0.0), FakeOst(), srv, NOW + 12_000) is False
    assert srv.sent == []
    assert _run(book, FakeStore(0.0, 0.0), FakeOst(), srv, NOW + 30_000) is True
    assert srv.sent[-1].place_order.market is True
