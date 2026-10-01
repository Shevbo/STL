"""Защитная заявка обязана умирать вместе с позицией, которую охраняет.

ИНЦИДЕНТ 01.10.2026. Два стопа оператора на покупку 40 и 13 по уровню 85850
простояли взведёнными 34 минуты после того, как охраняемый шорт на 53 контракта
исчез (30.09 в 16:32). До уровня оставалось 230 пунктов, а 29.09 RIZ6 проходил
690 пунктов за минуту: сработав, они открыли бы ЛОНГ на 53 контракта с нуля.

Механизм знал о проблеме и молчал: передача защиты терминалу при нулевой позиции
возвращает None, то есть система ВИДИТ, что охранять нечего, — но заявка от этого
лишь остаётся `armed`, а `armed` означает «стережёт сторож STL».
"""
from trader.api.quik_smart_orders import _ORPHAN_GRACE_MS, _retire_unprotecting
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_830_000_000


class Store:
    """Снимок агента. positions=None означает СЛЕПОТУ, а не «позиций нет»."""

    def __init__(self, positions):
        self._p = positions

    def agent_status(self, agent=None):
        if self._p is None:
            return {"health": {}}                       # снимка нет
        return {"health": {"positions": [
            {"sec": c, "net": n} for c, n in self._p.items()]}}


def _book(tmp_path, **kw):
    b = SmartOrderBook(str(tmp_path / "b.json"))
    args = dict(so_id=new_id(), kind="sl", code="RIZ6", side="buy", qty=40,
                trigger_price=85850.0, status="armed", created_ms=NOW)
    args.update(kw)
    b.orders.append(SmartOrder(**args))
    return b, b.orders[0]


def _run(book, store, now=NOW):
    return _retire_unprotecting(book, store, "9618", now)


def test_stop_without_its_position_is_retired_after_the_grace(tmp_path):
    """ГЛАВНЫЙ СЛУЧАЙ: шорта нет, стоп на покупку охранять нечего."""
    book, so = _book(tmp_path)
    store = Store({})                       # счёт плоский
    assert _run(book, store) is True
    assert so.status == "armed", "сразу не списываем: нужна выдержка"
    assert so.flat_since_ms == NOW
    later = NOW + _ORPHAN_GRACE_MS + 1
    assert _run(book, store, later) is True
    assert so.status == "orphaned"
    assert "охранять нечего" in so.note


def test_stop_guarding_a_real_short_is_left_alone(tmp_path):
    """Шорт на месте — стоп на покупку его закрывает, трогать нельзя."""
    book, so = _book(tmp_path)
    store = Store({"RIZ6": -53})
    assert _run(book, store) is False
    assert so.status == "armed"
    assert so.flat_since_ms == 0


def test_blind_snapshot_never_retires_anything(tmp_path):
    """Пустой снимок значит «не знаю», а не «позиций нет».

    Судить по слепоте — значит снять ЖИВУЮ защиту у открытой позиции. Это
    потеря дороже той, от которой защищаемся.
    """
    book, so = _book(tmp_path)
    assert _run(book, Store(None), NOW + _ORPHAN_GRACE_MS * 10) is False
    assert so.status == "armed"
    assert so.flat_since_ms == 0


def test_position_coming_back_resets_the_counter(tmp_path):
    """Между филлами позиция мелькает нулём, а переворот идёт через ноль."""
    book, so = _book(tmp_path)
    assert _run(book, Store({}), NOW) is True
    assert so.flat_since_ms == NOW
    assert _run(book, Store({"RIZ6": -53}), NOW + 60_000) is True
    assert so.flat_since_ms == 0, "позиция вернулась — выдержку считаем заново"
    assert so.status == "armed"


def test_wrong_side_position_does_not_count_as_protected(tmp_path):
    """Стоп на ПОКУПКУ закрывает шорт. Лонг в рынке он не закрывает."""
    book, so = _book(tmp_path)                      # side=buy
    store = Store({"RIZ6": +3})                     # лонг робота
    assert _run(book, store) is True
    assert so.flat_since_ms == NOW
    assert _run(book, store, NOW + _ORPHAN_GRACE_MS + 1) is True
    assert so.status == "orphaned"


def test_entry_order_is_never_touched(tmp_path):
    """Вход с блоками после сделки — не защита, позиции у него и не должно быть."""
    book, so = _book(tmp_path, sl_offset=100.0)
    assert _run(book, Store({}), NOW + _ORPHAN_GRACE_MS * 10) is False
    assert so.status == "armed"


def test_corridor_and_grid_are_not_protective(tmp_path):
    """У коридора и сетки позиции может не быть по замыслу."""
    for kind in ("corridor", "triangle", "grid", "on_fill"):
        book, so = _book(tmp_path, kind=kind)
        assert _run(book, Store({}), NOW + _ORPHAN_GRACE_MS * 10) is False, kind
        assert so.status == "armed", kind


