"""C2 (айсберги) на синтетике — реальных данных здесь нет, считает только i9
(STRICT). (1) обычный стакан без восстановлений: кандидатов почти нет; там,
где случайно нашлись, эффект не отличим от нуля. (2) 20 вставленных айсбергов
в биде (10 лотов, просадка до 2 каждые 2 с, возврат через 1 с, жизнь ~40 с):
находится большинство, slice_mode около 8, после обнаружения ход mid вверх
значим. (3) уровень, съеденный насквозь сразу после обнаружения — попадает в
share_crossed."""
from __future__ import annotations

import random
from datetime import datetime, timezone

from trader.lab.footprints import c2_iceberg as c2

D0 = int(datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())  # 10:00 МСК-стенка


def _filler(price_step_sign, base_q, rng, jitter=0):
    """4 доп. уровня книги (2..5), сама логика C2 их не читает — только вид."""
    return [(price_step_sign * i, max(1, base_q + (rng.randint(-jitter, jitter) if jitter else 0)))
            for i in range(1, 5)]


def _snap(ts_ms, bid1, qb1, ask1, qa1, rng, jitter=0):
    bids = [(bid1, qb1)] + [(bid1 - i, max(1, 50 + (rng.randint(-jitter, jitter) if jitter else 0)))
                             for i in range(1, 5)]
    asks = [(ask1, qa1)] + [(ask1 + i, max(1, 50 + (rng.randint(-jitter, jitter) if jitter else 0)))
                             for i in range(1, 5)]
    return (ts_ms, bids, asks)


# --------------------------------------------------------------------------
# (1) обычный стакан: цена дрейфует, объёмы слегка дрожат, ни разу не
# настолько, чтобы совпасть с порогом min_drop/30% + восстановление 90%.
# --------------------------------------------------------------------------

def _base_rows(seed, days=2, hours=2, move_p=0.3, base_q=50, jitter=3, spread=1.0):
    """jitter=3 при base_q=50: максимальная соседняя разница объёма = 6, часть
    пар всё же может случайно дать drop>=5 (крайние значения диапазона), но
    90%-восстановление вслед за этим — независимое повторное совпадение,
    накопление до R=5 подряд практически не складывается (проверено запуском
    с этим seed)."""
    rng = random.Random(seed)
    rows = []
    for day in range(days):
        px = 100000.0
        day_start = D0 + day * 86400
        for t in range(hours * 3600):
            if rng.random() < move_p:
                px += rng.choice((-1.0, 1.0))
            qb = max(1, base_q + rng.randint(-jitter, jitter))
            qa = max(1, base_q + rng.randint(-jitter, jitter))
            rows.append(_snap((day_start + t) * 1000, px - spread / 2, qb,
                              px + spread / 2, qa, rng))
    return rows


def _row(res, side, R, T):
    return next(r for r in res["rows"] if r["side"] == side and r["R"] == R and r["horizon_s"] == T)


def test_calm_book_no_candidates():
    rows = _base_rows(seed=1)
    res = c2.analyze(rows, min_refills=(3, 5), horizons_s=(60, 300), draws=200, seed=0)
    for side in ("bid", "ask"):
        row = _row(res, side, 5, 60)
        assert row["n_icebergs"] <= 2, row
        if row["n_icebergs"] > 0:
            assert row["move_after"]["null"]["p"] > 0.05, row["move_after"]


# --------------------------------------------------------------------------
# (2) 20 айсбергов в биде: 10 лотов, просадка до 2 каждые 2 с (соседние
# снимки), возврат через 1 с; после конца эпизода — рамп +2 пт за 60 с, затем
# плато (эффект виден на любом достаточно большом T).
# --------------------------------------------------------------------------

def _iceberg_rows(seed=11, n_icebergs=20, hours=2, episode_len_s=40, base_q=10,
                  trough_q=2, spread=1.0, ramp_s=60, ramp_pts=2.0, gap_s=150):
    rng = random.Random(seed)
    rows = []
    px = 100000.0
    n_sec = hours * 3600
    day_start = D0
    t = 0
    placed = 0
    next_at = 20
    day = 0
    while day < 2:
        if t >= n_sec:
            day += 1
            t = 0
            day_start = D0 + day * 86400
            continue
        if placed < n_icebergs and t == next_at and t + episode_len_s + ramp_s < n_sec:
            # -2.0: цена эпизода должна ОТЛИЧАТЬСЯ от фонового best, иначе
            # пробег склеится с предшествующим спокойным участком и жизнь на
            # best будет мерить не сам айсберг, а весь тихий период до него.
            p = px - spread / 2.0 - 2.0
            for lt in range(episode_len_s):
                qty = trough_q if lt % 2 == 1 else base_q
                rows.append(_snap((day_start + t + lt) * 1000, p, qty, p + spread, 50, rng))
            t += episode_len_s
            px = p + spread / 2.0
            for lt in range(ramp_s):
                frac = min(1.0, (lt + 1) / ramp_s)
                cur = px + ramp_pts * frac
                rows.append(_snap((day_start + t + lt) * 1000, cur - spread / 2, 50,
                                  cur + spread / 2, 50, rng))
            t += ramp_s
            px += ramp_pts
            placed += 1
            next_at = t + gap_s
            continue
        rows.append(_snap((day_start + t) * 1000, px - spread / 2, 50, px + spread / 2, 50, rng))
        t += 1
    return rows


def test_inserted_icebergs_found_and_move_after_significant():
    rows = _iceberg_rows()
    res = c2.analyze(rows, min_refills=(3, 5), horizons_s=(60, 300), draws=200, seed=0)
    for R in (3, 5):
        row = _row(res, "bid", R, 60)
        assert row["n_icebergs"] >= 15, row
        assert row["slice_mode"] == 8, row
        assert 30 <= row["life_median_s"] <= 45, row
        assert row["move_after"]["pos_share"] > 0.9, row["move_after"]
        assert row["move_after"]["null"]["p"] < 0.01, row["move_after"]
    # сторона ask этим фикстурой не трогалась — кандидатов там нет
    ask_row = _row(res, "ask", 3, 60)
    assert ask_row["n_icebergs"] == 0


# --------------------------------------------------------------------------
# (3) уровень съеден насквозь сразу после обнаружения -> share_crossed > 0.
# --------------------------------------------------------------------------

def _crossed_rows(seed=5, base_q=10, trough_q=2, spread=1.0, crash_at=14, tail=16):
    rng = random.Random(seed)
    rows = []
    p = 100000.0
    for lt in range(crash_at):
        qty = trough_q if lt % 2 == 1 else base_q
        rows.append(_snap((D0 + lt) * 1000, p, qty, p + spread, 50, rng))
    crashed_bid, crashed_ask = p - 5.0, p - 4.0  # ask провалился ниже старого P
    for lt in range(tail):
        rows.append(_snap((D0 + crash_at + lt) * 1000, crashed_bid, 50, crashed_ask, 50, rng))
    return rows


def test_level_crossed_counts_in_share_crossed():
    rows = _crossed_rows()
    res = c2.analyze(rows, min_refills=(3, 5), horizons_s=(10,), draws=20, seed=0)
    for R in (3, 5):
        row = _row(res, "bid", R, 10)
        assert row["n_icebergs"] == 1, row
        assert row["share_crossed"] == 1.0, row
