"""Уровень не повторяет ту же сторону без встречной сделки в сетке.

Оператор 04.10.2026: «если продажа была 9969, то ставить следующую опять продажу по
этой цене значит баг в логике, который может породить зацикливание сделок в одну
сторону — опасность набрать бешеную позицию». В 10:54:13 по GZ уровни 0 (9969) и +1
(9979) продали в одну секунду, уровень 0 проснулся от соседа, а сторож, выбирающий
сторону по цене на момент постановки и отстающий на секунду-две, поставил на него
продажу снова: цена уже вернулась ниже 9969.
"""
import random

import pytest

from trader.api.quik_smart_orders import _grid_sync
from trader.quik import smart_orders as so_mod
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

from .test_smart_grid import GLim, GNOW, GOst, GSrv, GSTEPS, _gstore, _gterm_row


@pytest.mark.parametrize("seed", range(60))
def test_gate_matches_an_independent_history_model(seed):
    """Независимая модель: берём ИСТОРИЮ исполнений списком и спрашиваем, была ли после
    последнего исполнения уровня хоть одна заявка противоположной стороны."""
    rnd = random.Random(seed)
    live: dict = {}
    hist: list[tuple[int, str]] = []
    for _ in range(rnd.randint(1, 80)):
        lvl, side = rnd.randint(-4, 4), rnd.choice(("buy", "sell"))
        so_mod.grid_note_fill(live, lvl, side)
        hist.append((lvl, side))
        for probe_lvl in range(-4, 5):
            for probe_side in ("buy", "sell"):
                mine = [i for i, (lv_, _s) in enumerate(hist) if lv_ == probe_lvl]
                if not mine or hist[mine[-1]][1] != probe_side:
                    want = False
                else:
                    opp = "buy" if probe_side == "sell" else "sell"
                    want = not any(s == opp for _l, s in hist[mine[-1] + 1:])
                got = so_mod.grid_repeat_blocked(live, probe_lvl, probe_side)
                assert got == want, (f"seed {seed}, уровень {probe_lvl} {probe_side}: "
                                     f"запрет {got}, модель {want}, история {hist[-6:]}")


def test_values_in_live_are_never_strings():
    """Строку в live _cancel_resting приняла бы за идентификатор заявки."""
    live: dict = {}
    for lvl, side in ((0, "sell"), (1, "sell"), (-1, "buy")):
        so_mod.grid_note_fill(live, lvl, side)
    assert all(not isinstance(v, str) for v in live.values()), live


class _Ost(GOst):
    def __init__(self):
        self.recs = []

    def working_orders(self, agent=None):
        return list(self.recs)


def _book(tmp_path):
    b = SmartOrderBook(str(tmp_path / "r.json"))
    so = SmartOrder(so_id=new_id(), kind="grid", code="RIZ6", side="sell", qty=1,
                    g_step=100.0, g_buys=4, g_sells=4, g_lot=7, g_base=85000.0,
                    g_stop_pts=0.0, created_ms=GNOW, status="armed", g_cash_on=True,
                    g_window=0)
    b.orders.append(so)
    return b, so


def _placed(srv):
    return [m.place_order for m in srv.sent if m.WhichOneof("payload") == "place_order"]


def _filled(cid, num, side, price):
    return {"client_id": cid, "order_id": num, "state": "filled", "remaining": 0,
            "filled": 7, "side": side, "price": price}


@pytest.mark.parametrize("passes", [1, 3, 8])
def test_the_incident_two_levels_sell_in_one_second_then_no_second_sell(tmp_path, passes):
    """Уровни 0 (85000) и +1 (85100) продали одновременно, рынок вернулся НИЖЕ уровня 0.
    Уровень 0 проснулся от соседа, но продажей по 85000 встать не имеет права, пока в
    сетке не исполнится покупка."""
    book, so = _book(tmp_path)
    so.g_live = {"0": "cidA", "1": "cidB"}
    ost = _Ost()
    ost.recs = [_filled("cidA", "N1", "sell", 85000.0), _filled("cidB", "N2", "sell", 85100.0)]
    t = GNOW
    for i in range(passes + 1):
        srv = GSrv()
        _grid_sync(book, _gstore(84900.0), ost, srv, GLim(), "9618", GSTEPS, {}, t, True)
        t += 1000
        again = [p for p in _placed(srv) if abs(p.price - 85000.0) < 1e-6 and p.side == 2]
        assert again == [], f"проход {i}: уровень 0 снова встал продажей по 85000"

    # исполнилась ПОКУПКА на уровне -1: теперь продажа на уровне 0 снова законна
    ost.recs.append(_filled("cidC", "N3", "buy", 84900.0))
    live = dict(so.g_live)
    live["-1"] = "cidC"
    so.g_live = live
    srv = GSrv()
    for j in range(3):
        _grid_sync(book, _gstore(84900.0), ost, srv, GLim(), "9618", GSTEPS, {}, t, True)
        t += 1000
    assert any(abs(p.price - 85000.0) < 1e-6 and p.side == 2 for p in _placed(srv)), (
        "после встречной сделки продажа на уровне 0 обязана вернуться")


def test_a_standing_order_that_breaks_the_rule_is_withdrawn(tmp_path):
    book, so = _book(tmp_path)
    tag = f"stl-so-{so.so_id}:gp"
    # уровень 0 последний раз продал, встречной сделки не было; продажа 85000 стоит в стакане
    live: dict = {}
    so_mod.grid_note_fill(live, 0, "sell")
    live["flip:1"] = True
    so.g_live = live
    ok = _gterm_row("A", "sell", 85200.0, qty=7, tag=tag)          # уровень +2: законна
    bad = _gterm_row("B", "sell", 85000.0, qty=7, tag=tag)         # уровень 0: нарушает
    srv = GSrv()
    _grid_sync(book, _gstore(84900.0, [ok, bad]), _Ost(), srv, GLim(), "9618", GSTEPS, {},
               GNOW, True)
    killed = {m.cancel_order.order_id for m in srv.sent
              if m.WhichOneof("payload") == "cancel_order"}
    assert "B" in killed, "заявка, нарушающая запрет, осталась в стакане"
    assert "A" not in killed, "законная заявка снята"
