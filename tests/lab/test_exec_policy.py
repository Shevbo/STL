"""Политики доведения заявки (exec_policy) на синтетике — реальных данных
здесь нет, они считаются только на i9 (STRICT, docs/execution-cost-program.md)."""
import statistics
from datetime import datetime, timezone

from trader.lab import exec_policy

D0 = int(datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())  # 10:00 МСК-стенка


def _ladder(top: float, step: float, qty: float, sign: int, n: int = 5) -> list[tuple]:
    """n уровней от top, шаг step в сторону sign (bids: sign=-1, asks: sign=+1)."""
    return [(top + sign * step * k, qty) for k in range(n)]


def _book_rows_cycle(n_days: int = 2, hours: int = 2) -> list[tuple]:
    """mid стоит на месте (спред 10), объём L1 на обеих сторонах раз в 10 с на
    3 с падает до нуля и восстанавливается (съедание уровня чужими сделками)."""
    rows = []
    mid, half_spread, step = 100000.0, 5.0, 1.0
    for day in range(n_days):
        day_start = D0 + day * 86400
        for t in range(hours * 3600):
            q1 = 0 if (t % 10) < 3 else 5
            bid1, ask1 = mid - half_spread, mid + half_spread
            bids = [(bid1, q1)] + _ladder(bid1 - step, step, 5, -1, 4)
            asks = [(ask1, q1)] + _ladder(ask1 + step, step, 5, 1, 4)
            rows.append(((day_start + t) * 1000, bids, asks))
    return rows


def _book_rows_drift(hours: int = 1, drift_per_sec: float = 1.0) -> list[tuple]:
    """mid дрейфует против покупателя на drift_per_sec пунктов в секунду,
    спред держится 10, объёмы полные (глубина не съедается, только цена уходит)."""
    rows = []
    half_spread, step = 5.0, 1.0
    for t in range(hours * 3600):
        mid = 100000.0 - drift_per_sec * t
        bid1, ask1 = mid - half_spread, mid + half_spread
        bids = _ladder(bid1, step, 5, -1)
        asks = _ladder(ask1, step, 5, 1)
        rows.append(((D0 + t) * 1000, bids, asks))
    return rows


def _book_rows_flat(minutes: int = 20) -> list[tuple]:
    """mid и глубина неподвижны: спред 10, 5 лотов на каждом из 5 уровней."""
    rows = []
    mid, half_spread, step = 100000.0, 5.0, 1.0
    for t in range(minutes * 60):
        bids = _ladder(mid - half_spread, step, 5, -1)
        asks = _ladder(mid + half_spread, step, 5, 1)
        rows.append(((D0 + t) * 1000, bids, asks))
    return rows


def _book_rows_slice(recover: bool, gap_s: int = 5) -> list[tuple]:
    """mid неподвижен (спред 10); L1=3, L2=3 лота. В момент gap_s после старта
    L1 восстановлен (recover=True) или съеден чужими сделками до нуля
    (recover=False), L2 не меняется — проверка slice(2, gap_s)."""
    mid, half_spread, step = 100000.0, 5.0, 1.0
    bid1, ask1 = mid - half_spread, mid + half_spread
    rows = []
    for t in range(gap_s + 2):
        bids = _ladder(bid1, step, 5, -1)
        l1_qty = 3 if recover or t != gap_s else 0
        asks = [(ask1, l1_qty), (ask1 + step, 3)] + _ladder(ask1 + step * 2, step, 3, 1)
        rows.append(((D0 + t) * 1000, bids, asks))
    return rows


