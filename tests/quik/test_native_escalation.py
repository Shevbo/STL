"""Стоп, отданный ТЕРМИНАЛУ, тоже доводится до исполнения.

Вопрос оператора 29.09.2026: отдавая защиту терминалу, мы получали живучесть
(стоп переживает падение STL) и теряли гарантию. Нативный стоп QUIK выставляет
лимит с запасом в два шага цены; на быстром движении его не наливают — ровно это
случилось с его родным стопом на 70 контрактов, сработавшим в 14:50:06 и
умершим с нулём исполнения, пока рынок шёл 690 пунктов за минуту.
"""
from trader.api.quik_smart_orders import _escalate_native_child
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_700_000_000
ACT = NOW - 60_000          # запись сработала минуту назад


class FakeSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)


class FakeStore:
    def __init__(self, orders):
        self._orders = orders

    def agent_status(self, agent=None):
        return {"quik": {"orders": self._orders}}


class Lim:
    price_collar_frac = 0.002
    trading_enabled = True
    instrument_whitelist = ("RIZ6",)
    max_contracts_per_order = 100
    max_working_contracts = 200
    daily_order_cap = 500


def _setup(tmp_path, balance=70, **kw):
    book = SmartOrderBook(str(tmp_path / "b.json"))
    so = SmartOrder(so_id=new_id(), kind="sl", code="RIZ6", side="buy", qty=70,
                    trigger_price=83700, status="native", native_stop_num="310503600",
                    created_ms=NOW, **kw)
    book.orders.append(so)
    rows = {so.so_id: {"order_num": "310503600", "brokerref": f"stl-so-{so.so_id}",
                       "activation_date_time_ms": str(ACT),
                       "linkedorder": "1925040256584789239"}}
    store = FakeStore([{"num": "1925040256584789239", "balance": balance,
                        "active": False, "qty": 70}])
    return book, so, rows, store


def test_unfilled_child_of_a_fired_native_stop_is_finished_by_market(tmp_path):
    book, so, rows, store = _setup(tmp_path)
    srv = FakeSrv()
    assert _escalate_native_child(book, store, srv, Lim(), "9618", rows, NOW) is True
    kinds = [m.WhichOneof("payload") for m in srv.sent]
    assert kinds == ["cancel_order", "place_order"]
    mkt = srv.sent[-1].place_order
    assert mkt.market is True and mkt.quantity == 70 and mkt.price == 0.0
    assert so.esc_market is True, "добиваем ОДИН раз"
    srv2 = FakeSrv()
    assert _escalate_native_child(book, store, srv2, Lim(), "9618", rows, NOW) is False
    assert srv2.sent == []


def test_filled_child_and_unfired_record_are_left_alone(tmp_path):
    # налилась целиком — не трогаем
    book, _, rows, store = _setup(tmp_path, balance=0)
    srv = FakeSrv()
    assert _escalate_native_child(book, store, srv, Lim(), "9618", rows, NOW) is False
    # запись ещё не срабатывала: стережёт, и хорошо
    book2, so2, rows2, store2 = _setup(tmp_path)
    rows2[so2.so_id]["activation_date_time_ms"] = "0"
    rows2[so2.so_id]["linkedorder"] = "0"
    assert _escalate_native_child(book2, store2, srv, Lim(), "9618", rows2, NOW) is False
    assert srv.sent == []


def test_normal_profile_never_forces_the_market(tmp_path):
    """«Нормальный» профиль — осознанный отказ от рыночного добивания."""
    book, _, rows, store = _setup(tmp_path, esc_profile="normal")
    srv = FakeSrv()
    assert _escalate_native_child(book, store, srv, Lim(), "9618", rows, NOW) is False
    assert srv.sent == []


def test_phases_are_waited_before_the_market_shot(tmp_path):
    book, _, rows, store = _setup(tmp_path, esc_profile="active")   # 3 мин + 3 мин
    srv = FakeSrv()
    assert _escalate_native_child(book, store, srv, Lim(), "9618", rows, NOW) is False
    assert _escalate_native_child(book, store, srv, Lim(), "9618", rows,
                                  ACT + 400_000) is True
