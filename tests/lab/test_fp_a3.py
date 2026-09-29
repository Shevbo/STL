"""A3 (momentum ignition) на синтетике — реальных данных здесь нет и не будет."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import a3_momentum_ignition as a3

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())   # метка = МСК-стенка
SESSION_MIN = 300


def _row(rows_list, test, horizon):
    return next(r for r in rows_list if r["test"] == test and r["horizon"] == horizon)


def _walk_bars(n_days=40, seed=1, price_std=0.5, vol_lo=8.0, vol_hi=12.0):
    """Случайное блуждание, объёмы независимы от хода — для проверки отсутствия эффекта."""
    rng = random.Random(seed)
    rows = []
    for d in range(n_days):
        px = 100000.0
        for m in range(SESSION_MIN):
            o = px
            px += rng.gauss(0, price_std)
            c = px
            rows.append([D0 + d * 86400 + m * 60, o, max(o, c), min(o, c), c,
                         rng.uniform(vol_lo, vol_hi)])
    return rows


def _engineered_bars(n_days=40, spike_min=100, base_vol=10.0, vol_mult=30.0,
                      move=0.4, cont_step=1.0, cont_bars=3, rev_step=-0.5,
                      rev_start=5, rev_end=30, seed=7):
    """Дни плоские (h/l = o±0.05 -> ATR ~0.1 без нулевого разброса), кроме
    spike_min: объём 30x, ход +4*ATR (move=4*0.1), затем cont_bars минут
    дрейф +1 пт/мин, мост без хода, с rev_start-й по rev_end-ю минуту дрейф
    -0.5 пт/мин, дальше плоский хвост дня НА ДОСТИГНУТОЙ цене (без прыжка
    назад к исходной — см. reference_splice_phantom_rounds.md проекта:
    молчаливый скачок ряда переворачивает вывод). Драфтовые бары получают
    объём НИЖЕ шумового диапазона, чтобы не пройти объёмный гейт сами по
    себе — разгоном должен остаться только spike_min."""
    rng = random.Random(seed)
    low_vol = base_vol - 2.0
    rows = []
    for d in range(n_days):
        px, day = 100000.0, []
        for m in range(SESSION_MIN):
            ts = D0 + d * 86400 + m * 60
            k = m - spike_min
            if m < spike_min:
                bar = [ts, px, px + 0.05, px - 0.05, px, base_vol + rng.uniform(-1.0, 1.0)]
            elif k == 0:
                c = px + move
                bar = [ts, px, max(px, c), min(px, c), c, base_vol * vol_mult]
                px = c
            elif k <= cont_bars:
                new_c = px + cont_step
                bar = [ts, px, max(px, new_c), min(px, new_c), new_c, low_vol]
                px = new_c
            elif k < rev_start:
                bar = [ts, px, px, px, px, low_vol]
            elif k <= rev_end:
                new_c = px + rev_step
                bar = [ts, px, max(px, new_c), min(px, new_c), new_c, low_vol]
                px = new_c
            else:
                bar = [ts, px, px + 0.05, px - 0.05, px, base_vol + rng.uniform(-1.0, 1.0)]
            day.append(bar)
        rows.extend(day)
    return rows


def test_random_walk_no_effect():
    res = a3.analyze(_walk_bars(), draws=100, seed=0)
    for row in res["rows"]:
        p = row["null"]["p"]
        assert p is None or p > 0.05, row


def test_engineered_ignition_significant():
    res = a3.analyze(_engineered_bars(), draws=200, seed=0)
    cont3 = _row(res["rows"], "cont", 3)
    assert cont3["n"] == 40
    assert abs(cont3["median"] - 3.0) < 1e-6
    assert cont3["null"]["p"] < 0.01

    rev30 = _row(res["rows"], "rev", 30)
    assert rev30["n"] == 40
    assert rev30["median"] < 0
    assert rev30["null"]["p"] < 0.01


def test_close_events_collapse():
    """Два разгона одного дня ближе 5 минут — остаётся первый."""
    day = [[D0 + m * 60, 100000.0, 100000.05, 99999.95, 100000.0, 10.0]
           for m in range(SESSION_MIN)]

    def _spike(i, move, vol):
        o = day[i][1] if i == 0 else day[i - 1][4]
        c = o + move
        day[i][1], day[i][2], day[i][3], day[i][4], day[i][5] = (
            o, max(o, c), min(o, c), c, vol)

    _spike(100, 4.0, 300.0)
    _spike(102, 4.0, 300.0)   # 2 минуты после первого — должен слиться

    merged, _ = a3._events_for_day(day, vol_q=0.99, move_atr=2.0, atr_val=0.1)
    assert merged == [(100, 1)]


def test_far_events_kept_separate():
    day = [[D0 + m * 60, 100000.0, 100000.05, 99999.95, 100000.0, 10.0]
           for m in range(SESSION_MIN)]

    def _spike(i, move, vol):
        o = day[i][1] if i == 0 else day[i - 1][4]
        c = o + move
        day[i][1], day[i][2], day[i][3], day[i][4], day[i][5] = (
            o, max(o, c), min(o, c), c, vol)

    _spike(100, 4.0, 300.0)
    _spike(110, 4.0, 300.0)   # 10 минут после — отдельное событие

    merged, _ = a3._events_for_day(day, vol_q=0.99, move_atr=2.0, atr_val=0.1)
    assert merged == [(100, 1), (110, 1)]
