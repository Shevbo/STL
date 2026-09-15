"""Сработавшая умная заявка, чей ребёнок умер не исполнившись.

QUIK снимает неисполненные заявки на границе сессии. Если этого не заметить, в
книге останется «сработала», а защиты в рынке не будет — оператор считает, что
стоп стоит, и держит позицию. Эти тесты держат обнаружение такого случая.
"""

from __future__ import annotations

from trader.api.quik_smart_orders import (
    _ORPHAN_GRACE_MS,
    _mark_orphans,
    _revive_false_orphans,
    _snap_entries_to_grid,
    _track_fills,
)
from trader.quik.smart_orders import SmartOrder, SmartOrderBook

NOW = 1_700_000_000_000


class FakeOrderStore:
    def __init__(self, rows):
        self._rows = rows

    def working_orders(self, agent_id=None):
        return list(self._rows)


def _book(*orders) -> SmartOrderBook:
    b = SmartOrderBook(path="")
    b.orders = list(orders)
    b.save = lambda: None          # тестам не нужен диск
    return b


def _fired(so_id="a1", fired_ms=NOW - 10_000) -> SmartOrder:
    return SmartOrder(so_id=so_id, kind="sl", code="RIU6", side="sell", qty=1,
                      trigger_price=88_000, status="fired",
                      fired_ms=fired_ms, fired_client_id=f"so:{so_id}")


def test_child_filled_stays_fired():
    so = _fired()
    book = _book(so)
    ost = FakeOrderStore([{"client_id": "so:a1", "state": "filled", "filled": 1}])
    assert _mark_orphans(book, ost, "A1", NOW) is False
    assert so.status == "fired"


def test_child_cancelled_unfilled_becomes_orphan():
    so = _fired(fired_ms=NOW - _ORPHAN_GRACE_MS - 1)
    book = _book(so)
    ost = FakeOrderStore([{"client_id": "so:a1", "state": "cancelled", "filled": 0}])
    assert _mark_orphans(book, ost, "A1", NOW) is True
    assert so.status == "orphaned"
    assert "не исполнилась" in so.note


def test_fresh_reject_waits_for_the_grace():
    """14.09: агент пометил rejected заявку, на которую QUIK молчал 20 с, а через
    3.5 минуты пришло исполнение 10 RIU6. Без отсрочки книга звала перевзвести."""
    so = _fired(fired_ms=NOW - 21_000)
    book = _book(so)
    ost = FakeOrderStore([{"client_id": "so:a1", "state": "rejected", "filled": 0}])
    assert _mark_orphans(book, ost, "A1", NOW) is False
    assert so.status == "fired"


def test_found_trade_revives_a_false_orphan():
    import trader.quik.smart_orders as so_mod
    so = _fired(fired_ms=so_mod.now_ms() - 200_000)   # _track_fills смотрит на живые часы
    so.status, so.note, so.qty = "orphaned", "дочерняя заявка rejected и не исполнилась", 10
    book = _book(so)
    ost = FakeOrderStore([{"client_id": "so:a1", "state": "rejected", "order_id": "777"}])

    class Store:
        def agent_status(self, agent_id=None):
            return {"quik": {"trades": [{"order_num": "777", "qty": 10, "price": 87_620}]}}

        def params(self, agent_id=None):
            return None                   # шаг неизвестен: округление не проверяем тут

    assert _track_fills(book, ost, Store(), "A1") is True
    assert (so.fired_price, so.fired_qty) == (87_620, 10)
    assert _revive_false_orphans(book) is True
    assert so.status == "fired" and so.note == ""


def test_orphan_without_fill_stays_orphan():
    so = _fired()
    so.status = "orphaned"
    assert _revive_false_orphans(_book(so)) is False
    assert so.status == "orphaned"


def test_child_partially_filled_is_not_an_orphan():
    """Частично исполненная и снятая заявка защиту всё же дала — это не сирота."""
    so = _fired()
    book = _book(so)
    ost = FakeOrderStore([{"client_id": "so:a1", "state": "cancelled", "filled": 1}])
    assert _mark_orphans(book, ost, "A1", NOW) is False
    assert so.status == "fired"


