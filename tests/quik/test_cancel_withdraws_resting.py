"""Отмена умной заявки снимает и её заявки ИЗ СТАКАНА QUIK.

ИНЦИДЕНТ 01.10.2026. Оператор нажал «изменить» на коридоре, интерфейс снял умную
заявку — а две её заявки остались жить в терминале: покупка 1 по 85450 и продажа
1 по 86210 при рынке 85700. Книга писала «отменена», QUIK держал обе. Снял руками
по номерам; налиться не успело, позиции не возникло.

Корень: 30.09 коридор, треугольник и сетка научились СТОЯТЬ В СТАКАНЕ заранее, а
путь отмены снимал только НАТИВНЫЕ СТОП-ЗАЯВКИ — до 30.09 ничего другого умная
заявка в терминале и не держала. Новая возможность молча обошла старую уборку.
"""
from types import SimpleNamespace

from trader.api.quik_smart_orders import _withdraw_resting
from trader.quik.smart_orders import SmartOrder, new_id


class _Srv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)

    def cancelled_cids(self):
        return sorted(m.cancel_order.client_id for m in self.sent
                      if m.WhichOneof("payload") == "cancel_order")


class _Ost:
    def __init__(self, recs):
        self._recs = recs

    def working_orders(self, agent=None):
        return self._recs


class _Store:
    """Зеркало агента с ТАБЛИЦЕЙ ЗАЯВОК ТЕРМИНАЛА — вторым путём снятия."""

    def __init__(self, terminal_orders=()):
        self._orders = list(terminal_orders)

    def agents(self):
        return ["9618"]

    def agent_status(self, agent=None):
        return {"_received_at_ms": 1_790_800_000_000, "quik": {"orders": self._orders}}


def _term_row(num, side, price, qty=1, tag="", active=True):
    return {"num": num, "sec": "RIZ6", "side": side, "price": price, "qty": qty,
            "balance": qty, "active": active, "tag": tag,
            "ts_ms": 1_790_800_000_000}


def _request(srv, ost, store=None):
    state = SimpleNamespace(quik_server=srv, quik_order_store=ost,
                            quik_store=store or _Store())
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _corridor(**kw):
    args = dict(so_id=new_id(), kind="corridor", code="RIZ6", side="sell", qty=1,
                status="armed", created_ms=0)
    args.update(kw)
    return SmartOrder(**args)


def test_corridor_walls_are_withdrawn_on_cancel(monkeypatch):
    """ГЛАВНЫЙ СЛУЧАЙ, ценой двух живых заявок оператора."""
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={"top": "so:x:top:1", "low": "so:x:low:1"})
    srv = _Srv()
    ost = _Ost([
        {"client_id": "so:x:top:1", "order_id": "111", "state": "active"},
        {"client_id": "so:x:low:1", "order_id": "222", "state": "active"},
    ])
    assert _withdraw_resting(_request(srv, ost), so) == 2
    assert srv.cancelled_cids() == ["so:x:low:1", "so:x:top:1"]
    assert so.c_live == {}, "книга не вправе помнить заявки, которых больше нет"


def test_grid_levels_are_withdrawn_too(monkeypatch):
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(kind="grid", g_live={"-1": "so:g:-1", "2": "so:g:2"})
    srv = _Srv()
    ost = _Ost([
        {"client_id": "so:g:-1", "order_id": "1", "state": "active"},
        {"client_id": "so:g:2", "order_id": "2", "state": "active"},
    ])
    assert _withdraw_resting(_request(srv, ost), so) == 2
    assert so.g_live == {}


def test_bookkeeping_keys_are_not_mistaken_for_orders(monkeypatch):
    """`flip:` и `cross:` — состояние, а не client_id: снимать по ним нечего."""
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={"cross:top": 1}, g_live={"flip:-1": 1})
    srv = _Srv()
    assert _withdraw_resting(_request(srv, _Ost([])), so) == 0
    assert srv.cancelled_cids() == []


