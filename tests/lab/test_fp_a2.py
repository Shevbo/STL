"""A2 (пробой канала) на синтетике: без эффекта, с внедрённым дрейфом,
без пересечения границы дня. Реальных данных здесь нет (i9 их не считает)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import a2_channel_breakout as a2
from trader.lab.footprints import common

D0 = int(datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp())   # 10:00 МСК-стенка, в сессии


def _bars_walk(n_days=15, seed=1, minutes=200):
    """Чистое случайное блуждание без внедрённого эффекта."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    for d in range(n_days):
        for m in range(minutes):
            px += rng.uniform(-2, 2)
            o = px - rng.uniform(-0.3, 0.3)
            v = rng.lognormvariate(3, 0.5)
            rows.append([D0 + d * 86400 + m * 60, o, max(o, px) + 1, min(o, px) - 1, px, v])
    return rows


def _bars_drift(n_days=10, seed=2, minutes=120, lookback=20, horizon=15,
                 drift=3.0, jump=12.0, period=40):
    """Узкий шумовой канал вокруг baseline; каждые `period` минут — гарантированный
    пробой (скачок `jump` от baseline) с продолжением +drift/бар на `horizon` баров."""
    rng = random.Random(seed)
    rows, baseline = [], 100000.0
    for d in range(n_days):
        drift_left = 0
        for m in range(minutes):
            if m % period == lookback:
                px = baseline + jump
                drift_left = horizon
            elif drift_left > 0:
                px = rows[-1][4] + drift
                drift_left -= 1
                if drift_left == 0:
                    baseline = px
            else:
                px = baseline + rng.uniform(-1.0, 1.0)
            o = px - rng.uniform(-0.2, 0.2)
            v = rng.lognormvariate(3, 0.5)
            rows.append([D0 + d * 86400 + m * 60, o, max(o, px) + 1, min(o, px) - 1, px, v])
    return rows


def _row(res, n, h):
    return next(r for r in res["rows"] if r["N"] == n and r["h"] == h)


def test_random_walk_no_effect():
    rows = _bars_walk()
    res = a2.analyze(rows, lookbacks=(10, 30), horizons=(5, 20), draws=200, seed=0)
    for n in (10, 30):
        for h in (5, 20):
            p = _row(res, n, h)["null"]["p"]
            assert p is None or p > 0.05, (n, h, p)


def test_breakout_drift_effect():
    rows = _bars_drift()
    res = a2.analyze(rows, lookbacks=(20,), horizons=(15,), draws=200, seed=0)
    row = _row(res, 20, 15)
    assert row["n"] >= 10, row["n"]
    assert row["null"]["p"] < 0.01, row["null"]
    assert row["median"] > 20, row["median"]  # заметно больше нулевого/шумового хода


def test_events_do_not_cross_day_boundary():
    # день 1: узкий шумовой канал 300 баров; день 2 НАЧИНАЕТСЯ скачком, который
    # выглядел бы пробоем истории дня 1, но своей истории (>=N баров) у него нет.
    rng = random.Random(7)
    rows, px = [], 100000.0
    for m in range(300):
        px += rng.uniform(-1, 1)
        rows.append([D0 + m * 60, px, px + 1, px - 1, px, 10])
    px += 50  # день 2, бар 0: скачок далеко за пределы канала дня 1
    rows.append([D0 + 86400, px, px + 1, px - 1, px, 10])
    for m in range(1, 25):
        px += rng.uniform(-1, 1)
        rows.append([D0 + 86400 + m * 60, px, px + 1, px - 1, px, 10])

    days = common.by_day(rows)
    d2 = sorted(days)[1]
    events = a2._breakout_events(days[d2], 20)
    assert all(i >= 20 for i, _ in events), events
    assert 0 not in {i for i, _ in events}

    res = a2.analyze(rows, lookbacks=(20,), horizons=(5,), draws=20, seed=0)
    assert all("отброшено" in n or "откат после" in n for n in res["notes"]), res["notes"]