def test_child_missing_from_the_table_becomes_orphan_only_after_the_grace(monkeypatch):
    import trader.api.quik_smart_orders as w
    monkeypatch.setattr(w, "_PROC_START_MS", NOW - 60_000)  # сработала при ЭТОМ процессе
    so = _fired(fired_ms=NOW - 1_000)
    book = _book(so)
    ost = FakeOrderStore([])                       # заявки в таблице нет вовсе
    # Только что выставлена: агент мог ещё не отчитаться — молчим.
    assert _mark_orphans(book, ost, "A1", NOW) is False
    assert so.status == "fired"
    # Прошла отсрочка, заявки так и нет: исполнения не было, защиты нет.
    later = NOW + _ORPHAN_GRACE_MS + 1
    assert _mark_orphans(book, ost, "A1", later) is True
    assert so.status == "orphaned"
    assert "границе сессии" in so.note


def test_fired_before_restart_is_never_declared_orphan(monkeypatch):
    """Рестарт STL стирает OrderStore: про сработавшую ДО старта процесса заявку
    ничего не известно — она могла исполниться (26.07: выкуп 14 конт. исполнился,
    а ложный orphaned предлагал перевзвести его второй раз)."""
    import trader.api.quik_smart_orders as w
    monkeypatch.setattr(w, "_PROC_START_MS", NOW)  # процесс стартовал ПОСЛЕ срабатывания
    so = _fired(fired_ms=NOW - 10_000)
    book = _book(so)
    assert _mark_orphans(book, FakeOrderStore([]), "A1",
                         NOW + 10 * _ORPHAN_GRACE_MS) is False
    assert so.status == "fired"


def test_armed_and_cancelled_orders_are_untouched():
    armed = SmartOrder(so_id="b1", kind="tp", code="RIU6", side="sell", qty=1,
                       trigger_price=90_000, status="armed")
    dead = SmartOrder(so_id="b2", kind="sl", code="RIU6", side="sell", qty=1,
                      trigger_price=87_000, status="cancelled")
    book = _book(armed, dead)
    assert _mark_orphans(book, FakeOrderStore([]), "A1", NOW + 10 * _ORPHAN_GRACE_MS) is False
    assert armed.status == "armed"
    assert dead.status == "cancelled"


def test_entry_and_bracket_snap_to_price_step():
    """15.09: 4 x 87440 + 1 x 87450 дали вход 87442, стоп 86942, тейк 88442 — у RI
    шаг 10, таких цен нет. Вход покупки округляется вверх (хуже для лонга)."""
    parent = SmartOrder(so_id="p1", kind="trail_tp", code="RIU6", side="buy", qty=5,
                        trail_offset=50, sl_offset=500, tp_offset=1000, status="fired",
                        fired_price=87442, fired_qty=5)
    sl = SmartOrder(so_id="s1", kind="sl", code="RIU6", side="sell", qty=5,
                    trigger_price=86942, parent_id="p1", status="armed")
    tp = SmartOrder(so_id="t1", kind="tp", code="RIU6", side="sell", qty=5,
                    trigger_price=88442, parent_id="p1", status="armed")
    book = _book(parent, sl, tp)
    assert _snap_entries_to_grid(book, {"RIU6": 10.0}) is True
    assert (parent.fired_price, sl.trigger_price, tp.trigger_price) == (87450, 86950, 88450)
    assert "вход 87450" in sl.note
    assert _snap_entries_to_grid(book, {"RIU6": 10.0}) is False     # идемпотентно
    short = SmartOrder(so_id="p2", kind="sl", code="RIU6", side="sell", qty=1,
                       status="fired", fired_price=87442, fired_qty=1)
    _snap_entries_to_grid(_book(short), {"RIU6": 10.0})
    assert short.fired_price == 87440                                # продажа вниз
