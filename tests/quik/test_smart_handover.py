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

    def agent_status(self, agent=None):
        return {"quik": {"trades": []}}


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


class FakeStoreWithTrades(FakeStore):
    """Стоп-таблица плюс сделки дочерней заявки: чем закрылась связка на деле."""

    def __init__(self, rows=(), trades=()):
        super().__init__(rows)
        self.trades = list(trades)

    def agent_status(self, agent=None):
        return {"quik": {"trades": self.trades}}


def _executed_bracket(tmp_path, fill_price):
    book, parent = _book_with_bracket(tmp_path, sl_offset=300, tp_offset=500)
    srv, ost = FakeSrv(), FakeOst()
    _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW)
    holder = next(c for c in book.orders if c.status == "native")
    tag = f"stl-so-{holder.so_id}"
    live = [{"brokerref": tag, "order_num": "310467778", "flags": "29"}]
    _track_native(book, FakeStore(live), "9618", NOW + 3000)
    done = [{"brokerref": tag, "order_num": "310467778", "flags": "28",
             "linkedorder": "1925040235109497119"}]
    trades = [{"order_num": "1925040235109497119", "qty": 1, "price": fill_price,
               "ts_ms": NOW + 8000}]
    return book, parent, FakeStoreWithTrades(done, trades)


def test_executed_bracket_marks_only_the_leg_that_actually_fired(tmp_path):
    # Вход 87000, стоп 86700, тейк 87500. Закрылись по 87510 — значит ТЕЙК.
    book, parent, store = _executed_bracket(tmp_path, 87510)
    _track_native(book, store, "9618", NOW + 9000)
    kids = {c.kind: c for c in book.orders if c.parent_id == parent.so_id}
    assert parent.native_state == "done"
    assert kids["tp"].status == "fired" and kids["tp"].fired_price == 87510
    assert kids["sl"].status == "cancelled"
    assert "сработал тейк" in kids["sl"].note
    # Обратный случай: закрылись по 86690 — сработал СТОП, тейк снят.
    book2, parent2, store2 = _executed_bracket(tmp_path, 86690)
    _track_native(book2, store2, "9618", NOW + 9000)
    kids2 = {c.kind: c for c in book2.orders if c.parent_id == parent2.so_id}
    assert kids2["sl"].status == "fired" and kids2["tp"].status == "cancelled"


def test_execution_without_proof_is_not_called_fired(tmp_path):
    # Сделок дочерней заявки нет: какая нога сработала — неизвестно, и врать нельзя.
    book, parent, _ = _executed_bracket(tmp_path, 87510)
    store = FakeStoreWithTrades([{"brokerref": f"stl-so-{next(c.so_id for c in book.orders if c.status == 'native')}",
                                  "order_num": "310467778", "flags": "28", "linkedorder": "0"}])
    assert _track_native(book, store, "9618", NOW + 9000) == []      # ждём сделки
    out = _track_native(book, store, "9618", NOW + 9000 + 60_001)
    assert out == [parent]
    assert {c.status for c in book.orders if c.parent_id == parent.so_id} == {"orphaned"}


def test_a_foreign_trade_is_not_taken_as_our_fill(tmp_path):
    """Сделка того же инструмента, но ЧУЖОЙ стороны, исполнением не считается.

    Иначе покупка оператора руками «закрыла» бы висящий защитный стоп на продажу,
    и позиция осталась бы без защиты с отметкой «сработала»."""
    book, parent = _live_bracket(tmp_path)
    foreign = FakeStoreWithTrades((), [{"sec": "RIZ6", "side": "B", "qty": 1,
                                        "price": 87490, "ts_ms": NOW + 30_000, "tag": ""}])
    assert _track_native(book, foreign, "9618", NOW + 40_000) == [parent]
    assert parent.native_state == ""
    assert {c.status for c in book.orders if c.parent_id == parent.so_id} == {"armed"}


class FakeStoreWithPositions(FakeStore):
    def __init__(self, rows=(), positions=()):
        super().__init__(rows)
        self.positions = list(positions)

    def agent_status(self, agent=None):
        return {"health": {"positions": self.positions}}


