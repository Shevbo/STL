"""Режим «только на выход» у умных заявок: закрыть позицию БЕЗ УБЫТКА.

Требование оператора 02.10.2026 дословно: «кнопка „только на выход“ — это значит
выход без убытка». У роботов такой режим с 28.07.2026 и служит тому же: вывести из
боя, не обрывая сделку. Отличие: там выход по сигналу стратегии и цена не
проверяется, здесь выход уровнями — цену проверяем.
"""
import pathlib
import random

import pytest

from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id
from trader.api.quik_smart_orders import _grid_count_fill, _grid_sync, _walls_sync
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


def test_losing_position_waits_at_its_own_average(tmp_path):
    """Оператор 02.10.2026: «только на выход — это выход только из ТЕКУЩЕЙ позиции
    без убытка». Шорт −2 по 84000 при рынке 85000 в минусе: выход стоит ОДНОЙ
    заявкой покупки 2 ровно по средней 84000 и ждёт, а не уровнями и не в убыток."""
    book, so = _grid(tmp_path, g_pos=-2, g_avg=84000.0)
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    placed = _placed(srv)
    assert len(placed) == 1, "выход — одна заявка на всю позицию"
    assert placed[0].side == 1 and placed[0].quantity == 2 and placed[0].price == 84000.0


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


def test_a_position_without_a_known_average_is_not_weighted_against_zero():
    """ПОЗИЦИЯ ЕСТЬ, СРЕДНЕЙ НЕТ — ноль это не цена, а отсутствие знания.

    02.10.2026 сетка 41bf3af0dd пришла с позицией +3 и средней 0 (её завели до
    того, как средняя вообще появилась). Каждый филл по 84840-84940 взвешивался
    против нуля, и средняя уехала к 56476 при реальных ~84900. С такой средней
    режим «только на выход» либо не выпустит никогда, либо выпустит в убыток.
    """
    pos, avg = so_mod.blend_avg(3, 0.0, 1, 84900.0, True)
    assert pos == 4
    assert avg == 84900.0, f"средняя {avg}: взвесили против нуля"
    # дальше считается уже нормально
    pos, avg = so_mod.blend_avg(pos, avg, 4, 85100.0, True)
    assert pos == 8 and avg == 85000.0


# --------------------------------------------------------------------------
# ЗАЩИТА СЕТКИ: сама переводит в «только на выход» после N филлов или ухода цены.
# Спецификация окна backtests, решение оператора 02.10.2026. Числа бэктеста:
# убыток на отложенной трети в 11-15 раз меньше (−288/−399/−366 -> −23/−27/−27
# тыс ₽, уже с комиссией), 81% закрытий по безубытку. Это ограничитель ущерба,
# прибыльной сетку он не делает.
# --------------------------------------------------------------------------


def _guarded(tmp_path, **kw):
    b = SmartOrderBook(str(tmp_path / "gg.json"))
    args = dict(so_id=new_id(), kind="grid", code="RIZ6", side="buy", qty=1,
                g_step=100.0, g_buys=5, g_sells=5, g_lot=1, g_base=85000.0,
                g_stop_pts=0.0, created_ms=GNOW, status="armed",
                g_trig_fills=3, g_trig_move_pct=0.25)
    args.update(kw)
    so = SmartOrder(**args)
    b.orders.append(so)
    return b, so