def test_hold_optimistic_fills_on_eaten_level_pessimistic_falls_to_market():
    rows = _book_rows_cycle()
    res = exec_policy.analyze(rows, sizes=(1,), hold_s_list=(30,), chase_s_list=(),
                              n_samples=400, seed=0)
    hold_rows = {(r["side"], r["minute_class"], r["weekend"]): r
                 for r in res["rows"] if r["policy"] == "hold" and r["hold_s"] == 30}
    market_rows = {(r["side"], r["minute_class"], r["weekend"]): r
                   for r in res["rows"] if r["policy"] == "market"}
    assert hold_rows
    for key, r in hold_rows.items():
        # цикл съедания повторяется каждые 10 с — в любом 30-секундном окне
        # съедание случается почти всегда, спред неподвижен весь тест.
        assert r["fill_share_opt"] > 0.9, r
        assert abs(r["cost_med_opt"] - (-5.0)) < 1e-6, r
        # mid/спред никогда не движутся -> "насквозь" не бывает.
        assert r["fill_share_pess"] == 0.0, r
        # 30 с кратно периоду цикла (10 с) -> фаза съедания на добора в t+30
        # совпадает с фазой на исходном t: издержка добора = издержка market
        # ТОЧНО на каждом отсчёте, не только в среднем.
        m = market_rows[key]
        assert abs(r["cost_med_pess"] - m["cost_med_pess"]) < 1e-6, (r, m)
        assert abs(r["vs_market_med_pess"]) < 1e-6, r


def test_hold_pessimistic_fills_on_drift_with_positive_adverse():
    rows = _book_rows_drift(hours=1, drift_per_sec=1.0)
    res = exec_policy.analyze(rows, sizes=(1,), hold_s_list=(60,), chase_s_list=(),
                              n_samples=300, seed=1)
    hold_rows = [r for r in res["rows"] if r["policy"] == "hold" and r["hold_s"] == 60
                and r["side"] == "buy"]
    assert hold_rows
    for r in hold_rows:
        # цена уходит насквозь (>10 пт дрейфа) всего за ~11 с, глубоко внутри 60 с.
        assert r["fill_share_pess"] > 0.9, r
        assert r["n"] > 0
        assert r["adverse_60s_med"] is not None
        # наливка случилась, когда дрейф уже "съел" наш полспред — рынок
        # продолжает идти против нас все следующие 60 с.
        assert r["adverse_60s_med"] > 10.0, r


def test_market_cost_scales_with_size():
    rows = _book_rows_flat()
    res = exec_policy.analyze(rows, sizes=(1, 10), hold_s_list=(), chase_s_list=(),
                              n_samples=100, seed=2)
    market_rows = [r for r in res["rows"] if r["policy"] == "market"]
    assert market_rows
    for r in market_rows:
        if r["size"] == 1:
            assert abs(r["cost_med_opt"] - 5.0) < 1e-6, r
        elif r["size"] == 10:
            # 5 лотов по L1 (5 пт от mid) + 5 по L2 (6 пт) -> VWAP на полпункта хуже.
            assert abs(r["cost_med_opt"] - 5.5) < 1e-6, r


def test_hold_zero_equals_immediate_market():
    """hold(0)+chase(0) деградирует в «рынок сейчас же» — граничный случай
    политик должен сходиться с отдельной строкой market один в один."""
    rows = _book_rows_flat()
    res = exec_policy.analyze(rows, sizes=(1,), hold_s_list=(0,), chase_s_list=(),
                              n_samples=100, seed=3)
    market = {(r["side"], r["minute_class"], r["weekend"]): r
             for r in res["rows"] if r["policy"] == "market"}
    hold0 = [r for r in res["rows"] if r["policy"] == "hold" and r["hold_s"] == 0]
    assert hold0
    for r in hold0:
        m = market[(r["side"], r["minute_class"], r["weekend"])]
        assert abs(r["cost_med_opt"] - m["cost_med_opt"]) < 1e-9, (r, m)
        assert r["fill_share_opt"] == 0.0  # T=0 никогда не "наливается" пассивно


# --------------------------------------------------------------------------
# delay(T) и slice(k, s): без пассивной фазы, один рыночный добор/доли.
# --------------------------------------------------------------------------

