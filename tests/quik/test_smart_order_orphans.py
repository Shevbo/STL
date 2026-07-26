"""Сработавшая умная заявка, чей ребёнок умер не исполнившись.

QUIK снимает неисполненные заявки на границе сессии. Если этого не заметить, в
книге останется «сработала», а защиты в рынке не будет — оператор считает, что
стоп стоит, и держит позицию. Эти тесты держат обнаружение такого случая.
"""

from __future__ import annotations

from trader.api.quik_smart_orders import _ORPHAN_GRACE_MS, _mark_orphans
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
    so = _fired()
    book = _book(so)
    ost = FakeOrderStore([{"client_id": "so:a1", "state": "cancelled", "filled": 0}])
    assert _mark_orphans(book, ost, "A1", NOW) is True
    assert so.status == "orphaned"
    assert "не исполнилась" in so.note


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
