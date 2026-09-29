"""C3 (спуфинг/layering) на синтетике: без стен и p>0.05, с 30 вставленными
стенами share_pulled>0.8 и p<0.01 на move_during/move_after, приоритет
touched над pulled. Реальных данных здесь нет (i9 их не считает)."""
from __future__ import annotations

import random
from datetime import datetime, timezone

from trader.lab.footprints import c3_spoofing as c3

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
STEP = 20.0
BASELINE_BID1 = 100000.0
SPREAD = 20.0


def _snap(ts_ms, bid1, ask1, rng):
    """Обычный снимок: 5 уровней шагом STEP от best, объёмы 1..20."""
    bids = [(bid1 - STEP * i, rng.randint(1, 20)) for i in range(5)]
    asks = [(ask1 + STEP * i, rng.randint(1, 20)) for i in range(5)]
    return (ts_ms, bids, asks)


def _snap_wall(ts_ms, bid1, ask1, p_price, p_qty, rng):
    """Снимок с ПРИКОЛОЧЕННЫМ уровнем 3 бида: цена p_price фиксирована
    независимо от bid1 (стена не дрейфует вместе с книгой), остальные
    уровни — обычная формула от текущего best."""
    bids = [(bid1, rng.randint(1, 20)), (bid1 - STEP, rng.randint(1, 20)),
            (p_price, p_qty),
            (bid1 - 3 * STEP, rng.randint(1, 20)), (bid1 - 4 * STEP, rng.randint(1, 20))]
    asks = [(ask1 + STEP * i, rng.randint(1, 20)) for i in range(5)]
    return (ts_ms, bids, asks)


def _gen_random(seed=1, n_days=2, hours=2):
    """Без стен: обычные снимки, случайный mid, объёмы 1..20 (< любого K*q_med)."""
    rng = random.Random(seed)
    snaps = []
    mid = BASELINE_BID1
    for d in range(n_days):
        t0 = (D0 + d * 86400) * 1000
        for s in range(hours * 3600):
            mid += rng.uniform(-0.3, 0.3)
            snaps.append(_snap(t0 + s * 1000, mid, mid + SPREAD, rng))
    return snaps


def _gen_walls(seed=2, n_days=2, hours=2, n_walls_per_day=15,
               wall_dur=15, push=2.0, revert_dur=10):
    """30 стен на уровне 3 бида (200 лотов, p_price=BASELINE-2*STEP=99960
    фиксирован), 15 с жизни. Во время жизни best (mid) искусственно растёт
    на push пт линейной рампой, после снятия падает обратно теми же
    revert_dur секундами. step=20 против push=2 держит сортировку уровней
    целой (p_price всегда строго между bid1-STEP и bid1-3*STEP)."""
    rng = random.Random(seed)
    p_price = BASELINE_BID1 - 2 * STEP
    total_s = hours * 3600
    gap = total_s // (n_walls_per_day + 1)
    wall_starts = {gap * (w + 1) for w in range(n_walls_per_day)}
    snaps = []
    for d in range(n_days):
        t0 = (D0 + d * 86400) * 1000
        active = None
        for s in range(total_s):
            if s in wall_starts:
                active = s
            bid1, p_qty = BASELINE_BID1 + rng.uniform(-0.3, 0.3), rng.randint(1, 20)
            if active is not None and s < active + wall_dur:
                local = s - active
                bid1, p_qty = BASELINE_BID1 + push * (local + 1) / wall_dur, 200
            elif active is not None and s < active + wall_dur + revert_dur:
                local = s - (active + wall_dur)
                bid1 = BASELINE_BID1 + push - push * (local + 1) / revert_dur
            elif active is not None:
                active = None
            snaps.append(_snap_wall(t0 + s * 1000, bid1, bid1 + SPREAD, p_price, p_qty, rng))
    return snaps


def _find(rows, k, t, group):
    return next(r for r in rows if r["k"] == k and r["t"] == t and r["level_group"] == group)


def test_random_no_effect():
    snaps = _gen_random()
    res = c3.analyze(snaps, k_factors=(5, 10), lifetimes_s=(10, 30, 120), draws=100, seed=0)
    for row in res["rows"]:
        assert row["n_appear"] == 0, row          # объём 1..20 никогда не берёт K*q_med
        p_d, p_a = row["move_during"]["null"]["p"], row["move_after"]["null"]["p"]
        assert p_d is None or p_d > 0.05, row
        assert p_a is None or p_a > 0.05, row


def test_walls_pulled_and_significant():
    snaps = _gen_walls()
    res = c3.analyze(snaps, k_factors=(5, 10), lifetimes_s=(10, 30, 120), draws=200, seed=1)
    row = _find(res["rows"], 5, 30, "2-3")
    assert row["n_appear"] >= 25, row
    assert row["share_pulled"] > 0.8, row
    assert row["move_during"]["null"]["p"] < 0.01, row["move_during"]
    assert row["move_during"]["median"] > 0, row["move_during"]           # толкает вверх
    assert row["move_after"]["null"]["p"] < 0.01, row["move_after"]
    assert row["move_after"]["median"] < 0, row["move_after"]             # откат


def test_touched_priority():
    """Best дошёл до P (bid1<=P) на том же снимке, где объём по P тоже упал
    ниже половины пика — приоритет должен отдать touched, не pulled."""
    rng = random.Random(9)
    p_price = BASELINE_BID1 - 2 * STEP
    appear = _snap_wall(0, BASELINE_BID1, BASELINE_BID1 + SPREAD, p_price, 200, rng)
    touched_snap = (1000, [(p_price, 3), (p_price - STEP, 5), (p_price - 2 * STEP, 5),
                            (p_price - 3 * STEP, 5), (p_price - 4 * STEP, 5)],
                     [(p_price + SPREAD, 10), (p_price + SPREAD + STEP, 10),
                      (p_price + SPREAD + 2 * STEP, 10), (p_price + SPREAD + 3 * STEP, 10),
                      (p_price + SPREAD + 4 * STEP, 10)])
    ev = {"seg": [appear, touched_snap], "i": 0, "side": "bid", "level": 3,
          "price": p_price, "vol": 200, "mid_appear": c3._mid(appear), "t_appear": appear[0]}
    res = c3._resolve(ev, 30.0)
    assert res["outcome"] == "touched", res