def test_delay_on_stationary_mid_equals_market_and_vs_market_mean_zero():
    """(а) delay(T) на стоящем mid = market. (в) vs_market_mean = 0 у market
    и у delay на стоящем mid (mid не двигается -> парная разность всегда 0)."""
    rows = _book_rows_flat()
    res = exec_policy.analyze(rows, sizes=(1,), hold_s_list=(), chase_s_list=(),
                              delay_s_list=(30,), slices_list=(), n_samples=100, seed=5)
    market = {(r["side"], r["minute_class"], r["weekend"]): r
             for r in res["rows"] if r["policy"] == "market"}
    delay_rows = [r for r in res["rows"] if r["policy"] == "delay" and r["delay_s"] == 30]
    assert delay_rows
    for r in delay_rows:
        m = market[(r["side"], r["minute_class"], r["weekend"])]
        assert abs(r["cost_med_opt"] - m["cost_med_opt"]) < 1e-9, (r, m)
        assert r["cost_med_opt"] == r["cost_med_pess"] == r["cost_mean_opt"] == r["cost_mean_pess"]
        assert r["fill_share_opt"] == r["fill_share_pess"] == 1.0
        assert r["adverse_60s_med"] is None
        assert abs(r["vs_market_mean"]) < 1e-9, r
        assert abs(m["vs_market_mean"]) < 1e-9, m


def test_delay_drift_costs_exactly_one_tick_more_than_market():
    """(а) дрейф против нас 1 тик (шаг 1.0) за T=10 с (drift_per_sec=0.1) ->
    delay(10) на продаже дороже market ровно на тик."""
    rows = _book_rows_drift(hours=1, drift_per_sec=0.1)
    res = exec_policy.analyze(rows, sizes=(1,), hold_s_list=(), chase_s_list=(),
                              delay_s_list=(10,), slices_list=(), n_samples=200, seed=6)
    market = {(r["side"], r["minute_class"], r["weekend"]): r
             for r in res["rows"] if r["policy"] == "market" and r["side"] == "sell"}
    delay_rows = [r for r in res["rows"]
                 if r["policy"] == "delay" and r["delay_s"] == 10 and r["side"] == "sell"]
    assert delay_rows
    for r in delay_rows:
        m = market[(r["side"], r["minute_class"], r["weekend"])]
        assert abs((r["cost_med_opt"] - m["cost_med_opt"]) - 1.0) < 1e-6, (r, m)
        assert abs(r["vs_market_mean"] - 1.0) < 1e-6, r


def test_slice_no_recovery_matches_market_same_walk_size_below_k_equals_market():
    """(б) slice(2, 5) без восстановления L1 = market (тот же проход книги);
    N=1 < k=2 -> без нарезки, тоже = market."""
    rows = _book_rows_slice(recover=False)
    anchors = [(rows[0][0], "buy", 6, "r", "filled", None),
              (rows[0][0], "buy", 1, "r", "filled", None)]
    res = exec_policy.analyze(rows, anchors=anchors, hold_s_list=(), chase_s_list=(),
                              delay_s_list=(), slices_list=(2,), slice_every_s_list=(5,))
    by_size_class = {(r["policy"], r["size_class"]): r for r in res["rows"]}
    m6, s6 = by_size_class[("market", "6-10")], by_size_class[("slice", "6-10")]
    assert abs(m6["cost_med_opt"] - 5.5) < 1e-9, m6
    assert abs(s6["cost_med_opt"] - m6["cost_med_opt"]) < 1e-9, (s6, m6)
    assert abs(s6["vs_market_mean"]) < 1e-9, s6
    m1, s1 = by_size_class[("market", "1")], by_size_class[("slice", "1")]
    assert abs(s1["cost_med_opt"] - m1["cost_med_opt"]) < 1e-9, (s1, m1)


def test_slice_with_recovery_cheaper_than_market():
    """(б) slice(2, 5) с восстановлением L1 между долями дешевле market."""
    rows = _book_rows_slice(recover=True)
    anchors = [(rows[0][0], "buy", 6, "r", "filled", None)]
    res = exec_policy.analyze(rows, anchors=anchors, hold_s_list=(), chase_s_list=(),
                              delay_s_list=(), slices_list=(2,), slice_every_s_list=(5,))
    market = next(r for r in res["rows"] if r["policy"] == "market")
    sliced = next(r for r in res["rows"] if r["policy"] == "slice")
    assert abs(market["cost_med_opt"] - 5.5) < 1e-9, market
    assert abs(sliced["cost_med_opt"] - 5.0) < 1e-9, sliced
    assert sliced["cost_med_opt"] < market["cost_med_opt"]
    assert sliced["vs_market_mean"] < 0