def test_already_dead_orders_are_not_cancelled_twice(monkeypatch):
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={"top": "so:x:top:1", "low": "so:x:low:1"})
    srv = _Srv()
    ost = _Ost([
        {"client_id": "so:x:top:1", "order_id": "111", "state": "filled"},
        {"client_id": "so:x:low:1", "order_id": "222", "state": "active"},
    ])
    assert _withdraw_resting(_request(srv, ost), so) == 1
    assert srv.cancelled_cids() == ["so:x:low:1"]


def test_order_unknown_to_the_store_is_still_cancelled(monkeypatch):
    """Номер мог не прийти, а заявка уже в пути: молчать тут нельзя."""
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={"top": "so:x:top:1"})
    srv = _Srv()
    assert _withdraw_resting(_request(srv, _Ost([])), so) == 1
    assert srv.cancelled_cids() == ["so:x:top:1"]


def test_plain_stop_without_resting_orders_is_a_noop(monkeypatch):
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(kind="sl", c_live={}, g_live={})
    srv = _Srv()
    assert _withdraw_resting(_request(srv, _Ost([])), so) == 0
    assert srv.sent == []


# --------------------------------------------------------------------------
# ПРОВОДКА: что РУЧКА ОТМЕНЫ её зовёт. Тесты выше проверяют функцию и остались
# бы зелёными, если бы вызов из cancel_order потеряли — именно так 01.10 лёг STL
# (34 зелёных теста на класс, сломан вызов).
# --------------------------------------------------------------------------
import pytest

from trader.api.quik_smart_orders import cancel_order
from trader.quik.smart_orders import SmartOrderBook


@pytest.mark.asyncio
async def test_cancel_endpoint_withdraws_the_walls(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    monkeypatch.setattr("trader.api.quik_smart_orders._auth", lambda r: "op")
    monkeypatch.setattr("trader.api.quik_smart_orders._kill_native_by_table",
                        lambda r, so: None)

    book = SmartOrderBook(str(tmp_path / "b.json"))
    so = _corridor(c_live={"top": "so:x:top:1", "low": "so:x:low:1"})
    book.orders.append(so)

    srv = _Srv()
    ost = _Ost([
        {"client_id": "so:x:top:1", "order_id": "111", "state": "active"},
        {"client_id": "so:x:low:1", "order_id": "222", "state": "active"},
    ])
    state = SimpleNamespace(quik_server=srv, quik_order_store=ost,
                            quik_store=_Store(), smart_orders=book,
                            settings=SimpleNamespace(shectory_auth_bridge_secret="s"))
    req = SimpleNamespace(app=SimpleNamespace(state=state))

    out = await cancel_order(so.so_id, req)
    assert out["ok"] is True
    assert so.status == "cancelled"
    assert srv.cancelled_cids() == ["so:x:low:1", "so:x:top:1"], (
        "ручка отмены не сняла заявки из стакана — значит вызов _withdraw_resting потерян")
    assert so.c_live == {}


def test_bookkeeping_values_that_are_numbers_do_not_break_the_cancel(monkeypatch):
    """ОТМЕНА ОПЕРАТОРА УПАЛА 01.10.2026 С TypeError, и заявка осталась в QUIK.

    В c_live, кроме идентификаторов заявок, лежат служебные значения: moved:<стенка>
    это ВРЕМЯ последней перестановки, число. Я отбирал заявки по именам ключей и
    moved: не перечислил — число попало в список «заявок», sorted() сравнил его со
    строкой, DELETE упал, а человек считал, что заявку снял.

    Отбор идёт по ЗНАЧЕНИЮ: идентификатор это строка. Иначе каждый новый служебный
    ключ снова ломал бы отмену.
    """
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={
        "top": "so:x:top:1",
        "moved:low": 1790846601833,     # время, не заявка
        "cross:low": 1,                 # признак, не заявка
    })
    srv = _Srv()
    ost = _Ost([{"client_id": "so:x:top:1", "order_id": "11", "state": "active"}])
    assert _withdraw_resting(_request(srv, ost), so) == 1
    assert srv.cancelled_cids() == ["so:x:top:1"]