def test_child_of_a_parent_is_left_to_its_parent(tmp_path):
    book, so = _book(tmp_path, parent_id="abc123")
    assert _run(book, Store({}), NOW + _ORPHAN_GRACE_MS * 10) is False
    assert so.status == "armed"


def test_other_instrument_position_does_not_protect(tmp_path):
    """Шорт в SiZ6 не охраняет стоп по RIZ6."""
    book, so = _book(tmp_path)
    store = Store({"SiZ6": -53})
    assert _run(book, store, NOW) is True
    assert _run(book, store, NOW + _ORPHAN_GRACE_MS + 1) is True
    assert so.status == "orphaned"


# --------------------------------------------------------------------------
# ПРОВЕРКА ПРОВОДКИ, А НЕ ТОЛЬКО ФУНКЦИИ.
#
# Тесты выше зовут _retire_unprotecting напрямую — и остались бы зелёными, если
# бы сторож её не вызывал вовсе. Именно так 01.10.2026 лёг STL: у владельца были
# 34 зелёных теста на класс WsHub, а сломан был ВЫЗОВ в app.py, которого тесты
# не касались. Тест на функцию не проверяет, что её позвали.
# --------------------------------------------------------------------------

import pytest

from trader.api.quik_smart_orders import _watch_once


class _Srv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)


class _Permissive:
    """Всё, о чём тест не заботится, отвечает пустым словарём: он и итерируется
    пусто, и отвечает .get(None). Для теста ПРОВОДКИ это честно — утверждение
    тут одно, и лишние заглушки не должны его заслонять."""

    def __getattr__(self, name):
        return lambda *a, **k: {}


class _Ost(_Permissive):
    def working_orders(self, agent=None):
        return []

    def working_contracts(self, agent=None):
        return 0

    def placed_today(self, agent=None):
        return 0

    def register_pending(self, *a):
        pass

    def record_placement(self, agent):
        pass


class _Store(_Permissive, Store):
    """Снимок агента + минимум, который трогает проход сторожа."""

    def agents(self):
        return ["9618"]

    def status(self, agent=None):
        return {"quik": {"trades": [], "stop_orders": []}}

    def tick(self, code, agent=None):
        return {}

    def params(self, agent=None):
        return []

    def limits_state(self, agent=None):
        return {}

    def stop_orders(self, agent=None):
        return []

    def agent_status_age_ms(self, agent=None):
        return 0


class _Settings:
    quik_trading_enabled = True
    quik_max_contracts_per_order = 40
    quik_max_working_contracts = 70
    quik_price_collar_frac = 0.002
    quik_instrument_whitelist = "RIZ6"
    quik_daily_order_cap = 500

    def __getattr__(self, name):
        return None


class _State:
    settings = _Settings()

    def __getattr__(self, name):
        return None


@pytest.mark.asyncio
async def test_watcher_pass_actually_retires_the_orphan(tmp_path, monkeypatch):
    """Прогон ВСЕГО прохода сторожа: заявка без позиции обязана осиротеть."""
    book, so = _book(tmp_path)
    so.flat_since_ms = NOW - _ORPHAN_GRACE_MS - 1       # выдержка уже вышла

    st = _State()
    st.smart_orders = book
    st.quik_store = _Store({})                          # счёт плоский, снимок ЕСТЬ
    st.quik_order_store = _Ost()
    st.quik_server = _Srv()

    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    monkeypatch.setattr("trader.quik.smart_orders.now_ms", lambda: NOW)

    await _watch_once(st)
    assert so.status == "orphaned", (
        "проход сторожа не списал заявку, которой нечего охранять — "
        "значит вызов _retire_unprotecting потерян")


@pytest.mark.asyncio
async def test_watcher_pass_keeps_a_stop_that_guards_a_real_short(tmp_path, monkeypatch):
    """И обратное: живую защиту проход сторожа не трогает."""
    book, so = _book(tmp_path)
    so.flat_since_ms = NOW - _ORPHAN_GRACE_MS - 1

    st = _State()
    st.smart_orders = book
    st.quik_store = _Store({"RIZ6": -53})
    st.quik_order_store = _Ost()
    st.quik_server = _Srv()

    monkeypatch.setattr("trader.api.quik_smart_orders.resolve_agent",
                        lambda *a, **k: "9618")
    monkeypatch.setattr("trader.quik.smart_orders.now_ms", lambda: NOW)

    await _watch_once(st)
    # armed или native — и то и то ЖИВАЯ защита: при существующей позиции сторож
    # штатно отдаёт стоп терминалу (он переживает падение STL). Запрещено ровно
    # одно: осиротить заявку, которой есть что охранять.
    assert so.status in ("armed", "native"), so.status
    assert so.flat_since_ms == 0, "позиция на месте — выдержка сброшена"
