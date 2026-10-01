"""Коридор и треугольник держат заявки В СТАКАНЕ, на обеих стенках.

До 30.09.2026 сторож ждал касания и стрелял в тот же миг — то есть вставал в
очередь последним ровно там, где важна очередь. Оператор потребовал держать
заявки заранее. Стенки движутся, поэтому заявка не ставится заново, а
ПЕРЕСТАВЛЯЕТСЯ вслед за линией одной транзакцией: между «снять» и «поставить»
есть тик, в который защиты нет.
"""
from trader.api.quik_smart_orders import _walls_sync
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_800_000_000
HOUR = 3_600_000
STEPS = {"RIZ6": 10.0}


class FakeSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)

    def kinds(self):
        return [m.WhichOneof("payload") for m in self.sent]


class FakeOst:
    def __init__(self, recs=()):
        self.recs = list(recs)

    def working_orders(self, agent=None):
        return self.recs

    def working_contracts(self, agent=None):
        return 0

    def placed_today(self, agent=None):
        return 0

    def register_pending(self, *a):
        pass

    def record_placement(self, agent):
        pass


class FakeStore:
    def tick(self, code, agent=None):
        return {"last": 84500.0, "bid": 84490.0, "ask": 84510.0}


class Lim:
    price_collar_frac = 0.002
    trading_enabled = True
    instrument_whitelist = ("RIZ6",)
    max_contracts_per_order = 100
    max_working_contracts = 200
    daily_order_cap = 500


def _book(tmp_path, **kw):
    b = SmartOrderBook(str(tmp_path / "b.json"))
    args = dict(so_id=new_id(), kind="corridor", code="RIZ6", side="sell", qty=10,
                c_qty=10, c_t1_ms=NOW, c_p1=85000.0, c_t2_ms=NOW + HOUR,
                c_p2=85000.0, c_low=84000.0, created_ms=NOW)
    args.update(kw)
    so = SmartOrder(**args)
    b.orders.append(so)
    return b, so


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _store_is_warm(monkeypatch):
    """Замок прогрева склада заявок (после рестарта STL он пуст) к этим тестам
    не относится: они проверяют геометрию, а не момент после перезапуска.
    Отдельная проверка самого замка — test_nothing_is_placed_while_the_store_warms_up."""
    monkeypatch.setattr("trader.api.quik_smart_orders._PROC_START_MS", 0)


def _run(book, ost, srv, now=NOW, limits=None):
    return _walls_sync(book, FakeStore(), ost, srv, Lim(), "9618", STEPS,
                       limits or {}, now)


def test_both_walls_are_placed_up_front(tmp_path):
    book, so = _book(tmp_path)
    srv = FakeSrv()
    assert _run(book, FakeOst(), srv) is True
    assert srv.kinds() == ["place_order", "place_order"]
    got = {(m.place_order.side, round(m.place_order.price)) for m in srv.sent}
    assert got == {(2, 85000), (1, 84000)}, "верх продаёт, низ покупает"
    assert set(so.c_live) == {"top", "low"}
    assert all(m.place_order.quantity == 10 for m in srv.sent)