def test_guard_fires_on_the_third_filled_level(tmp_path):
    book, so = _guarded(tmp_path, g_fills_done=2, g_pos=-2, g_avg=85100.0)
    _grid_sync(book, _gstore(85000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.exit_only is False, "на двух филлах защита молчит"
    so.g_fills_done, so.g_pos = 3, -3
    _grid_sync(book, _gstore(85000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW + 1000, True)
    assert so.exit_only is True and so.g_trig_ms, "третий филл обязан включить защиту"


def test_guard_fires_on_price_move_from_base(tmp_path):
    """0.25% от 85000 это 212 пунктов. 84700 — ушли на 0.35%. Порог по уровням
    выключен, чтобы проверялось только условие по цене."""
    book, so = _guarded(tmp_path, g_trig_fills=0, g_pos=4, g_avg=85100.0)
    _grid_sync(book, _gstore(84900.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.exit_only is False, "0.12% — порог не достигнут"
    _grid_sync(book, _gstore(84700.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW + 1000, True)
    assert so.exit_only is True, "0.35% — защита обязана включиться"


def test_zero_switches_each_condition_off(tmp_path):
    book, so = _guarded(tmp_path, g_trig_fills=0, g_trig_move_pct=0.0,
                        g_fills_done=99, g_pos=5, g_avg=85100.0)
    _grid_sync(book, _gstore(80000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.exit_only is False, "оба условия выключены нулём"


def test_guard_cancels_standing_entry_orders(tmp_path):
    """Стоящие заявки — это входные уровни. Оставить их значило бы продолжать
    набор, против которого защита и заведена."""
    book, so = _guarded(tmp_path, g_fills_done=3, g_pos=-3, g_avg=85100.0)
    so.g_live = {"-1": "so:x:gm1", "2": "so:x:gp2", "flip:3": True}
    ost = GOst()
    ost.working_orders = lambda agent=None: [
        {"client_id": "so:x:gm1", "order_id": "11", "state": "active",
         "remaining": 1, "filled": 0, "price": 84900.0},
        {"client_id": "so:x:gp2", "order_id": "12", "state": "active",
         "remaining": 1, "filled": 0, "price": 85200.0}]
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), ost, srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    killed = [m.cancel_order.order_id for m in srv.sent
              if m.WhichOneof("payload") == "cancel_order"]
    assert sorted(killed) == ["11", "12"], "входные уровни обязаны быть сняты"
    assert so.g_live.get("flip:3") is True, "бухгалтерию погасших уровней не трогаем"


def test_guard_reports_which_condition_fired(tmp_path):
    """Оператор, увидев остановку набора, обязан прочитать ПРИЧИНУ и число."""
    so1 = _guarded(tmp_path / "a", g_fills_done=5, g_pos=5)[1]
    assert "в одну сторону 5" in so_mod.grid_guard_hit(so1, 85000.0)
    so2 = _guarded(tmp_path / "b", g_trig_fills=0)[1]
    why = so_mod.grid_guard_hit(so2, 84000.0)
    assert "ушла от базы" in why and "%" in why


def test_after_the_guard_only_the_closing_side_at_no_loss_is_placed(tmp_path):
    """Защита включилась — дальше работают правила «только на выход»."""
    book, so = _guarded(tmp_path, g_fills_done=3, g_pos=-3, g_avg=85100.0)
    _grid_sync(book, _gstore(85000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.exit_only is True
    srv = GSrv()
    _grid_sync(book, _gstore(85000.0), GOst(), srv, GLim(), "9618", GSTEPS, {},
               GNOW + 2000, True)
    for p in [m.place_order for m in srv.sent
              if m.WhichOneof("payload") == "place_order"]:
        assert p.side == 1, "шорт закрывается покупкой"
        assert p.price <= 85100.0, "и только не выше средней"


# --------------------------------------------------------------------------
# ПЕРЕВЗВЕДЕНИЕ ПОСЛЕ ЧИСТОГО ВЫХОДА. Требование оператора 02.10.2026: выйдя по
# защите без убытка, та же сетка через заданный срок встаёт снова. Защита убирает
# сетку из ТРЕНДА, а не из рынка.
# --------------------------------------------------------------------------


def test_rearm_is_scheduled_only_after_a_clean_exit(tmp_path):
    """Срок назначается, когда позиция ДЕЙСТВИТЕЛЬНО вышла в ноль, а не когда
    защита просто включилась: иначе сетка встала бы снова поверх незакрытой."""
    book, so = _guarded(tmp_path, g_rearm_min=30, g_pos=-2, g_avg=85100.0,
                        exit_only=True, g_trig_ms=GNOW - 1000)
    _grid_sync(book, _gstore(85000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.g_rearm_at_ms == 0, "позиция не закрыта — перевзведение не назначаем"
    so.g_pos = 0
    _grid_sync(book, _gstore(85000.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW + 1000, True)
    assert so.g_rearm_at_ms == GNOW + 1000 + 30 * 60_000


def test_rearm_rebases_on_the_current_price(tmp_path):
    """База берётся ТЕКУЩАЯ. Сетку выключило тем, что рынок ушёл от базы; вернуть
    уровни туда, где рынка нет, значит выставить половину по ту сторону цены и
    мгновенно налиться — это уже стоило 43 контрактов 30.09.2026."""
    book, so = _guarded(tmp_path, g_rearm_min=1, g_pos=0, g_avg=0.0,
                        exit_only=True, g_trig_ms=GNOW - 1000,
                        g_rearm_at_ms=GNOW - 1, g_fills_done=7, g_base=85000.0)
    so.g_live = {"flip:2": True, "adopt:3": {"num": "1"}}
    _grid_sync(book, _gstore(84300.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.g_base == 84300.0, "база обязана переехать на текущую цену"
    assert so.exit_only is False and so.g_trig_ms == 0
    assert so.g_fills_done == 0, "счётчик филлов обнулён, иначе защита сработает сразу"
    assert so.g_rearms == 1 and so.g_rearm_at_ms == 0
    # Бухгалтерия ПРОШЛОЙ базы ушла. Пустым g_live не будет: перевзведённая сетка
    # в том же проходе выставляет уровни от новой базы — это и есть её работа.
    assert "flip:2" not in so.g_live and "adopt:3" not in so.g_live, (
        "погасшие уровни и подхваты прошлой базы к новой отношения не имеют")


def test_rearm_waits_for_its_time(tmp_path):
    book, so = _guarded(tmp_path, g_rearm_min=30, g_pos=0, exit_only=True,
                        g_trig_ms=GNOW - 1000, g_rearm_at_ms=GNOW + 600_000)
    _grid_sync(book, _gstore(84300.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.exit_only is True and so.g_rearms == 0, "срок не вышел — ждём"


def test_zero_means_the_exit_is_final(tmp_path):
    book, so = _guarded(tmp_path, g_rearm_min=0.0, g_pos=0, exit_only=True,
                        g_trig_ms=GNOW - 1000)
    _grid_sync(book, _gstore(84300.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.g_rearm_at_ms == 0 and so.exit_only is True, "0 = не перевзводить"


def test_rearm_does_not_happen_without_a_price(tmp_path):
    """Цены нет — базу взять неоткуда, вслепую сетку не ставим."""
    class Blind:
        def tick(self, code, agent=None):
            return {}

        def agent_status(self, agent=None):
            return {"_received_at_ms": GNOW, "health": {"ord_age_ms": 1200},
                    "quik": {"orders": []}}

    book, so = _guarded(tmp_path, g_rearm_min=1, g_pos=0, exit_only=True,
                        g_trig_ms=GNOW - 1000, g_rearm_at_ms=GNOW - 1)
    _grid_sync(book, Blind(), GOst(), GSrv(), GLim(), "9618", GSTEPS, {}, GNOW, True)
    assert so.g_rearms == 0 and so.exit_only is True


def test_a_manual_exit_only_is_not_rearmed(tmp_path):
    """Режим, включённый ОПЕРАТОРОМ (без срабатывания защиты, g_trig_ms=0), сам не
    снимается: он выключил сетку намеренно, и возвращать её за него нельзя."""
    book, so = _guarded(tmp_path, g_rearm_min=1, g_pos=0, exit_only=True,
                        g_trig_ms=0)
    _grid_sync(book, _gstore(84300.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.g_rearm_at_ms == 0 and so.exit_only is True


# --------------------------------------------------------------------------
# КАСАНИЯ ТРИГГЕРНОЙ ЦЕНЫ — отдельный параметр (оператор 02.10.2026, напоминание
# об исходной постановке). Одиночный выброс не должен снимать сетку.
# --------------------------------------------------------------------------


def test_a_touch_is_entering_the_zone_not_a_tick_inside_it(tmp_path):
    """Сторож ходит раз в секунду: без этого различия минута за порогом дала бы
    шестьдесят «касаний» и защита сработала бы при любом пороге касаний."""
    _, so = _guarded(tmp_path, g_trig_fills=0, g_trig_move_pct=0.25,
                     g_trig_touches=3, g_base=85000.0)
    for _ in range(10):                       # десять тиков ВНУТРИ зоны
        so_mod.grid_guard_hit(so, 84700.0)
    assert so.g_touch_down == 1, "это одно касание, а не десять"
    so_mod.grid_guard_hit(so, 85000.0)        # вернулись к базе
    assert so.g_zone_side == ""
    so_mod.grid_guard_hit(so, 84700.0)        # вошли снова
    assert so.g_touch_down == 2


def test_guard_waits_for_the_required_number_of_touches(tmp_path):
    _, so = _guarded(tmp_path, g_trig_fills=0, g_trig_move_pct=0.25,
                     g_trig_touches=3, g_base=85000.0)
    for i in (1, 2):
        assert so_mod.grid_guard_hit(so, 84700.0) == "", f"касание {i} — рано"
        so_mod.grid_guard_hit(so, 85000.0)
    why = so_mod.grid_guard_hit(so, 84700.0)
    assert why and "касаний с этой стороны 3 из 3" in why, why


def test_one_touch_keeps_the_previous_behaviour(tmp_path):
    _, so = _guarded(tmp_path, g_trig_fills=0, g_trig_move_pct=0.25,
                     g_trig_touches=1, g_base=85000.0)
    assert so_mod.grid_guard_hit(so, 84700.0), "порог 1 — срабатывает сразу"


def test_touches_are_counted_PER_SIDE_not_summed(tmp_path):
    """УТОЧНЕНИЕ ОПЕРАТОРА 02.10.2026: касания считаются С ОДНОЙ стороны.

    Снимать сетку должен ОДНОСТОРОННИЙ уход. Касание вверх и касание вниз — это
    колебание вокруг базы, то есть ровно то, ради чего сетка и стоит; суммировать
    их значило бы выключать сетку в её рабочем режиме."""
    _, so = _guarded(tmp_path, g_trig_fills=0, g_trig_move_pct=0.25,
                     g_trig_touches=2, g_base=85000.0)
    assert so_mod.grid_guard_hit(so, 84700.0) == ""       # касание вниз
    so_mod.grid_guard_hit(so, 85000.0)
    assert so_mod.grid_guard_hit(so, 85300.0) == "", (
        "касание вверх не складывается с касанием вниз")
    so_mod.grid_guard_hit(so, 85000.0)
    assert so_mod.grid_guard_hit(so, 85300.0), "ВТОРОЕ касание вверх — тренд, снимаем"
    assert so.g_touch_up == 2 and so.g_touch_down == 1


def test_rearm_resets_the_touch_counter(tmp_path):
    """Иначе перевзведённая сетка снялась бы первым же касанием новой базы."""
    book, so = _guarded(tmp_path, g_rearm_min=1, g_pos=0, exit_only=True,
                        g_trig_ms=GNOW - 1000, g_rearm_at_ms=GNOW - 1,
                        g_touch_up=5, g_zone_side="up")
    _grid_sync(book, _gstore(84300.0), GOst(), GSrv(), GLim(), "9618", GSTEPS, {},
               GNOW, True)
    assert so.g_touch_up == 0 and so.g_touch_down == 0 and so.g_zone_side == ""


# --------------------------------------------------------------------------
# УРОВНИ — В ОДНУ СТОРОНУ. Оператор 02.10.2026: «кол-во касаний в том числе и
# уровней!». На GZZ6 защита сработала на трёх филлах при позиции в ОДИН уровень:
# сетка отрабатывала боковик, а сочла его трендом и сняла все покупки.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n", [3, 4, 7, 20, 101])
def test_pure_alternation_never_fires_the_guard(n):
    """ЗАПРЕТ: сколько бы раз сетка ни гасила уровни попеременно вниз-вверх,
    защита по уровням не срабатывает — это её рабочий режим, а не тренд."""
    so = _guarded(pathlib.Path("."), g_trig_move_pct=0.0)[1]
    live: dict = {}
    for i in range(n):
        side = "buy" if i % 2 == 0 else "sell"
        _grid_count_fill(so, live, -1 if side == "buy" else 0, side, 1, GNOW + i,
                         price=85000.0)
        assert so_mod.grid_guard_hit(so, 85000.0) == "", (
            f"чередование на {i + 1}-м филле включило защиту (позиция {so.g_pos:+d})")


@pytest.mark.parametrize("seed", range(30))
def test_guard_fires_exactly_when_net_levels_reach_the_threshold(seed):
    """Тренд с откатами ловится: защита включается ровно тогда, когда набранная
    в одну сторону позиция дошла до порога, сколько бы откатов ни было между."""
    rnd = random.Random(seed)
    so = _guarded(pathlib.Path("."), g_trig_move_pct=0.0, g_lot=2)[1]
    live: dict = {}
    for i in range(60):
        side = "buy" if rnd.random() < 0.6 else "sell"
        _grid_count_fill(so, live, 0, side, 2, GNOW + i, price=85000.0)
        fired = so_mod.grid_guard_hit(so, 85000.0) != ""
        assert fired == (abs(so.g_pos) / 2 >= 3), (
            f"seed {seed}, филл {i + 1}: позиция {so.g_pos:+d}, защита {fired}")
