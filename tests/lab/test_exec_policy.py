"""Политики доведения заявки (exec_policy) на синтетике — реальных данных
здесь нет, они считаются только на i9 (STRICT, docs/execution-cost-program.md)."""
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