def test_moving_wall_is_replaced_not_recreated(tmp_path):
    """Наклонный коридор: стенка уехала — двигаем одной транзакцией."""
    book, so = _book(tmp_path, c_p2=86000.0)          # +1000 за час
    so.c_live = {"top": "so:x:top:1", "low": "so:x:low:1"}
    recs = [{"client_id": "so:x:top:1", "order_id": "11", "state": "active",
             "remaining": 10, "price": 85000.0},
            {"client_id": "so:x:low:1", "order_id": "12", "state": "active",
             "remaining": 10, "price": 84000.0}]
    srv = FakeSrv()

    class Mid(FakeStore):                      # рынок между стенками 84500 и 85500
        def tick(self, code, agent=None):
            return {"last": 85000.0, "bid": 84990.0, "ask": 85010.0}

    assert _walls_sync(book, Mid(), FakeOst(recs), srv, Lim(), "9618", STEPS, {},
                       NOW + HOUR // 2) is True
    assert srv.kinds() == ["replace_order", "replace_order"]
    prices = sorted(round(m.replace_order.new_price) for m in srv.sent)
    assert prices == [84500, 85500], "обе линии сдвинулись на полшага наклона"


def test_wall_we_already_traded_from_is_cancelled(tmp_path):
    """В шорте от верхней стенки заявки наверху быть не должно: она нарастила бы
    позицию там, где коридор только ждёт противоположную стенку."""
    book, so = _book(tmp_path, c_pos=-10)
    so.c_live = {"top": "so:x:top:1"}
    recs = [{"client_id": "so:x:top:1", "order_id": "11", "state": "active",
             "remaining": 10, "price": 85000.0}]
    srv = FakeSrv()
    assert _run(book, FakeOst(recs), srv) is True
    assert "cancel_order" in srv.kinds()
    assert "top" not in so.c_live
    # а внизу стоит заявка НА ПЕРЕВОРОТ — вдвое
    low = [m.place_order for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    assert low and low[0].quantity == 20


def test_level_beyond_exchange_limit_is_not_placed(tmp_path):
    book, _ = _book(tmp_path)
    srv = FakeSrv()
    # верхняя стенка 85000 за планкой 84800 — её не выставляем, нижнюю ставим
    assert _run(book, FakeOst(), srv, limits={"RIZ6": (83000.0, 84800.0)}) is True
    sides = [m.place_order.side for m in srv.sent]
    assert sides == [1], "только покупка снизу"


def test_order_creeps_after_a_falling_line_every_ten_seconds(tmp_path):
    """Линия нисходящая — заявка сползает вниз вслед за ней, но не чаще чем раз
    в десять секунд: наклонная ползёт непрерывно, и двигать заявку каждым
    проходом сторожа значит гонять транзакции ради долей шага."""
    # крутая линия: 1000 пунктов за 10 минут, то есть 1.67 пункта в секунду
    book, so = _book(tmp_path, c_t2_ms=NOW + 600_000, c_p2=84000.0)
    so.c_live = {"top": "so:x:top:1", "low": "so:x:low:1"}
    rec_top = {"client_id": "so:x:top:1", "order_id": "11", "state": "active",
               "remaining": 10, "price": 85000.0}
    rec_low = {"client_id": "so:x:low:1", "order_id": "12", "state": "active",
               "remaining": 10, "price": 84000.0}
    ost = FakeOst([rec_top, rec_low])

    srv = FakeSrv()
    assert _run(book, ost, srv, NOW + 60_000) is True          # минута: −100 пунктов
    first = [m.replace_order for m in srv.sent
             if m.WhichOneof("payload") == "replace_order"][0]
    assert first.new_price < 85000.0, "заявка поехала ВНИЗ за линией"
    rec_top["price"] = first.new_price                          # QUIK подтвердил перестановку

    srv2 = FakeSrv()
    assert _run(book, ost, srv2, NOW + 65_000) is False, "пять секунд — рано"
    assert srv2.sent == []

    srv3 = FakeSrv()
    assert _run(book, ost, srv3, NOW + 71_000) is True          # прошло 11 секунд
    second = [m.replace_order for m in srv3.sent
              if m.WhichOneof("payload") == "replace_order"][0]
    assert second.new_price < first.new_price, "сползла ещё ниже"


def test_rising_line_creeps_the_order_up(tmp_path):
    """Наклон вверх — заявка ползёт ВВЕРХ. Направление задаёт линия, а не сторона
    заявки: у восходящего канала и продажа сверху, и покупка снизу поднимаются."""
    book, so = _book(tmp_path, c_t2_ms=NOW + 600_000, c_p2=86000.0)   # +1000 за 10 мин
    so.c_live = {"top": "so:x:top:1", "low": "so:x:low:1"}
    rec_top = {"client_id": "so:x:top:1", "order_id": "11", "state": "active",
               "remaining": 10, "price": 85000.0}
    rec_low = {"client_id": "so:x:low:1", "order_id": "12", "state": "active",
               "remaining": 10, "price": 84000.0}
    srv = FakeSrv()
    assert _run(book, FakeOst([rec_top, rec_low]), srv, NOW + 60_000) is True
    moved = {round(m.replace_order.new_price) for m in srv.sent
             if m.WhichOneof("payload") == "replace_order"}
    assert moved == {85100, 84100}, "обе стенки поднялись на сто пунктов"


def test_wall_on_the_wrong_side_of_the_market_is_never_placed(tmp_path):
    """ГЛАВНЫЙ УРОК 30.09.2026, ценой 43 контракта оператора.

    Верхняя стенка треугольника оказалась НИЖЕ цены. Продажа на ней стала
    маркетабельной и исполнилась мгновенно, а сторож каждые десять секунд ставил
    следующую — сорок три сделки по одному лоту. Лимит на продажу обязан стоять
    ВЫШЕ рынка, на покупку НИЖЕ; иначе это не «ждём касания стенки», а вход по
    рынку прямо сейчас.
    """
    # стенки 85000 / 84000, а рынок уже 85500 — обе по ту сторону
    class HighStore(FakeStore):
        def tick(self, code, agent=None):
            return {"last": 85500.0, "bid": 85490.0, "ask": 85510.0}

    book, so = _book(tmp_path)
    srv = FakeSrv()
    out = _walls_sync(book, HighStore(), FakeOst(), srv, Lim(), "9618", STEPS, {}, NOW)
    placed = [m.place_order for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    # продажа по 85000 при рынке 85500 исполнилась бы мгновенно — её НЕ ставим;
    # покупка по 84000 ниже рынка законна и остаётся
    assert [p.side for p in placed] == [1], "только покупка снизу"
    assert so.c_live.get("cross:top") == 1, "верхняя помечена как пересекающая рынок"
    assert "cross:low" not in so.c_live
    assert out is True


def test_hanging_wall_is_pulled_when_market_crosses_it(tmp_path):
    """Рынок ушёл за стенку — висящую заявку снимаем, а не ждём исполнения."""
    class HighStore(FakeStore):
        def tick(self, code, agent=None):
            return {"last": 85500.0, "bid": 85490.0, "ask": 85510.0}

    book, so = _book(tmp_path)
    so.c_live = {"top": "so:x:top:1"}
    recs = [{"client_id": "so:x:top:1", "order_id": "11", "state": "active",
             "remaining": 10, "price": 85000.0}]
    srv = FakeSrv()
    _walls_sync(book, HighStore(), FakeOst(recs), srv, Lim(), "9618", STEPS, {}, NOW)
    assert "cancel_order" in [m.WhichOneof("payload") for m in srv.sent]
    assert "top" not in so.c_live


def test_no_quote_no_orders(tmp_path):
    """Без котировки не понять, по ту или эту сторону рынка стенка — молчим."""
    class Blind(FakeStore):
        def tick(self, code, agent=None):
            return {}

    book, _ = _book(tmp_path)
    srv = FakeSrv()
    assert _walls_sync(book, Blind(), FakeOst(), srv, Lim(), "9618", STEPS, {}, NOW) is False
    assert srv.sent == []


# --------------------------------------------------------------------------
# СВИП ПО ГЕОМЕТРИИ, А НЕ ПРИМЕР.
#
# Метод, введённый после 30.09.2026. Тестов на стенки было достаточно, и все
# они были зелёными, когда код продал оператору 43 контракта. Причина
# методическая: тесты проверяли ПОВЕДЕНИЕ («заявка переставилась на +100»), а
# FakeStore всегда отдавал last=84500 при стенках 85000/84000 — рынок ВСЕГДА
# внутри фигуры. Баг жил ровно в той геометрии, которую фикстура не умела
# построить.
#
# Тест на ожидаемое значение не может поймать состояние, не пришедшее в голову
# автору. Тест на ИНВАРИАНТ может: он перебирает состояния, а утверждает
# ЗАПРЕТ. Правило для всего, что порождает заявки: один такой свип на функцию.
# --------------------------------------------------------------------------

import pytest


def _store_at(px):
    class S(FakeStore):
        def tick(self, code, agent=None):
            return {"last": px, "bid": px - 10.0, "ask": px + 10.0}
    return S()


@pytest.mark.parametrize("market", range(82000, 88001, 250))
@pytest.mark.parametrize("c_pos", [0, 10, -10])
def test_no_wall_order_ever_crosses_the_market(tmp_path, market, c_pos):
    """ИНВАРИАНТ: ни при какой цене рынка и ни при какой позиции сторож не
    выставляет заявку, которая пересекает рынок.

    Продажа обязана стоять ВЫШЕ рынка, покупка НИЖЕ. Нарушение этого и есть
    инцидент 30.09.2026: маркетабельный лимит исполняется мгновенно, а сторож
    каждые десять секунд ставит следующий.
    """
    book, so = _book(tmp_path, c_pos=c_pos)
    srv = FakeSrv()
    _walls_sync(book, _store_at(float(market)), FakeOst(), srv, Lim(), "9618",
                STEPS, {}, NOW)

    ref_sell = max(market - 10.0, float(market))
    ref_buy = min(market + 10.0, float(market))
    for m in srv.sent:
        if m.WhichOneof("payload") != "place_order":
            continue
        p = m.place_order
        if p.side == 2:      # продажа
            assert p.price > ref_sell, (
                f"продажа по {p.price:g} при рынке {market} пересекает рынок")
        else:                # покупка
            assert p.price < ref_buy, (
                f"покупка по {p.price:g} при рынке {market} пересекает рынок")


@pytest.mark.parametrize("market", range(82000, 88001, 500))
def test_wall_order_never_grows_position_against_the_corridor(tmp_path, market):
    """ИНВАРИАНТ: в позиции сторож не доливает в ту же сторону.

    Коридор в лонге ждёт ВЕРХНЮЮ стенку, чтобы выйти; ещё одна покупка снизу
    превратила бы фигуру в усреднение, которого оператор не заказывал.
    """
    for c_pos, forbidden_side in ((10, 1), (-10, 2)):   # в лонге нельзя покупать
        book, so = _book(tmp_path, c_pos=c_pos)
        srv = FakeSrv()
        _walls_sync(book, _store_at(float(market)), FakeOst(), srv, Lim(),
                    "9618", STEPS, {}, NOW)
        sides = [m.place_order.side for m in srv.sent
                 if m.WhichOneof("payload") == "place_order"]
        assert forbidden_side not in sides, (
            f"позиция {c_pos:+d} при рынке {market}: заявка доливает в ту же сторону")


def test_nothing_is_placed_while_the_store_warms_up(tmp_path, monkeypatch):
    """ЗАМОК ПРОГРЕВА СКЛАДА ЗАЯВОК, ценой шести контрактов вместо одного.

    01.10.2026: склад заявок STL живёт в памяти и после рестарта пуст. Заявка
    коридора, стоявшая в QUIK с до-рестартного времени, для STL перестала
    существовать — и сторож поставил поверх живой ещё две (so:...:low:48830 и
    :low:5891, обе налились по 2), а сверху выстрелил сам. Один коридор на объём
    1 купил 6 контрактов.

    Мирно стоящая заявка не порождает обновлений, поэтому «подождём, агент
    пришлёт» не работает само: нужен явный запрет ставить, пока склад не прогрет.
    """
    import trader.api.quik_smart_orders as mod
    monkeypatch.setattr(mod, "_PROC_START_MS", NOW - 1000)   # процесс только что встал
    book, so = _book(tmp_path)
    srv = FakeSrv()
    _walls_sync(book, FakeStore(), FakeOst(), srv, Lim(), "9618", STEPS, {}, NOW)
    placed = [m for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    assert placed == [], "пока склад не прогрет, в стакан не ставим"

    # прогрелись — ставим как обычно
    monkeypatch.setattr(mod, "_PROC_START_MS", NOW - mod._ORDER_STORE_WARMUP_MS - 1)
    srv2 = FakeSrv()
    _walls_sync(book, FakeStore(), FakeOst(), srv2, Lim(), "9618", STEPS, {}, NOW)
    assert [m for m in srv2.sent if m.WhichOneof("payload") == "place_order"], \
        "после прогрева стенки обязаны встать"