# --------------------------------------------------------------------------
# СНЯТИЕ БЕЗ ЧЬЕЙ-ЛИБО ПАМЯТИ.
#
# Тесты выше снимают по client_id, и это работает, пока целы ДВЕ карты в памяти:
# склад заявок STL и карта заявок агента. 01.10.2026 перезапуск агента обнулил
# его карту, и 24 заявки сетки стали НЕСНИМАЕМЫМИ: STL слал отмену, агент не
# находил заявку и выходил, книга писала «снято», а заявки продолжали торговать и
# набрали оператору лишние контракты. Жалоба дословно: «не можешь их снять и
# пугаешь меня амнезией если будет перезагрузка».
#
# Лечится тем, что снятие опирается на таблицу заявок ТЕРМИНАЛА: она живёт в QUIK
# и переживает перезапуск обоих процессов. KILL_ORDER нужны только номер заявки,
# класс и инструмент — ничьей памяти для этого не требуется.
# --------------------------------------------------------------------------


def test_orders_are_withdrawn_from_the_terminal_table_when_nobody_remembers_them(monkeypatch):
    """ГЛАВНЫЙ СЛУЧАЙ: книга и склад пусты, а заявки СТОЯТ. Падает на старом коде."""
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(kind="grid", g_live={})            # книга ничего не помнит
    store = _Store([
        _term_row("701", "buy", 85400.0, tag=f"stl-so-{so.so_id}"),
        _term_row("702", "sell", 85600.0, tag=f"stl-so-{so.so_id}"),
        _term_row("703", "buy", 85300.0, tag="stl-so-ffffffff01"),   # чужая умная
        _term_row("704", "sell", 85700.0, tag=""),                   # руками оператора
    ])
    srv = _Srv()
    assert _withdraw_resting(_request(srv, _Ost([]), store), so) == 2
    nums = sorted(m.cancel_order.order_id for m in srv.sent
                  if m.WhichOneof("payload") == "cancel_order")
    assert nums == ["701", "702"], "снимаем ТОЛЬКО свои, чужие не трогаем"
    assert all(m.cancel_order.code == "RIZ6" for m in srv.sent),         "инструмент обязателен: без него агент не снимет заявку, которой не знает"


def test_a_known_order_is_not_cancelled_twice(monkeypatch):
    """Заявка, известная складу, снимается один раз, а не дважды — таблица
    терминала её уже не повторяет."""
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={"top": "so:x:top:1"})
    store = _Store([_term_row("111", "sell", 86000.0, tag=f"stl-so-{so.so_id}")])
    srv = _Srv()
    ost = _Ost([{"client_id": "so:x:top:1", "order_id": "111", "state": "active"}])
    assert _withdraw_resting(_request(srv, ost, store), so) == 1
    assert len([m for m in srv.sent if m.WhichOneof("payload") == "cancel_order"]) == 1


def test_instrument_travels_with_every_smart_order_cancel(monkeypatch):
    """Инструмент уходит В КАЖДОМ снятии, даже когда заявка известна складу: иначе
    снятие осталось бы зависимым от памяти агента, которая умирает с процессом."""
    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    so = _corridor(c_live={"top": "so:x:top:1", "low": "so:x:low:1"})
    srv = _Srv()
    ost = _Ost([
        {"client_id": "so:x:top:1", "order_id": "111", "state": "active"},
        {"client_id": "so:x:low:1", "order_id": "222", "state": "active"},
    ])
    _withdraw_resting(_request(srv, ost), so)
    assert [m.cancel_order.code for m in srv.sent] == ["RIZ6", "RIZ6"]
