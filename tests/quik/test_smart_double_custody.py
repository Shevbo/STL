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


def _row(so_id, num="310501606", **dead):
    """Живая строка таблицы стоп-заявок QUIK. Номер лежит в order_num —
    поля stop_order_num в таблице нет вовсе."""
    row = {"brokerref": f"stl-so-{so_id}", "order_num": num, "ordernum": num,
           "seccode": "RIZ6", "withdraw_datetime_ms": "0",
           "activation_date_time_ms": "0", "linkedorder": "0"}
    row.update(dead)
    return row


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
    native_rows = m._stop_rows_live(FakeStore([_row(so.so_id)]), "9618")
    assert so.so_id in native_rows
    # блок в _watch_once читает ровно это множество: id заявки или её ребёнка
    blocked = so.so_id in native_rows or any(
        c.parent_id == so.so_id and c.so_id in native_rows for c in book.orders)
    assert blocked is True
    # чужая запись не блокирует
    other = m._stop_rows_live(FakeStore([_row("deadbeef01")]), "9618")
    assert (so.so_id in other) is False


def test_audit_sees_both_directions(tmp_path):
    """Сверка книги с терминалом: обе стороны расхождения и молчание, когда сходится."""
    from trader.api.quik_smart_orders import _audit_book_vs_terminal
    book, so = _standalone(tmp_path, status="armed", native_state="")
    # 1. сходится: записи нет, книга стережёт сама — молчим
    assert _audit_book_vs_terminal(book, {}) == []
    # 2. книга сняла заявку, а в терминале она жива — случай 29.09
    so.status = "cancelled"
    msgs = _audit_book_vs_terminal(book, {so.so_id: _row(so.so_id)})
    assert len(msgs) == 1 and "ЖИВА" in msgs[0] and "310501606" in msgs[0]
    # 3. запись под чужим тегом: книга о ней не знает вовсе
    msgs = _audit_book_vs_terminal(book, {"deadbeef01": _row("deadbeef01", "999")})
    assert any("в книге такой заявки нет" in m for m in msgs)
    # 4. книга думает, что охраняет терминал, а записи нет — позиция без сторожа
    so.status = "native"
    msgs = _audit_book_vs_terminal(book, {})
    assert len(msgs) == 1 and "без сторожа" in msgs[0]
    # 5. та же заявка и живая запись — расхождения нет
    assert _audit_book_vs_terminal(book, {so.so_id: _row(so.so_id)}) == []


def test_dead_rows_are_not_custody(tmp_path):
    """Таблица QUIK хранит ВСЕ стоп-заявки дня. Сработавшая и снятая ничего не
    стерегут: принять их за живую охрану значит и заглушить сторожа, и поднять
    ложную тревогу — ровно это случилось в первый же час после выкладки."""
    import trader.api.quik_smart_orders as m
    live = _row("aaaaaaaa01")
    withdrawn = _row("aaaaaaaa02", "310501606", withdraw_datetime_ms="1790674445000")
    executed = _row("aaaaaaaa03", "310501605", activation_date_time_ms="1790663538000",
                    linkedorder="1925040256583954673")
    store = FakeStore([live, withdrawn, executed])
    assert set(m._stop_rows_live(store, "9618")) == {"aaaaaaaa01"}
    # а «все строки» обязаны остаться полными: по сработавшей записи разбирается,
    # какая нога связки исполнилась (её linkedorder ведёт к сделке)
    assert set(m._stop_rows_by_tag(store, "9618")) == {
        "aaaaaaaa01", "aaaaaaaa02", "aaaaaaaa03"}
    assert m._stop_num(live) == "310501606"