def test_standalone_stop_is_handed_over_and_guards_itself(tmp_path):
    from trader.api.quik_smart_orders import _handover_standalone
    book = SmartOrderBook(str(tmp_path / "b.json"))
    so = SmartOrder(so_id=new_id(), kind="sl", code="RIZ6", side="buy", qty=10,
                    trigger_price=84510, status="armed", created_ms=NOW)
    book.orders.append(so)
    srv, ost = FakeSrv(), FakeOst()
    store = FakeStoreWithPositions(positions=[{"sec": "RIZ6", "net": -13}])

    assert _handover_standalone(book, STEPS, store, ost, srv, "9618", NOW) is True
    assert so.status == "native" and so.native_state == "sent"
    assert srv.sent[0].place_stop_order.fields["STOPPRICE"] == "84510"
    # Второй проход не шлёт вторую транзакцию.
    assert _handover_standalone(book, STEPS, store, ost, srv, "9618", NOW + 1000) is False

    # Регистрация: заявка сама себе держатель, номер стоп-заявки пишется ей же.
    rows = FakeStore([{"brokerref": f"stl-so-{so.so_id}", "order_num": "310471000", "flags": "29"}])
    assert _track_native(book, rows, "9618", NOW + 3000) == []
    assert so.native_state == "live" and so.native_stop_num == "310471000"


def test_standalone_entry_order_stays_in_stl(tmp_path):
    from trader.api.quik_smart_orders import _handover_standalone
    book = SmartOrderBook(str(tmp_path / "b.json"))
    entry = SmartOrder(so_id=new_id(), kind="trail_tp", code="RIZ6", side="sell", qty=15,
                       trigger_price=84990, trail_offset=150, sl_offset=300, tp_trail=50,
                       status="armed", created_ms=NOW)
    book.orders.append(entry)
    srv, ost = FakeSrv(), FakeOst()
    store = FakeStoreWithPositions(positions=[{"sec": "RIZ6", "net": -13}])
    assert _handover_standalone(book, STEPS, store, ost, srv, "9618", NOW) is False
    assert not srv.sent and entry.status == "armed"


def test_terminal_refusal_returns_a_standalone_order_to_stl(tmp_path):
    from trader.api.quik_smart_orders import _NATIVE_CONFIRM_MS, _handover_standalone
    book = SmartOrderBook(str(tmp_path / "b.json"))
    so = SmartOrder(so_id=new_id(), kind="sl", code="RIZ6", side="buy", qty=10,
                    trigger_price=84510, status="armed", created_ms=NOW)
    book.orders.append(so)
    srv, ost = FakeSrv(), FakeOst()
    store = FakeStoreWithPositions(positions=[{"sec": "RIZ6", "net": -13}])
    _handover_standalone(book, STEPS, store, ost, srv, "9618", NOW)

    failed = _track_native(book, FakeStore(), "9618", NOW + _NATIVE_CONFIRM_MS + 1)
    assert failed == [so] and so.status == "armed" and so.native_state == "failed"


def _live_bracket(tmp_path):
    """Связка, отданная терминалу и подтверждённая в его таблице."""
    book, parent = _book_with_bracket(tmp_path, sl_offset=300, tp_offset=500)
    srv, ost = FakeSrv(), FakeOst()
    _handover_to_terminal(book, STEPS, ost, srv, "9618", NOW)
    holder = next(c for c in book.orders if c.status == "native")
    tag = f"stl-so-{holder.so_id}"
    _track_native(book, FakeStore([{"brokerref": tag, "order_num": "310469000",
                                    "flags": "29"}]), "9618", NOW + 3000)
    return book, parent


def test_vanished_without_a_trade_returns_the_guard_to_stl(tmp_path):
    """Снятие по СРОКУ (торговый день кончился) - не исполнение.

    23.09.2026 вчерашняя следящая продажа 30 контрактов исчезла утром из
    терминала, книга похоронила её в orphaned, и заявки не стало нигде."""
    book, parent = _live_bracket(tmp_path)
    failed = _track_native(book, FakeStore(), "9618", NOW + 40_000)
    assert failed == [parent]
    assert parent.native_state == ""        # чистый: связка уедет в терминал заново
    kids = [c for c in book.orders if c.parent_id == parent.so_id]
    assert [c.status for c in kids] == ["armed", "armed"]
    assert all(c.native_state == "" for c in kids)


def test_vanished_with_a_matching_trade_is_a_fill_not_a_reset(tmp_path):
    """Сделка по инструменту в окне = исполнение: заявку переставлять НЕЛЬЗЯ."""
    book, parent = _live_bracket(tmp_path)
    # Стоп на 86700, тейк на 87500; сделка по 87490 - сработал тейк.
    store = FakeStoreWithTrades((), [{"sec": "RIZ6", "side": "S", "qty": 1,
                                      "price": 87490, "ts_ms": NOW + 30_000, "tag": ""}])
    orphaned = _track_native(book, store, "9618", NOW + 40_000)
    assert orphaned == [parent] and parent.native_state == "done"
    kids = {c.kind: c for c in book.orders if c.parent_id == parent.so_id}
    assert kids["tp"].status == "fired" and kids["tp"].fired_price == 87490
    assert kids["sl"].status == "cancelled"
