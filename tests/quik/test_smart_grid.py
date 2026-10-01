"""Сетка «радиация»: заявки живут В СТАКАНЕ и восстанавливаются сами.

Заказ оператора 30.09.2026. Уровни фиксированы от цены постановки; исполнилась
заявка — на ТОТ ЖЕ уровень встаёт встречная (купленное на −2 продаётся там же:
это и есть тейк в один шаг сетки). Стоп за последним уровнем закрывает работу.
Уровень за планкой биржи не выставляется, а ждёт расширения границ: MOEX двигает
планки по своему расписанию, и заявку за ними просто не примут.
"""
from trader.api.quik_smart_orders import price_within_limits
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, new_id


def _grid(**kw):
    base = dict(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                g_step=100.0, g_buys=3, g_sells=2, g_lot=2, g_base=84000.0,
                g_stop_pts=150.0, created_ms=0)
    base.update(kw)
    return SmartOrder(**base)


def test_levels_and_prices_are_fixed_around_the_base():
    so = _grid()
    assert sorted(so_mod.grid_levels(so)) == [-3, -2, -1, 0, 1, 2]
    assert so_mod.grid_price(so, -1) == 83900 and so_mod.grid_price(so, 2) == 84200
    # низ покупает, верх продаёт
    assert so_mod.grid_side_for(so, -1) == "buy"
    assert so_mod.grid_side_for(so, 2) == "sell"


def test_filled_level_goes_dark_until_a_neighbour_fills():
    """МЕХАНИКА «РАДИАЦИИ» СО СЛОВ ОПЕРАТОРА 01.10.2026, после реального
    сжигания комиссии.

    Тейка в радиации НЕТ, есть уровни. Уровень, на котором произошёл филл,
    ИСЧЕЗАЕТ и возвращается только после филла СОСЕДНЕГО уровня — любого, хоть
    ниже, хоть выше.

    Прежняя моя конструкция ставила встречную заявку НА ТУ ЖЕ ЦЕНУ: купил по
    86080 — туда же продажа по 86080. Круг с нулевой прибылью и двойной
    комиссией; в этот день уровень −1 так отработал трижды подряд.
    """
    live = {"flip:-2": True}
    assert so_mod.grid_places_here(live, -2) is False, "погасший уровень не выставляется"
    assert so_mod.grid_places_here(live, -1) is True, "соседний живёт своей жизнью"
    assert so_mod.grid_places_here(live, -3) is True


def test_side_follows_the_market_not_the_ladder():
    """Сторона уровня определяется тем, по какую сторону РЫНКА он оказался:
    выше рынка продаём, ниже покупаем. Это же правило не даёт заявке пересечь
    рынок — лимит по ту сторону исполнился бы мгновенно (30.09, 43 контракта)."""
    so = _grid()                                   # база 84000, шаг 100
    assert so_mod.grid_price(so, -1) == 83900
    # рынок УПАЛ ниже уровня −1: теперь это продажа, а не покупка
    assert so_mod.grid_side_for(so, -1, 83800) == "sell"
    # рынок выше уровня −1: покупка, как в исходной лестнице
    assert so_mod.grid_side_for(so, -1, 84050) == "buy"
    # без цены остаётся лестница от базы
    assert so_mod.grid_side_for(so, -1) == "buy"
    assert so_mod.grid_side_for(so, 2) == "sell"


def test_base_level_is_a_level_too():
    """ДЫРА, УВИДЕННАЯ ОПЕРАТОРОМ 01.10.2026 в живой сетке: база 85460, вокруг
    стоят 85360 и 85560, а на самой 85460 пусто. Я исключил ноль из лестницы,
    решив, что на цене постановки заявки быть не должно. Это такой же уровень:
    рынок с него уходит, и тогда там обязана стоять заявка."""
    so = _grid()                                    # база 84000
    assert 0 in so_mod.grid_levels(so)
    assert so_mod.grid_price(so, 0) == so.g_base
    # сторона по рынку, как у любого уровня
    assert so_mod.grid_side_for(so, 0, 84050) == "buy"
    assert so_mod.grid_side_for(so, 0, 83950) == "sell"


def test_stop_sits_beyond_the_last_level_on_both_sides():
    so = _grid()
    lo, hi = so_mod.grid_stop_levels(so)
    assert (lo, hi) == (84000 - 300 - 150, 84000 + 200 + 150)
    assert so_mod.grid_stop_hit(so, 83540) is True      # ниже нижнего края (83550)
    assert so_mod.grid_stop_hit(so, 83560) is False     # ещё внутри
    assert so_mod.grid_stop_hit(so, 84360) is True      # выше верхнего (84350)
    assert so_mod.grid_stop_hit(so, 84000) is False
    assert so_mod.grid_stop_hit(_grid(g_stop_pts=0), 99999) is False, "стоп выключен"


