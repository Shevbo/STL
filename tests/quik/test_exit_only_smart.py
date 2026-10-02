"""Режим «только на выход» у умных заявок: закрыть позицию БЕЗ УБЫТКА.

Требование оператора 02.10.2026 дословно: «кнопка „только на выход“ — это значит
выход без убытка». У роботов такой режим с 28.07.2026 и служит тому же: вывести из
боя, не обрывая сделку. Отличие: там выход по сигналу стратегии и цена не
проверяется, здесь выход уровнями — цену проверяем.
"""

from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id
from trader.api.quik_smart_orders import _grid_sync, _walls_sync
from tests.quik.test_smart_grid import GLim, GOst, GSrv, GSTEPS, GNOW, _gstore


# ---- средняя цены позиции -----------------------------------------------

def test_average_follows_the_position_not_the_last_fill():
    """Доливка взвешивает среднюю, сокращение её не трогает, переворот обнуляет."""
    pos, avg = 0, 0.0
    pos, avg = so_mod.blend_avg(pos, avg, 1, 100.0, True)      # купили 1 по 100
    assert (pos, avg) == (1, 100.0)
    pos, avg = so_mod.blend_avg(pos, avg, 1, 200.0, True)      # долили 1 по 200
    assert (pos, avg) == (2, 150.0), "средняя взвешена по объёму"
    pos, avg = so_mod.blend_avg(pos, avg, 1, 999.0, False)     # продали 1
    assert (pos, avg) == (1, 150.0), "сокращение среднюю не двигает"
    pos, avg = so_mod.blend_avg(pos, avg, 3, 50.0, False)      # переворот в шорт
    assert (pos, avg) == (-2, 50.0), "старой позиции нет, средняя это цена новой"
    pos, avg = so_mod.blend_avg(pos, avg, 2, 70.0, True)       # закрылись в ноль
    assert (pos, avg) == (0, 0.0), "вне рынка средней нет"


def test_without_loss_is_direction_aware():
    assert so_mod.exit_without_loss(5, 100.0, 101.0) is True    # лонг продаём выше
    assert so_mod.exit_without_loss(5, 100.0, 99.0) is False
    assert so_mod.exit_without_loss(-5, 100.0, 99.0) is True    # шорт покупаем ниже
    assert so_mod.exit_without_loss(-5, 100.0, 101.0) is False
    assert so_mod.exit_without_loss(5, 100.0, 100.0) is True, "ровно средняя — не убыток"
    # средней нет: честное «нет», а не «можно»
    assert so_mod.exit_without_loss(5, 0.0, 100.0) is False
    assert so_mod.exit_without_loss(0, 100.0, 100.0) is False


# ---- сетка ---------------------------------------------------------------

def _grid(tmp_path, **kw):
    b = SmartOrderBook(str(tmp_path / "g.json"))
    args = dict(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                g_step=100.0, g_buys=5, g_sells=5, g_lot=1, g_base=85000.0,
                g_stop_pts=0.0, created_ms=GNOW, status="armed", exit_only=True)
    args.update(kw)
    so = SmartOrder(**args)
    b.orders.append(so)
    return b, so


def _placed(srv):
    return [m.place_order for m in srv.sent
            if m.WhichOneof("payload") == "place_order"]


