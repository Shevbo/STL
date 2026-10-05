"""Рестарт STL не должен отбирать охрану у живой стоп-заявки терминала.

05.10.2026 07:40 оператор поставил trail_tp на покупку 13 GZZ6, STL отдал его
терминалу (стоп-заявка 310540449, зарегистрирована в 07:40:42). В 07:46 рестарт
STL. Две ошибки сложились:
  1. переход «отправлена -> зарегистрирована» не попал в файл книги, после
     рестарта заявка снова «отправлена» с временем 07:40;
  2. склад стоп-заявок после рестарта пуст до первой таблицы агента, и пустота
     читалась как «записи нет».
Итог: «терминал не принял», охрана у STL на 10 с при живой записи в QUIK —
сработай уровень, заявок было бы две. Плюс ложная тревога аудита через секунду
после постановки, когда регистрация ещё шла.
"""
import pytest

import trader.api.quik_smart_orders as m
from trader.quik.smart_orders import SmartOrder, SmartOrderBook

NOW = 1_791_175_584_000
SO = "e8e5f0eeec"


class _Store:
    """Склад агента: table_ms=0 — таблицы стоп-заявок в этом процессе ещё не было."""

    def __init__(self, rows=(), table_ms=NOW, positions=None):
        self.rows, self.table_ms = list(rows), table_ms
        self.positions = positions or {}

    def stop_orders(self, agent=None):
        return {"table": self.rows, "table_received_ms": self.table_ms, "events": []}

    def agent_status(self, agent=None):
        return {"quik": {"trades": []}, "health": {"positions": [
            {"sec": c, "net": n} for c, n in self.positions.items()]}}

    def __getattr__(self, name):
        return lambda *a, **k: {}


def _row(num="310540449"):
    return {"brokerref": f"stl-so-{SO}", "order_num": num, "seccode": "GZZ6",
            "flags": "25", "withdraw_datetime_ms": "0",
            "activation_date_time_ms": "0", "linkedorder": "0"}


def _book(tmp_path, native_state):
    book = SmartOrderBook(str(tmp_path / "book.json"))
    so = SmartOrder(so_id=SO, kind="trail_tp", code="GZZ6", side="buy", qty=13,
                    trigger_price=9910.0, created_ms=NOW - 400_000, status="native",
                    native_state=native_state, native_ms=NOW - 345_000,
                    guarded_seen=True)
    book.orders.append(so)
    return book, so


@pytest.mark.parametrize("state", ["sent", "live"])
def test_no_table_yet_means_unknown_not_missing(tmp_path, state):
    """Падает на старом коде: «sent» объявлялась непринятой, «live» — снятой сроком,
    и в обоих случаях охрана уходила STL при живой записи в терминале."""
    book, so = _book(tmp_path, state)
    failed = m._track_native(book, _Store(table_ms=0), "9618", NOW)
    assert failed == []
    assert (so.status, so.native_state) == ("native", state)


def test_a_real_empty_table_still_returns_custody(tmp_path):
    """Таблица пришла и записи в ней нет — это уже знание, охрана честно у STL."""
    book, so = _book(tmp_path, "sent")
    failed = m._track_native(book, _Store(table_ms=NOW), "9618", NOW)
    assert so in failed and so.status == "armed"


def test_audit_is_silent_while_registration_is_pending(tmp_path):
    book, so = _book(tmp_path, "sent")
    assert m._audit_book_vs_terminal(book, {}) == []
    so.native_state = "live"
    assert len(m._audit_book_vs_terminal(book, {})) == 1, "зарегистрирована и пропала — кричать"


@pytest.mark.asyncio
async def test_registration_reaches_the_book_file(tmp_path, monkeypatch):
    """Переход sent -> live обязан сохраниться: после рестарта книга читается с диска."""
    book, so = _book(tmp_path, "sent")
    book.save()

    class _State:
        settings = None

        def __getattr__(self, name):
            return None

    class _Ost:
        def __getattr__(self, name):
            return lambda *a, **k: {}

        def is_blocked(self, agent=None):
            return True                      # дальше прохода сторожа не идём

    st = _State()
    st.smart_orders = book
    st.quik_store = _Store([_row()], positions={"GZZ6": -13})
    st.quik_order_store = _Ost()
    st.quik_server = type("Srv", (), {"enqueue_order": lambda *a: None})()
    monkeypatch.setattr(m, "resolve_agent", lambda *a, **k: "9618")
    monkeypatch.setattr("trader.quik.smart_orders.now_ms", lambda: NOW)

    await m._watch_once(st)
    assert so.native_state == "live"
    again = SmartOrderBook(book._path)
    again.load()
    assert again.orders[0].native_state == "live", "регистрация не дошла до файла книги"
