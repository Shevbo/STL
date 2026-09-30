"""C6 (охота за стопами) на синтетике — реальных данных здесь нет и не будет."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import c6_stop_hunt as c6

D0 = int(datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp())   # 10:00 МСК-стенка, в сессии
SESSION_MIN = 300


def _walk_bars(n_days=15, seed=1, std=0.5):
    """Случайное блуждание без фитилей (h=max(o,c), l=min(o,c)), объём=10."""
    rng = random.Random(seed)
    rows = []
    for d in range(n_days):
        px = 100000.0
        for m in range(SESSION_MIN):
            o = px
            px += rng.gauss(0, std)
            c = px
            rows.append([D0 + d * 86400 + m * 60, o, max(o, c), min(o, c), c, 10.0])
    return rows


def _engineered_sweep_bars(n_days=40, spike_min=100, n=30, drift_total=-3.0, drift_bars=15):
    """Дни плоские, кроме одного свипа вверх на spike_min: фитиль пробивает
    локальный максимум N баров, закрытие возвращается внутрь диапазона,
    дальше детерминированный дрейф -3 pt на drift_bars баров (h=15 совпадает
    с концом дрейфа, эффект точно измерим)."""
    rows = []
    for d in range(n_days):
        px = 100000.0
        day = [[D0 + d * 86400 + m * 60, px, px, px, px, 10.0] for m in range(SESSION_MIN)]
        i = spike_min
        local_max = max(r[2] for r in day[i - n:i])
        return_close = local_max - 1.0
        day[i][2] = local_max + 5.0          # фитиль пробивает диапазон
        day[i][1] = day[i][3] = day[i][4] = return_close  # закрытие вернулось внутрь
        step = drift_total / drift_bars
        prev = return_close
        for k in range(1, drift_bars + 1):
            j = i + k
            new_close = prev + step
            day[j][1], day[j][4] = prev, new_close
            day[j][2], day[j][3] = max(prev, new_close), min(prev, new_close)
            prev = new_close
        rows.extend(day)
    return rows


def _row(rows_list, n, delta, window, h):
    return next(r for r in rows_list
                if r["N"] == n and r["delta"] == delta and r["window"] == window and r["h"] == h)


def test_random_walk_no_effect():
    res = c6.analyze(_walk_bars(n_days=20, seed=1), lookbacks=(30, 60), deltas=(0.0, 0.5),
                      horizons=(15, 30), draws=100, seed=0)
    for n in (30, 60):
        for window in (0, 3):
            row = _row(res["rows"], n, 0.0, window, 15)
            p = row["null"]["p"]
            assert p is None or p > 0.05, (n, window, row)


def test_engineered_sweep_significant():
    res = c6.analyze(_engineered_sweep_bars(), lookbacks=(30,), deltas=(0.0,),
                      horizons=(15,), draws=200, seed=0)
    row = _row(res["rows"], 30, 0.0, 0, 15)
    assert row["n"] == 40
    assert abs(row["median"] - 3.0) < 1e-6
    assert row["null"]["p"] < 0.01
    assert row["null"]["ci95"][0] > 0


def test_sweep_and_breakout_disjoint():
    rng = random.Random(3)
    px, day = 100000.0, []
    for m in range(200):
        o = px
        px += rng.gauss(0, 1.5)
        c = px
        day.append([D0 + m * 60, o, max(o, c), min(o, c), c, 10.0])
    cls = c6.classify_bars(day, 30, 0.0)
    up_sweep = {i for i, _ in cls["sweep_up"]}
    up_breakout = {i for i, _ in cls["breakout_up"]}
    dn_sweep = {i for i, _ in cls["sweep_dn"]}
    dn_breakout = {i for i, _ in cls["breakout_dn"]}
    assert not (up_sweep & up_breakout)
    assert not (dn_sweep & dn_breakout)
    assert up_sweep or up_breakout