# --------------------------------------------------------------------------
# anchors_key: точки отсчёта из реальных заявок (scripts/exec_anchors.py),
# а не случайная выборка.
# --------------------------------------------------------------------------

def test_anchor_snapshot_not_later_than_ts():
    """(а) индекс снимка для якоря — последний НЕ ПОЗЖЕ ts, не дальше MAX_GAP_S."""
    rows = _book_rows_flat(minutes=2)
    days_data = exec_policy._prep_days(rows)
    dd = next(iter(days_data.values()))
    # между снимками t=7 и t=8 (полсекунды после t=7) -> берём t=7, не t=8.
    assert exec_policy._nearest_anchor_snapshot(dd, dd["ts"][7] + 500) == 7
    # ровно на снимке -> сам снимок.
    assert exec_policy._nearest_anchor_snapshot(dd, dd["ts"][7]) == 7
    # раньше первого снимка дня -> снимка нет.
    assert exec_policy._nearest_anchor_snapshot(dd, dd["ts"][0] - 1) is None
    # дальше MAX_GAP_S после последнего снимка -> дырка, якорь дропается.
    assert exec_policy._nearest_anchor_snapshot(dd, dd["ts"][-1] + 61_000) is None


def test_anchors_strata_by_robot_and_actual_matches_fed_cost():
    """(б) строки несут robot и стратифицированы им (и size_class); (в)
    policy="actual" сходится с медианой ПЕРЕДАННЫХ fact_vs_mid — обходит
    симуляцию целиком, издержка берётся из якоря напрямую."""
    rows = _book_rows_flat(minutes=2)
    facts = {
        ("robotA", "buy"): [1.0, 2.0, 3.0],
        ("robotA", "sell"): [4.0, 6.0],
        ("robotB", "buy"): [10.0, 20.0],
        ("robotB", "sell"): [7.0, 8.0, 9.0],
    }
    ts_s = {
        ("robotA", "buy"): [5, 15, 25], ("robotA", "sell"): [35, 45],
        ("robotB", "buy"): [8, 18], ("robotB", "sell"): [28, 38, 48],
    }
    qtys = {
        ("robotA", "buy"): [1, 2, 1], ("robotA", "sell"): [1, 3],
        ("robotB", "buy"): [2, 1], ("robotB", "sell"): [1, 1, 2],
    }
    anchors = [((D0 + t) * 1000, side, qty, robot, "filled", fv)
              for (robot, side), fvals in facts.items()
              for t, qty, fv in zip(ts_s[(robot, side)], qtys[(robot, side)], fvals)]
    assert len(anchors) == 10  # 10 якорей с известными side/qty, как в задании

    res = exec_policy.analyze(rows, anchors=anchors, sizes=(1,), hold_s_list=(),
                              chase_s_list=(), n_samples=10, seed=0)
    assert res["n_used"] == 10
    assert res["dropped_anchors"] == 0

    grouped: dict = {}
    for (robot, side), fvals in facts.items():
        for qty, fv in zip(qtys[(robot, side)], fvals):
            grouped.setdefault((robot, side, exec_policy._size_class(qty)), []).append(fv)

    actual_rows = {(r["robot"], r["side"], r["size_class"]): r
                  for r in res["rows"] if r["policy"] == "actual"}
    assert set(actual_rows) == set(grouped)
    for key, fvals in grouped.items():
        r = actual_rows[key]
        assert r["n"] == len(fvals)
        assert abs(r["cost_med_pess"] - statistics.median(fvals)) < 1e-9, (key, r, fvals)

    # (б) market (и любая другая политика) тоже несёт robot, не константу "random".
    market_robots = {r["robot"] for r in res["rows"] if r["policy"] == "market"}
    assert market_robots == {"robotA", "robotB"}


def test_anchors_random_path_robot_is_constant():
    """Старое поведение (без anchors) — robot="random" у всех строк, сетка
    sizes*sides не меняется этим полем."""
    rows = _book_rows_flat()
    res = exec_policy.analyze(rows, sizes=(1,), hold_s_list=(), chase_s_list=(),
                              n_samples=50, seed=4)
    assert res["rows"]
    assert {r["robot"] for r in res["rows"]} == {"random"}
