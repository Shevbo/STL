"""Двойная охрана: терминал и STL стерегут ОДНУ заявку — и исполняют её дважды.

29.09.2026, реальные деньги. Стоп оператора на продажу 40 RIZ6 был отправлен в
терминал в 06:45, до начала торгов. Подтверждение не пришло за 20 секунд, STL
объявил «терминал не принял» и взял охрану себе. Терминал заявку ПРИНЯЛ. В 09:32
стоп сработал в терминале на 40 контрактов, а сторож STL, ничего о нём не зная,
секундой позже выставил свою заявку — ещё 40. Позиция оператора перевернулась с
+40 в −40 без его решения. При отмене та же слепота оставила стоп-заявку живой:
книга писала «отменена», QUIK показывал «активна».

Лечение в трёх местах, и все три проверяются здесь: сторож не стреляет поверх
живой записи, охрана возвращается терминалу при позднем подтверждении, отмена
снимает запись по ФАКТУ таблицы, а не по статусу книги.
"""
from trader.api.quik_smart_orders import _track_native
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_663_000_000


class FakeStore:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def stop_orders(self, agent=None):
        return {"table": self.rows}

    def agent_status(self, agent=None):
        return {"quik": {"trades": []}}


def _row(so_id, num="310501606"):
    return {"brokerref": f"stl-so-{so_id}", "stop_order_num": num,
            "sec_code": "RIZ6", "operation": "S"}


def _standalone(tmp_path, **kw):
    book = SmartOrderBook(str(tmp_path / "book.json"))
    so = SmartOrder(so_id=new_id(), kind="sl", code="RIZ6", side="sell", qty=40,
                    trigger_price=82840, created_ms=NOW, **kw)
    book.orders.append(so)
    return book, so


def test_late_confirmation_returns_custody_to_terminal(tmp_path):
    """Терминал подтвердил позже таймаута — охрана обязана вернуться ему."""
    book, so = _standalone(tmp_path, status="armed", native_state="failed",
                           native_ms=NOW - 600_000)
    _track_native(book, FakeStore([_row(so.so_id)]), "9618", NOW)
    assert so.native_state == "live", "запись в терминале есть — охрана не может быть у STL"
    assert so.status == "native"
    assert so.native_stop_num == "310501606"


def test_no_row_keeps_custody_with_stl(tmp_path):
    """Записи нет — STL остаётся сторожем: иначе позиция осталась бы голой."""
    book, so = _standalone(tmp_path, status="armed", native_state="failed",
                           native_ms=NOW - 600_000)
    _track_native(book, FakeStore([]), "9618", NOW)
    assert (so.native_state, so.status) == ("failed", "armed")


def test_fire_is_blocked_while_our_native_row_is_alive(tmp_path):
    """Сторож не стреляет поверх живой записи терминала — источник двойной продажи."""
    import trader.api.quik_smart_orders as m
    book, so = _standalone(tmp_path, status="armed", native_state="failed")
    native_rows = m._stop_rows_by_tag(FakeStore([_row(so.so_id)]), "9618")
    assert so.so_id in native_rows
    # блок в _watch_once читает ровно это множество: id заявки или её ребёнка
    blocked = so.so_id in native_rows or any(
        c.parent_id == so.so_id and c.so_id in native_rows for c in book.orders)
    assert blocked is True
    # чужая запись не блокирует
    other = m._stop_rows_by_tag(FakeStore([_row("deadbeef01")]), "9618")
    assert (so.so_id in other) is False