def test_exchange_price_limits_hold_a_level_back():
    """За планкой заявку не примут — держим у себя и ждём расширения."""
    assert price_within_limits(84200, (83000.0, 85000.0)) is True
    assert price_within_limits(85200, (83000.0, 85000.0)) is False
    assert price_within_limits(82000, (83000.0, 85000.0)) is False
    # границы неизвестны — НЕ ограничиваем: молчащий параметр не должен
    # останавливать торговлю
    assert price_within_limits(99999, None) is True
    assert price_within_limits(99999, (0.0, 0.0)) is True


def test_validation_demands_the_whole_grid():
    assert _grid(g_step=0).validate() is not None
    assert _grid(g_buys=0, g_sells=0).validate() is not None
    assert _grid(g_lot=0).validate() is not None
    assert _grid(g_base=0).validate() is not None
    assert _grid(sl_offset=100).validate() is not None, "сетка ведёт позицию сама"
    assert _grid().validate() is None
    # односторонняя сетка законна: только покупки или только продажи
    assert _grid(g_sells=0).validate() is None


# --------------------------------------------------------------------------
# СВИП ПО ГЕОМЕТРИИ для сторожа сетки.
#
# Уровни сетки считались чистыми функциями и были покрыты, а САМ СТОРОЖ
# (_grid_sync) — тот, кто превращает уровень в заявку, — не был покрыт ничем.
# Проверка «уровень по ту сторону рынка» жила там без единого теста, хотя
# добавлена она была тем же фиксом, что и у стенок, и по той же причине:
# 30.09.2026, 43 проданных контракта.
# --------------------------------------------------------------------------

import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik.smart_orders import SmartOrderBook

GNOW = 1_790_800_000_000
GSTEPS = {"RIZ6": 10.0}


class GSrv:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append(msg)


class GOst:
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


class GLim:
    price_collar_frac = 0.002
    trading_enabled = True
    instrument_whitelist = ("RIZ6",)
    max_contracts_per_order = 100
    max_working_contracts = 500
    daily_order_cap = 500


def _gstore(px, terminal_orders=()):
    """Зеркало агента: котировка И таблица заявок терминала.

    Таблица обязательна. Сторож решает, ставить ли уровень, по ней (её отсутствие
    = «не знаю, что в QUIK» = не ставим), поэтому фейк БЕЗ таблицы делает любой
    тест «сетка не поставила ничего лишнего» зелёным ВХОЛОСТУЮ. Этот файл уже
    получал такой урок: тесты на стенки были зелёными, когда код продал оператору
    43 контракта, — потому что фикстура не умела построить нужную геометрию.
    Поэтому свип ниже отдельно требует, чтобы заявки ВООБЩЕ ставились."""
    rows = list(terminal_orders)

    class S:
        def tick(self, code, agent=None):
            return {"last": px, "bid": px - 10.0, "ask": px + 10.0}

        def agent_status(self, agent=None):
            # health.ord_age_ms обязателен — см. terminal.fresh: пустой orders есть
            # в снимке агента всегда, даже до первого кадра от QLua.
            return {"_received_at_ms": GNOW, "health": {"ord_age_ms": 1200},
                    "quik": {"orders": rows}}
    return S()


def _gterm_row(num, side, price, qty=1, tag="", active=True):
    return {"num": num, "sec": "RIZ6", "side": side, "price": price, "qty": qty,
            "balance": qty, "active": active, "tag": tag, "ts_ms": GNOW}


