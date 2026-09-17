"""Передача защиты позиции под охрану терминала и возврат её сторожу STL.

Смысл механизма: пока защита в STL, падение STL оставляет позицию голой. Поэтому
после входа ставится нативная стоп-заявка QUIK, а наши защитные заявки замирают.
Терминал не принял - защита обязана вернуться в STL, а оператор узнать."""

from trader.api.quik_smart_orders import (
    _NATIVE_CONFIRM_MS,
    _handover_to_terminal,
    _track_native,
)
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id, protective_children

NOW = 1_784_700_000_000
STEPS = {"RIZ6": 10.0}


class FakeSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)


class FakeOst:
    def __init__(self):
        self.placements = 0

    def record_placement(self, agent):
        self.placements += 1


class FakeStore:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def stop_orders(self, agent=None):
        return {"table": self.rows}


def _book_with_bracket(tmp_path, **kw):
    book = SmartOrderBook(str(tmp_path / "book.json"))
    parent = SmartOrder(so_id=new_id(), kind="trail_tp", code="RIZ6", side="buy", qty=1,
                        trail_offset=50, status="fired", fired_price=87000, fired_qty=1,
                        fired_ms=NOW, created_ms=NOW, **kw)
    book.orders.append(parent)
    book.orders.extend(protective_children(parent, 87000, NOW))
    return book, parent


def test_bracket_is_handed_to_the_terminal_as_one_stop_order(tmp_path):
    book, parent = _book_with_bracket(tmp_path, sl_offset=300, tp_offset=500, tp_trail=100)
    srv, ost = FakeSrv(), FakeOst()
    assert _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW) is True
    assert parent.native_state == "sent" and ost.placements == 1
    assert all(c.status == "native" for c in book.orders if c.parent_id == parent.so_id)
    fields = srv.sent[0].place_stop_order.fields
    assert fields["STOP_ORDER_KIND"] == "TAKE_PROFIT_AND_STOP_LIMIT_ORDER"
    assert fields["STOPPRICE"] == "87500" and fields["STOPPRICE2"] == "86700"
    assert fields["OFFSET"] == "100"          # следящий тейк ведёт терминал
    # Повторный проход не шлёт вторую транзакцию: уже отдано.
    assert _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW + 1000) is False


def test_trailing_stop_after_entry_stays_in_stl(tmp_path):
    book, parent = _book_with_bracket(tmp_path, trail_after=200)
    srv, ost = FakeSrv(), FakeOst()
    assert _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW) is False
    assert not srv.sent and parent.native_state == ""
    assert [c.status for c in book.orders if c.parent_id == parent.so_id] == ["armed"]


def test_registration_marks_it_live_and_remembers_the_stop_number(tmp_path):
    book, parent = _book_with_bracket(tmp_path, sl_offset=300)
    srv, ost = FakeSrv(), FakeOst()
    _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW)
    holder = next(c for c in book.orders if c.status == "native")
    store = FakeStore([{"brokerref": f"stl-so-{holder.so_id}", "order_num": "310467777", "flags": "29"}])
    assert _track_native(book, store, "9618", NOW + 3000) == []
    assert parent.native_state == "live" and holder.native_stop_num == "310467777"


def test_terminal_did_not_take_it_so_stl_guards_again(tmp_path):
    book, parent = _book_with_bracket(tmp_path, sl_offset=300)
    srv, ost = FakeSrv(), FakeOst()
    _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW)
    store = FakeStore()
    assert _track_native(book, store, "9618", NOW + 1000) == []      # ждём регистрации
    failed = _track_native(book, store, "9618", NOW + _NATIVE_CONFIRM_MS + 1)
    assert failed == [parent] and parent.native_state == "failed"
    assert [c.status for c in book.orders if c.parent_id == parent.so_id] == ["armed"]


def test_executed_in_the_terminal_closes_our_records(tmp_path):
    book, parent = _book_with_bracket(tmp_path, sl_offset=300, tp_offset=500)
    srv, ost = FakeSrv(), FakeOst()
    _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW)
    holder = next(c for c in book.orders if c.status == "native")
    tag = f"stl-so-{holder.so_id}"
    _track_native(book, FakeStore([{"brokerref": tag, "order_num": "310467778", "flags": "29"}]),
                  "9618", NOW + 3000)
    _track_native(book, FakeStore([{"brokerref": tag, "order_num": "310467778", "flags": "28"}]),
                  "9618", NOW + 9000)
    assert parent.native_state == "done"
    assert {c.status for c in book.orders if c.parent_id == parent.so_id} == {"fired"}