def test_with_no_position_exit_only_places_nothing(tmp_path):
    """Закрывать нечего, а открывать запрещено — в стакан не идёт ничего."""
    book, so = _grid(tmp_path, g_pos=0, g_avg=0.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert _placed(srv) == []
    assert any(k.startswith("exit:") for k in so.g_live), "причина обязана быть в книге"


def test_only_the_closing_side_is_placed(tmp_path):
    """Позиция ШОРТ −2: закрывает её ПОКУПКА. Уровни-продажи доливали бы."""
    book, so = _grid(tmp_path, g_pos=-2, g_avg=85500.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    sides = {p.side for p in _placed(srv)}
    assert sides <= {1}, "в режиме выхода у шорта допустимы только покупки"
    assert _placed(srv), "закрывающие уровни обязаны стоять"


def test_a_level_worse_than_the_average_is_not_placed(tmp_path):
    """ГЛАВНОЕ ТРЕБОВАНИЕ: выход БЕЗ УБЫТКА. Шорт со средней 85100 закрывается
    покупкой НЕ ВЫШЕ 85100; уровень 85200 закрыл бы в минус."""
    book, so = _grid(tmp_path, g_pos=-2, g_avg=85100.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    for p in _placed(srv):
        assert p.price <= 85100.0, (
            f"покупка по {p.price:g} при средней шорта 85100 — это убыток")


def test_a_long_exits_only_above_its_average(tmp_path):
    book, so = _grid(tmp_path, g_pos=3, g_avg=85100.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85500.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    for p in _placed(srv):
        assert p.side == 2 and p.price >= 85100.0


def test_switching_the_mode_off_restores_normal_work(tmp_path):
    book, so = _grid(tmp_path, g_pos=0, g_avg=0.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert _placed(srv) == []
    so.exit_only = False
    srv2 = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv2, GLim(), "9618", GSTEPS, {},
               GNOW + 1000, True)
    assert _placed(srv2), "режим снят — сетка работает как обычно"


def test_the_reason_is_journalled_not_silent(tmp_path):
    """Оператор включил режим и ждёт выхода: «ничего не происходит» он обязан
    уметь объяснить по журналу, а не гадать."""
    book, so = _grid(tmp_path, g_pos=-2, g_avg=84000.0)   # рынок выше средней
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert _placed(srv) == [], "все уровни дороже средней — не выходим в убыток"
    assert any(k.startswith("exit:") for k in so.g_live)


# ---- коридор -------------------------------------------------------------

def test_corridor_closes_exactly_its_position(tmp_path):
    """Объём закрытия РОВНО позиция, а не удвоенный на переворот."""
    from tests.quik.test_walls_in_book import FakeOst, FakeSrv, FakeStore, Lim, _book
    book, so = _book(tmp_path)
    so.exit_only, so.c_pos, so.c_avg = True, -4, 86000.0
    srv = FakeSrv()
    _walls_sync(book, FakeStore(), FakeOst(), srv, Lim(), "9618", {"RIZ6": 10.0},
                {}, 1_790_800_000_000, None, True)
    placed = [m.place_order for m in srv.sent
              if m.WhichOneof("payload") == "place_order"]
    for p in placed:
        assert p.side == 1, "шорт закрывается покупкой"
        assert p.quantity == 4, "ровно позиция, без переворота"
        assert p.price <= 86000.0, "не выше средней — иначе убыток"


def test_a_zero_price_fill_never_poisons_the_average():
    """ЦЕНА НОЛЬ — НЕ ЦЕНА, ценой живой ошибки 02.10.2026.

    Запись о филле может прийти без цены, и такие нули утянули среднюю сетки
    67c52ac651 к 14211 при филлах 85270..86080 — в шесть раз мимо. С такой средней
    режим «только на выход» заблокировал бы ЛЮБОЙ выход: все цены «хуже средней».
    Позицию считаем всегда, среднюю трогаем только по настоящей цене.
    """
    pos, avg = so_mod.blend_avg(0, 0.0, 10, 85500.0, True)
    assert (pos, avg) == (10, 85500.0)
    pos, avg = so_mod.blend_avg(pos, avg, 10, 0.0, True)       # филл без цены
    assert pos == 20, "позиция обязана учесться"
    assert avg == 85500.0, "средняя не испорчена нулём"
    # выход в ноль обнуляет среднюю даже без цены
    pos, avg = so_mod.blend_avg(pos, avg, 20, 0.0, False)
    assert (pos, avg) == (0, 0.0)
