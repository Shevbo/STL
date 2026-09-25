"""Качество робота: круги, доля выигранных, recovery factor.

Метрики считаются ТОЙ ЖЕ методикой, что в бэктесте: сделка — это круг, а не филл."""

import datetime

from trader.quik import robot_stats as rs

T = 1_790_000_000_000


def _f(ts, pos_after, net=0.0, qty=1, side="buy", rid="r1", mode="real", sym="RIZ6"):
    return {"ts_ms": ts, "pos_after": pos_after, "pnl_net_rub": net, "qty": qty,
            "side": side, "robot_id": rid, "mode": mode, "symbol": sym}


def test_a_cycle_is_a_round_trip_not_a_fill():
    """Долив тремя филлами и выход одним — ОДНА сделка, а не четыре."""
    fills = [_f(T, 1), _f(T + 1000, 2), _f(T + 2000, 3), _f(T + 3000, 0, 900.0, qty=3)]
    cyc, tail = rs.cycles(fills)
    assert len(cyc) == 1 and tail == 0
    assert cyc[0]["fills"] == 4 and cyc[0]["net_rub"] == 900.0 and cyc[0]["lots"] == 3


def test_open_tail_is_reported_not_swallowed():
    cyc, tail = rs.cycles([_f(T, 1), _f(T + 1000, 0, 100.0), _f(T + 2000, 2)])
    assert len(cyc) == 1 and tail == 1


def test_win_rate_and_recovery_factor():
    # Круги: +300, -100, +200, -50 → net 350, просадка 100 (после +300 упали до 200).
    fills = []
    for i, net in enumerate((300.0, -100.0, 200.0, -50.0)):
        fills += [_f(T + i * 10_000, 1), _f(T + i * 10_000 + 1000, 0, net)]
    s = rs.summarize(fills)
    assert s["trades"] == 4 and s["wins"] == 2 and s["win_rate"] == 0.5
    assert s["net_rub"] == 350.0 and s["max_drawdown_rub"] == 100.0
    assert s["recovery_factor"] == 3.5
    assert s["profit_factor"] == round(500 / 150, 3)


def test_no_drawdown_means_undefined_rf_not_infinity():
    fills = [_f(T, 1), _f(T + 1000, 0, 100.0), _f(T + 2000, 1), _f(T + 3000, 0, 50.0)]
    s = rs.summarize(fills)
    assert s["recovery_factor"] is None and s["win_rate"] == 1.0


def test_no_trades_means_unknown_win_rate_not_zero():
    s = rs.summarize([])
    assert s["trades"] == 0 and s["win_rate"] is None and s["net_rub"] == 0.0


def test_equity_series_is_cumulative_by_msk_day():
    fills = [_f(T, 1), _f(T + 1000, 0, 100.0),
             _f(T + 86_400_000, 1), _f(T + 86_401_000, 0, -30.0)]
    s = rs.summarize(fills)
    assert [d["equity_rub"] for d in s["series"]] == [100.0, 70.0]
    assert s["by_day"] == s["series"]        # прежнее имя не сломано


def test_weeks_and_months_are_calendar_buckets():
    """Неделя на графике — календарная ISO, а не «последние семь дней»."""
    day = 86_400_000
    fills = []
    for i, net in enumerate((10.0, 20.0, 40.0)):     # 3 круга подряд, разные дни
        fills += [_f(T + i * day, 1), _f(T + i * day + 500, 0, net)]
    week = rs.summarize(fills, bucket="week")["series"]
    month = rs.summarize(fills, bucket="month")["series"]
    assert all(p["key"].count("-W") == 1 for p in week)
    assert sum(p["net_rub"] for p in week) == 70.0
    assert len(month) == 1 and month[0]["net_rub"] == 70.0 and month[0]["trades"] == 3


def test_a_cycle_lands_in_the_bucket_of_its_EXIT():
    """Круг, открытый в одном дне и закрытый в другом, принадлежит дню выхода."""
    fills = [_f(T, 1), _f(T + 86_400_000, 0, 500.0)]
    s = rs.summarize(fills)
    assert len(s["series"]) == 1
    exit_day = rs.bucket_key(T + 86_400_000, "day")
    assert s["series"][0]["key"] == exit_day


def test_robots_are_ranked_by_quality_not_by_money():
    """Мелкий робот с высоким RF обязан стоять выше крупного с низким —
    ради этого экран и заводится."""
    small, big = [], []
    for i, net in enumerate((100.0, 100.0, -20.0)):
        small += [_f(T + i * 10_000, 1, rid="small"),
                  _f(T + i * 10_000 + 500, 0, net, rid="small")]
    for i, net in enumerate((5000.0, -4000.0, 4000.0)):
        big += [_f(T + i * 10_000, 1, rid="big"),
                _f(T + i * 10_000 + 500, 0, net, rid="big")]
    out = rs.by_robot(small + big)
    assert [r["robot_id"] for r in out] == ["small", "big"]
    assert out[1]["net_rub"] > out[0]["net_rub"]      # денег у big больше


def test_period_bounds_are_msk_sliding_windows():
    now = datetime.datetime(2026, 9, 25, 12, 0, tzinfo=rs.MSK)
    lo, hi = rs.period_bounds("week", now)
    assert datetime.datetime.fromtimestamp(lo / 1000, rs.MSK).date().isoformat() == "2026-09-19"
    assert hi == int(now.timestamp() * 1000)
    assert rs.period_bounds("all", now)[0] == 0
