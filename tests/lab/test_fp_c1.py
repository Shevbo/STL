"""Отпечаток C1 (дисбаланс стакана/microprice) на синтетике — реальных данных
здесь нет, они считаются только на i9 (STRICT)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import c1_book_imbalance as c1

D0 = int(datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())  # 10:00 МСК-стенка


def _book_rows(n_days=2, hours=2, seed=1, bias_pts=0.0, mislabel_p=0.2, sigma=1.0,
               base_q=50, skew=30):
    """Снимки стакана раз в секунду, n_days x hours часов с 10:00. bias_pts>0
    делает так, что знак приращения mid через 1 с совпадает со знаком I_1
    текущего снимка с вероятностью (1 - mislabel_p) — см. test_biased_imbalance."""
    rng = random.Random(seed)
    rows = []
    n_sec = hours * 3600
    for day in range(n_days):
        px = 100000.0
        day_start = D0 + day * 86400
        for t in range(n_sec):
            # запись СНАЧАЛА (до шага t), затем шаг t с pd применяется к px —
            # он покажется в mid следующего снимка, не текущего, иначе I(t)
            # коррелирует с mid(t), а не с будущим ходом mid(t+1)-mid(t).
            bid1, ask1 = px - 1.0, px + 1.0
            pd = rng.choice((1, -1))
            sign = pd if rng.random() < (1 - mislabel_p) else -pd
            qb1 = max(1, base_q + (skew if sign > 0 else -skew) + rng.randint(-5, 5))
            qa1 = max(1, base_q - (skew if sign > 0 else -skew) + rng.randint(-5, 5))
            bids = [(bid1, qb1)] + [(bid1 - i * 0.5, max(1, base_q + rng.randint(-5, 5))) for i in range(1, 5)]
            asks = [(ask1, qa1)] + [(ask1 + i * 0.5, max(1, base_q + rng.randint(-5, 5))) for i in range(1, 5)]
            rows.append(((day_start + t) * 1000, bids, asks))
            px += rng.gauss(0, sigma) + pd * bias_pts
    return rows


def _gap_rows(seg1=200, gap_s=70, seg2=200, seed=5):
    """Один день: seg1 непрерывных снимков, разрыв gap_s секунд, seg2 снимков.
    gap_s=0 сливает сегменты в один непрерывный блок (контроль без дыры)."""
    rng = random.Random(seed)
    rows, px, t = [], 100000.0, 0

    def _row(tt):
        nonlocal px
        px += rng.gauss(0, 1.0)
        bid1, ask1 = px - 1.0, px + 1.0
        bids = [(bid1, 50)] + [(bid1 - i * 0.5, 50) for i in range(1, 5)]
        asks = [(ask1, 50)] + [(ask1 + i * 0.5, 50) for i in range(1, 5)]
        return ((D0 + tt) * 1000, bids, asks)

    for _ in range(seg1):
        rows.append(_row(t))
        t += 1
    t += gap_s
    for _ in range(seg2):
        rows.append(_row(t))
        t += 1
    return rows


def _row(res, k, h):
    return next(r for r in res["rows"] if r["k"] == k and r["horizon_s"] == h)


def test_random_walk_no_effect():
    rows = _book_rows(bias_pts=0.0, seed=1)
    res = c1.analyze(rows, depths=(1, 3), horizons_s=(5, 300), draws=200, seed=0)
    for k in (1, 3):
        for h in (5, 300):
            row = _row(res, k, h)
            assert row["null_corr"]["p"] > 0.05, (k, h, row["null_corr"])
            assert row["null_move"]["p"] > 0.05, (k, h, row["null_move"])


def test_biased_imbalance_predicts_short_horizon_only():
    rows = _book_rows(bias_pts=0.2, mislabel_p=0.2, seed=7)
    res = c1.analyze(rows, depths=(1,), horizons_s=(5, 300), draws=200, seed=0)
    near = _row(res, 1, 5)
    far = _row(res, 1, 300)
    assert near["null_corr"]["p"] < 0.01, near["null_corr"]
    assert far["null_corr"]["p"] > 0.05, far["null_corr"]
    assert near["sign_agree"] > 0.5


def test_default_depths_l1_only_effect():
    """Дефолт: "2-5" есть, "micro" нет. Смещение только в L1 -> на глубине без
    L1 корреляции нет (эффект = очередь L1), наклон и среднее хода положительны."""
    rows = _book_rows(bias_pts=0.2, mislabel_p=0.2, seed=7)
    res = c1.analyze(rows, horizons_s=(5,), draws=100, seed=0)
    assert {r["k"] for r in res["rows"]} == {1, 3, 5, "2-5"}
    l1, deep = _row(res, 1, 5), _row(res, "2-5", 5)
    assert l1["slope"] > 0 and l1["mean_signed_move_top_quintile"] > 0
    assert abs(deep["corr"]) < 0.05 and deep["null_corr"]["p_two"] > 0.05
    assert l1["zero_share"] == 0.0
    micro = c1.analyze(rows, depths=("micro",), horizons_s=(5,), draws=5, seed=0)
    assert [r["k"] for r in micro["rows"]] == ["micro"]


def test_sign_agree_ignores_zero_moves():
    assert c1._sign_agree([1.0, -1.0, 1.0, 1.0], [2.0, 0.0, 0.0, -1.0]) == 0.5
    assert c1._sign_agree([1.0], [0.0]) is None


def test_gap_cuts_forward_window():
    with_gap = c1.analyze(_gap_rows(gap_s=70, seed=5), depths=(1,), horizons_s=(60,), draws=5, seed=0)
    assert _row(with_gap, 1, 60)["n"] == 280

    no_gap = c1.analyze(_gap_rows(gap_s=0, seed=5), depths=(1,), horizons_s=(60,), draws=5, seed=0)
    assert _row(no_gap, 1, 60)["n"] == 340
