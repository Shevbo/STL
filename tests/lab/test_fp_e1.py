"""E1 (ведущий инструмент) на синтетике: реальных данных здесь нет."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import e1_lead_lag as e1

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())   # метка = МСК-стенка
N_DAYS, MINUTES = 15, 60


def _walk(seed: int, start: float, n_days=N_DAYS, minutes=MINUTES) -> list[list]:
    """Независимое случайное блуждание по закрытию, o=h=l=c для простоты."""
    rng = random.Random(seed)
    rows, px = [], start
    for d in range(n_days):
        for m in range(minutes):
            px += rng.gauss(0, 20)
            ts = D0 + d * 86400 + m * 60
            rows.append([ts, px, px, px, px, 1.0])
    return rows


def _linked_bars(seed: int, lag: int, coef: float = 0.5, noise_sd: float = 5.0,
                  n_days=N_DAYS, minutes=MINUTES) -> tuple[list[list], list[list]]:
    """follow: r_follow[t] = coef * r_lead[t-lag] + шум, т.е. r_lead ведёт r_follow на +lag."""
    rng = random.Random(seed)
    lead_rows, follow_rows = [], []
    lead_px, follow_px = 100000.0, 50000.0
    for d in range(n_days):
        day_lead_incs = [rng.gauss(0, 20) for _ in range(minutes - 1)]
        lead_closes = [lead_px]
        for r in day_lead_incs:
            lead_closes.append(lead_closes[-1] + r)
        lead_px = lead_closes[-1]
        follow_closes = [follow_px]
        for j in range(minutes - 1):
            base = day_lead_incs[j - lag] if j - lag >= 0 else 0.0
            follow_closes.append(follow_closes[-1] + coef * base + rng.gauss(0, noise_sd))
        follow_px = follow_closes[-1]
        for m in range(minutes):
            ts = D0 + d * 86400 + m * 60
            lc, fc = lead_closes[m], follow_closes[m]
            lead_rows.append([ts, lc, lc, lc, lc, 1.0])
            follow_rows.append([ts, fc, fc, fc, fc, 1.0])
    return lead_rows, follow_rows


def _row(res, lag):
    return next(r for r in res["rows"] if r["lag"] == lag)


def test_independent_walks_no_lag_effect():
    """Два независимых блуждания: p > 0.05 на всех лагах, в т.ч. по знаку хода."""
    lead = _walk(seed=1, start=100000.0)
    follow = _walk(seed=2, start=50000.0)
    res = e1.analyze(lead, follow, lags=tuple(range(-5, 6)), draws=100, seed=0)
    for row in res["rows"]:
        assert row["null"]["p"] > 0.05, row["lag"]
        if "null_move" in row:
            assert row["null_move"]["p"] > 0.05, row["lag"]


def test_lagged_link_detected_at_own_lag_only():
    """follow построен как 0.5*r_lead[t-2] + шум: сильный сигнал на lag=2, тишина на lag=-2."""
    lead, follow = _linked_bars(seed=3, lag=2)
    res = e1.analyze(lead, follow, lags=(-2, -1, 0, 1, 2, 3), draws=100, seed=0)
    row2 = _row(res, 2)
    assert row2["corr"] > 0.7
    assert row2["null"]["p"] < 0.01
    assert row2["null_move"]["p"] < 0.01
    assert row2["sign_agree_top_decile"] > 0.8
    row_neg2 = _row(res, -2)
    assert row_neg2["null"]["p"] > 0.05


def test_align_drops_unmatched_ts():
    """Бары без пары по ts выпадают из пересечения (тут теряются у лидера), notes не молчит."""
    lead = _walk(seed=4, start=100000.0, n_days=3, minutes=10)
    follow_full = _walk(seed=5, start=50000.0, n_days=3, minutes=10)
    follow = [r for r in follow_full if r[0] % 300 != 0]   # выкинули каждую 5-ю минуту
    removed = len(follow_full) - len(follow)
    assert removed > 0
    res = e1.analyze(lead, follow, lags=(0,), draws=5, seed=0)
    assert any(f"отброшено {removed} баров лидера, 0 баров последователя" in n
               for n in res["notes"])
    assert _row(res, 0)["n"] == len(follow) - res["n_days"]


def test_no_overnight_increment():
    """Приращение не считается через границу дня: n = сумма (длина_дня - 1), не (всего - 1)."""
    n_days, minutes = 4, 6
    lead = _walk(seed=6, start=100000.0, n_days=n_days, minutes=minutes)
    follow = _walk(seed=7, start=50000.0, n_days=n_days, minutes=minutes)
    # сдвиг всего дня на огромную константу: приращение через границу дня, если бы
    # оно считалось, было бы аномально большим (внутри дня сдвиг сокращается)
    for r in lead:
        d = (r[0] - D0) // 86400
        r[4] += d * 5_000_000.0
    res = e1.analyze(lead, follow, lags=(0,), draws=5, seed=0)
    assert res["n_days"] == n_days
    assert _row(res, 0)["n"] == n_days * (minutes - 1)