def _gbook(tmp_path):
    b = SmartOrderBook(str(tmp_path / "g.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                    g_step=100.0, g_buys=5, g_sells=5, g_lot=1, g_base=85000.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed")
    b.orders.append(so)
    return b, so


@pytest.mark.parametrize("market", range(84000, 86001, 100))
def test_no_grid_order_ever_crosses_the_market(tmp_path, market):
    """ИНВАРИАНТ: ни при какой цене рынка сетка не ставит заявку, пересекающую
    рынок. Уровни сетки ФИКСИРОВАНЫ, поэтому при любом сдвиге цены часть из них
    неизбежно оказывается по ту сторону — и именно они не имеют права стрелять.
    """
    book, so = _gbook(tmp_path)
    srv = GSrv()
    _grid_sync(book, _gstore(float(market)), GOst(), srv, GLim(), "9618",
               GSTEPS, {}, GNOW)
    placed = [m.place_order for m in srv.sent
              if m.WhichOneof("payload") == "place_order"]
    # ИНВАРИАНТ, ВЫПОЛНЕННЫЙ ПУСТОТОЙ, НИЧЕГО НЕ ДОКАЗЫВАЕТ. Уровни сетки покрывают
    # 85000±500, и при любом рынке внутри прогона часть из них законна — если не
    # поставлено НИ ОДНОЙ заявки, значит сломался сам прогон, а не геометрия.
    assert placed, f"при рынке {market} сетка не поставила ни одной заявки"
    for m in srv.sent:
        if m.WhichOneof("payload") != "place_order":
            continue
        p = m.place_order
        if p.side == 2:
            assert p.price > market, (
                f"продажа по {p.price:g} при рынке {market} пересекает рынок")
        else:
            assert p.price < market, (
                f"покупка по {p.price:g} при рынке {market} пересекает рынок")


def test_grid_places_nothing_without_a_quote(tmp_path):
    """Без котировки не понять, по какую сторону рынка уровень: не стреляем."""
    class Blind:
        def tick(self, code, agent=None):
            return {}

    book, _ = _gbook(tmp_path)
    srv = GSrv()
    _grid_sync(book, Blind(), GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)
    placed = [m for m in srv.sent if m.WhichOneof("payload") == "place_order"]
    assert placed == [], "вслепую сетка не выставляется"


def test_fill_extinguishes_its_level_and_revives_the_neighbour(tmp_path):
    """ПРОВОДКА, а не чистая функция: прогон сторожа с исполненным уровнем.

    Филл на уровне −1 обязан (1) погасить сам уровень −1 и (2) ВЕРНУТЬ соседей,
    если они были погашены раньше. Без второго сетка угасала бы уровень за
    уровнем и переставала работать после первого прохода цены.
    """
    book, so = _gbook(tmp_path)
    so.g_live = {"-1": "so:x:gm1", "flip:-2": True}      # сосед −2 погашен ранее
    srv = GSrv()
    ost = GOst()
    ost.working_orders = lambda agent=None: [
        {"client_id": "so:x:gm1", "order_id": "11", "state": "filled",
         "filled": 1, "remaining": 0, "price": 85000.0}]
    _grid_sync(book, _gstore(85000.0), ost, srv, GLim(), "9618", GSTEPS, {}, GNOW)

    assert so.g_live.get("flip:-1") is True, "исполненный уровень обязан погаснуть"
    assert "flip:-2" not in so.g_live, "сосед обязан вернуться после филла рядом"
    assert so.g_pos != 0, "позиция сетки должна измениться на филле"


def test_level_filled_this_pass_is_not_replaced_in_the_same_pass(tmp_path):
    """ТОТ САМЫЙ БАГ, 01.10.2026: уровень продал ДВА контракта вместо одного.

    Филл пишет пометку «уровень погас» в ЛОКАЛЬНУЮ копию состояния, а обратно в
    заявку она ложится только в конце прохода. Проверка «ставить ли здесь»
    читала СОХРАНЁННОЕ состояние — и в пределах того же прохода уровень выглядел
    пустым. Сторож ставил новую заявку в ту же секунду, она тоже наливалась:
    so:bf61895ca3:gp1:68221 налилась 13:29:51, :91790 выставлена в ту же секунду
    и налилась 13:29:52.

    Проверяем проход целиком: пока филл и проверка читают разные копии, чистые
    функции этого не видят.
    """
    book, so = _gbook(tmp_path)                    # база 85000, шаг 100
    lvl_price = so_mod.grid_price(so, 1)           # уровень +1 = 85100
    so.g_live = {"1": "so:x:gp1"}
    srv = GSrv()
    ost = GOst()
    ost.working_orders = lambda agent=None: [
        {"client_id": "so:x:gp1", "order_id": "11", "state": "filled",
         "filled": 1, "remaining": 0, "price": lvl_price}]
    # рынок НИЖЕ уровня: продажа на нём законна и ничего другого её не блокирует
    _grid_sync(book, _gstore(lvl_price - 50), ost, srv, GLim(), "9618", GSTEPS, {}, GNOW)

    again = [m.place_order for m in srv.sent
             if m.WhichOneof("payload") == "place_order"
             and abs(m.place_order.price - lvl_price) < 1e-6]
    assert again == [], (
        f"на только что исполнившийся уровень {lvl_price:g} поставлена новая заявка — "
        "это и есть второй контракт вместо одного")
    assert so.g_live.get("flip:1") is True, "уровень обязан погаснуть"


def test_fill_side_comes_from_the_order_not_from_the_current_price(tmp_path):
    """ПОЗИЦИЯ СЕТКИ СЧИТАЛАСЬ С НЕВЕРНЫМ ЗНАКОМ, 01.10.2026.

    Сторону исполнения я выводил заново через grid_side_for по ТЕКУЩЕЙ цене, а
    она с момента постановки уезжает. Журнал записал «уровень +0 (85580)
    исполнен buy 1; позиция +1», тогда как сделка была ПРОДАЖА. От позиции сетки
    считаются объём и стоп, поэтому неверный знак тянет за собой всё остальное.

    Что исполнилось — знает запись заявки, и только она.
    """
    book, so = _gbook(tmp_path)                     # база 85000
    so.g_live = {"1": "so:x:gp1"}
    srv = GSrv()
    ost = GOst()
    ost.working_orders = lambda agent=None: [
        {"client_id": "so:x:gp1", "order_id": "11", "state": "filled",
         "side": "sell", "filled": 1, "remaining": 0, "price": 85100.0}]
    # рынок УШЁЛ ВЫШЕ уровня: по текущей цене уровень выглядел бы покупкой
    _grid_sync(book, _gstore(85300.0), ost, srv, GLim(), "9618", GSTEPS, {}, GNOW)
    assert so.g_pos == -1, (
        f"позиция {so.g_pos:+d}: сторона взята из рынка, а не из исполненной заявки")


# --------------------------------------------------------------------------
# АМНЕЗИЯ: уровень, живой в QUIK и невидимый для STL.
# --------------------------------------------------------------------------


def test_level_standing_in_the_terminal_is_not_placed_twice(tmp_path):
    """ТОТ САМЫЙ УЩЕРБ 01.10.2026: сетка из 24 заявок УДВОИЛАСЬ после рестарта.

    Склад заявок STL живёт в памяти и пустеет при перезапуске, а мирно стоящая
    заявка обновлений не порождает — значит сама о себе не напомнит никогда.
    Сторож видел «записи нет» и ставил второй уровень поверх живого.

    Лечится вопросом к таблице заявок ТЕРМИНАЛА: она живёт в QUIK. Уровень
    опознаётся по ЦЕНЕ — client_id в brokerref не влезает (20 символов).
    """
    book, so = _gbook(tmp_path)                       # база 85000, шаг 100
    px = so_mod.grid_price(so, -1)                    # 84900, ниже рынка = покупка
    store = _gstore(85000.0, [
        _gterm_row("701", "buy", px, tag=f"stl-so-{so.so_id}")])
    srv = GSrv()
    # ни книга, ни склад заявок о заявке не знают — состояние после рестарта
    _grid_sync(book, store, GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)

    dup = [m.place_order for m in srv.sent
           if m.WhichOneof("payload") == "place_order"
           and abs(m.place_order.price - px) < 1e-6]
    assert dup == [], (
        f"на уровень {px:g}, где в терминале УЖЕ стоит заявка 701, поставлена "
        "вторая — это и есть удвоение сетки после рестарта")
    # Отмечен НОМЕРОМ: по нему догоняется филл этой заявки — своего client_id у
    # подхваченной нет, и склад заявок о её исполнении не узнает никогда.
    assert so.g_live.get("adopt:-1") == {"num": "701"}, "подхват отмечен номером заявки"
    # остальные уровни при этом обязаны встать: подхват одного не глушит сетку
    assert [m for m in srv.sent if m.WhichOneof("payload") == "place_order"]


def test_grid_places_nothing_when_the_terminal_table_is_unknown(tmp_path):
    """Зеркало агента молчит — что стоит в QUIK неизвестно. Прежний замок ждал
    90 секунд от старта процесса; заявке в QUIK пережить их ничего не стоит, она
    живёт сутками. Запрет теперь держится, пока таблицы нет."""
    class NoMirror:
        def tick(self, code, agent=None):
            return {"last": 85000.0, "bid": 84990.0, "ask": 85010.0}

    book, _ = _gbook(tmp_path)
    srv = GSrv()
    _grid_sync(book, NoMirror(), GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)
    assert [m for m in srv.sent if m.WhichOneof("payload") == "place_order"] == [],         "без таблицы заявок терминала сетка не ставит ничего"


def test_another_smart_orders_level_at_the_same_price_is_not_adopted(tmp_path):
    """Подхват только по СВОЕМУ тегу: заявка соседней умной заявки или оператора
    на той же цене своей не является. Иначе сетка перестала бы держать уровень,
    решив, что он уже стоит."""
    book, so = _gbook(tmp_path)
    px = so_mod.grid_price(so, -1)
    store = _gstore(85000.0, [
        _gterm_row("702", "buy", px, tag="stl-so-ffffffff01"),
        _gterm_row("703", "buy", px, tag=""),
    ])
    srv = GSrv()
    _grid_sync(book, store, GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)
    mine = [m.place_order for m in srv.sent
            if m.WhichOneof("payload") == "place_order"
            and abs(m.place_order.price - px) < 1e-6]
    assert len(mine) == 1, "чужая заявка на той же цене не отменяет нашего уровня"
    assert not any(k.startswith("adopt:") for k in so.g_live)


def test_grid_stop_withdraws_orders_nobody_remembers(tmp_path):
    """СТОП СЕТКИ НЕ ВПРАВЕ ЗАБЫТЬ ЖИВЫЕ ЗАЯВКИ.

    Здесь был свой проход по своим записям: снимал только известное складу, а
    следующей строкой ставил g_done — то есть объявлял сетку снятой и забывал
    остальное НАВСЕГДА. После рестарта складу не известно ничего, и 24 заявки
    остались бы торговать без присмотра.
    """
    book, so = _gbook(tmp_path)
    so.g_stop_pts = 150.0                             # стоп за краем сетки включён
    store = _gstore(84000.0, [                        # рынок ушёл далеко вниз
        _gterm_row("801", "buy", 84900.0, tag=f"stl-so-{so.so_id}"),
        _gterm_row("802", "buy", 84800.0, tag=f"stl-so-{so.so_id}"),
        _gterm_row("803", "sell", 85100.0, tag=""),   # чужая: не трогаем
    ])
    srv = GSrv()
    _grid_sync(book, store, GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)
    assert so.g_done is True and so.status == "cancelled"
    nums = sorted(m.cancel_order.order_id for m in srv.sent
                  if m.WhichOneof("payload") == "cancel_order")
    assert nums == ["801", "802"], "стоп обязан снять ВСЕ свои заявки из терминала"
    assert all(m.cancel_order.code == "RIZ6" for m in srv.sent
               if m.WhichOneof("payload") == "cancel_order")


def test_a_fill_of_an_adopted_level_is_caught_up_from_the_table(tmp_path):
    """ПОДХВАЧЕННЫЙ УРОВЕНЬ ПОСЛЕ ФИЛЛА ОБЯЗАН ПОГАСНУТЬ, а не встать заново.

    Подхваченной после рестарта заявке STL не возвращает client_id (brokerref QUIK,
    20 символов), значит склад заявок о её исполнении не узнает НИКОГДА. Без догона
    по таблице уровень переставлялся на ТУ ЖЕ ЦЕНУ: круг с нулевой прибылью и
    двойной комиссией, который механика радиации прямо запрещает, а g_pos оставался
    ложным — от него считаются и объём, и стоп. Сегодня так повели бы себя все 25
    живых заявок сетки после рестарта STL.

    Исполнившаяся строка из таблицы не исчезает: становится НЕАКТИВНОЙ, и
    qty - balance говорит, сколько налилось.
    """
    book, so = _gbook(tmp_path)                       # база 85000, шаг 100
    px = so_mod.grid_price(so, -1)                    # 84900
    so.g_live = {"adopt:-1": {"num": "701"}}
    store = _gstore(85000.0, [
        _gterm_row("701", "buy", px, qty=1, tag="stl-so-" + so.so_id, active=False)])
    store_rows = store.agent_status()["quik"]["orders"]
    store_rows[0]["balance"] = 0                      # налилась целиком
    srv = GSrv()
    _grid_sync(book, store, GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)

    assert so.g_pos == 1, f"позиция {so.g_pos:+d}: филл подхваченного уровня не учтён"
    assert so.g_live.get("flip:-1") is True, "исполненный уровень обязан погаснуть"
    again = [m.place_order for m in srv.sent
             if m.WhichOneof("payload") == "place_order"
             and abs(m.place_order.price - px) < 1e-6]
    assert again == [], (
        f"на только что исполнившийся уровень {px:g} поставлена новая заявка — "
        "это круг с нулевой прибылью и двойной комиссией")


def test_an_adopted_level_row_gone_from_the_table_is_reported(tmp_path):
    """Строки нет в таблице вовсе (за капом истории, сменился день): исполнение не
    учтено, и молчать об этом нельзя. Уровень при этом освобождается."""
    book, so = _gbook(tmp_path)
    so.g_live = {"adopt:-1": {"num": "999"}}
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {}, GNOW)
    assert so.g_pos == 0, "сколько налилось — неизвестно, не выдумываем"
    assert "adopt:-1" not in so.g_live
