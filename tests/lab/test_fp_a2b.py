"""Отпечаток A2b (откат после экстремума на mid стакана) на синтетике —
реальных данных здесь нет, считает только i9 (STRICT). Проверяет: (1)
построение минутных баров mid из снимков стакана раз в секунду и пропуск
дыры > 60 с; (2) случайный mid -> нет эффекта (p_two); (3) mid с дрейфом
-3 пт/мин после нового 20-минутного максимума -> откат виден (p_low)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import a2_channel_breakout as a2
from trader.lab.footprints import a2b_extreme_reversion_mid as a2b

D0 = int(datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())  # 10:00 МСК-стенка, в сессии


def _book_snap(t_sec: int, mid: float, qty: int = 50):
    """Снимок стакана (5 уровней, формат c1._load_full_book) на t_sec от D0."""
    bids = [(mid - 1.0 - i * 0.5, qty) for i in range(5)]
    asks = [(mid + 1.0 + i * 0.5, qty) for i in range(5)]
    return ((D0 + t_sec) * 1000, bids, asks)


def _book_rows_from_closes(closes_by_day: list[list[float]], snaps_per_min: int = 4,
                            noise: float = 0.05, seed: int = 0) -> list[tuple]:
    """closes_by_day[d][m] = целевой close минуты m дня d. Несколько снимков на
    минуту, mid линейно идёт от close предыдущей минуты к текущему; ПОСЛЕДНИЙ
    снимок минуты = ровно целевой close (агрегация _minute_bars_mid даёт
    минутный бар с этим close независимо от snaps_per_min)."""
    rng = random.Random(seed)
    rows = []
    for d, closes in enumerate(closes_by_day):
        day_base = d * 86400
        prev = closes[0]
        for m, c in enumerate(closes):
            for s in range(snaps_per_min):
                last = s == snaps_per_min - 1
                frac = (s + 1) / snaps_per_min
                mid = c if last else prev + (c - prev) * frac + rng.uniform(-noise, noise)
                t_sec = day_base + m * 60 + int(s * 60 / snaps_per_min)
                rows.append(_book_snap(t_sec, mid))
            prev = c
    return rows


# ---------------------------------------------------------------------------
# (1) построение минутных баров mid и пропуск дыры
# ---------------------------------------------------------------------------

def test_minute_bars_ohlc_and_gap_skip():
    mids0 = [100.0 + (i % 7) * 0.5 for i in range(60)]
    rows = [_book_snap(t, mids0[t]) for t in range(60)]                        # минута 0
    rows.append(_book_snap(150, 100.0))                                        # минута 2: дыра 90 с к минуте 0
    rows += [_book_snap(t, 100.0 + (t % 5) * 0.2) for t in range(151, 180)]    # минута 2: остаток
    mids3 = [90.0 + (i % 9) * 0.4 for i in range(60)]
    rows += [_book_snap(180 + t, mids3[t]) for t in range(60)]                # минута 3: снова чисто

    bars, skipped_gap = a2b._minute_bars_mid(rows)
    minute_offsets = {b[0] - D0 for b in bars}

    # минута 0 (хвост задет дырой), минута 1 (снимков нет) и минута 2 (начало
    # задето дырой) не строятся; только минута 3 чистая с обеих сторон.
    assert minute_offsets == {180}, minute_offsets
    assert skipped_gap == 2  # минуты 0 и 2 имели снимки, но исключены из-за дыры

    bar3 = next(b for b in bars if b[0] - D0 == 180)
    assert bar3[1] == mids3[0]
    assert bar3[2] == max(mids3)
    assert bar3[3] == min(mids3)
    assert bar3[4] == mids3[-1]
    assert bar3[5] == 60


def test_no_gap_all_minutes_built():
    rows = [_book_snap(t, 100.0 + (t % 11) * 0.1) for t in range(180)]  # 3 минуты, без дыр
    bars, skipped_gap = a2b._minute_bars_mid(rows)
    assert {b[0] - D0 for b in bars} == {0, 60, 120}
    assert skipped_gap == 0
    assert all(b[5] == 60 for b in bars)


# ---------------------------------------------------------------------------
# (2) случайный mid -> нет эффекта
# ---------------------------------------------------------------------------

def _closes_walk(n_days=15, minutes=150, seed=1):
    rng = random.Random(seed)
    out, px = [], 100000.0
    for _ in range(n_days):
        day = []
        for _ in range(minutes):
            px += rng.uniform(-2, 2)
            day.append(px)
        out.append(day)
    return out


def test_random_walk_no_effect():
    book_rows = _book_rows_from_closes(_closes_walk(), seed=3)
    bars, _ = a2b._minute_bars_mid(book_rows)
    res = a2.analyze(bars, lookbacks=(10, 30), horizons=(5, 20), draws=200, seed=0)
    for n in (10, 30):
        for h in (5, 20):
            row = next(r for r in res["rows"] if r["N"] == n and r["h"] == h)
            p_two = row["null"]["p_two"]
            assert p_two is None or p_two > 0.05, (n, h, row["null"])


# ---------------------------------------------------------------------------
# (3) новый 20-минутный максимум -> дрейф -3 пт/мин на 15 минут (откат)
# ---------------------------------------------------------------------------

def _closes_reversal(n_days=10, minutes=120, seed=2, lookback=20, horizon=15,
                      jump=60.0, drift=-3.0, period=40):
    """Как a2_channel_breakout._bars_drift, но по close минуты, а не по бару:
    каждые `period` минут — гарантированный пробой вверх на `jump` от baseline
    после `lookback` минут узкого канала, затем `drift`/мин на `horizon` минут
    (откат). `jump` намеренно с запасом больше |drift|*horizon: иначе откат
    пересекает исходный канал заново и на этом пересечении рождается
    паразитный пробой ВНИЗ (уже не разворот, а продолжение падения) — его
    положительный исход гасит медиану настоящего разворота почти до нуля
    (проверено: с jump=12 median сходится к -0.6 вместо -45)."""
    rng = random.Random(seed)
    out = []
    for _ in range(n_days):
        day = []
        baseline = 100000.0
        px = baseline
        drift_left = 0
        for m in range(minutes):
            if m % period == lookback:
                px = baseline + jump
                drift_left = horizon
            elif drift_left > 0:
                px = px + drift
                drift_left -= 1
                if drift_left == 0:
                    baseline = px
            else:
                px = baseline + rng.uniform(-1.0, 1.0)
            day.append(px)
        out.append(day)
    return out


def test_reversal_after_extreme_effect():
    book_rows = _book_rows_from_closes(_closes_reversal(), seed=4)
    bars, _ = a2b._minute_bars_mid(book_rows)
    res = a2.analyze(bars, lookbacks=(20,), horizons=(15,), draws=200, seed=0)
    row = next(r for r in res["rows"] if r["N"] == 20 and r["h"] == 15)
    assert row["n"] >= 5, row["n"]
    assert row["median"] < -20, row["median"]          # откат, не мелкий шум
    assert row["null"]["p_low"] < 0.01, row["null"]
