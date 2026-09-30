"""B2 (возврат к VWAP дня) на синтетике: без эффекта, с внедрённым дрейфом к
VWAP, гистерезис - одно событие на заход. Реальных данных здесь нет (i9 их не
считает, см. зону backtests)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import b2_vwap_reversion as b2

D0 = int(datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp())   # 10:00 МСК-стенка, в сессии
ATR = 4.0  # H-L=4 константа в _row -> common.atr_minute даёт ровно 4.0


def _row(ts, px):
    """OHLCV с H-L=4 и v=1: typical=(h+l+c)/3=close (h=c+2,l=c-2 гасят друг друга),
    поэтому VWAP с постоянным объёмом = обычная кумулятивная средняя close."""
    return [ts, px, px + 2.0, px - 2.0, px, 1.0]


def _bars_walk(n_days=30, seed=1, minutes=300, std=1.0):
    """Случайное блуждание без внедрённого эффекта."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    for d in range(n_days):
        for m in range(minutes):
            px += rng.gauss(0, std)
            rows.append(_row(D0 + d * 86400 + m * 60, px))
    return rows


def _bars_reversion(n_days=30, seed=3, minutes=300, drift=0.5, atr=ATR):
    """Как _bars_walk, но если |z| ПРЕДЫДУЩЕГО бара >= 2, приращение текущего
    получает дрейф drift*ATR к VWAP (используем z предыдущего бара, чтобы не
    звать текущий бар в собственном условии - иначе циклическая зависимость)."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    for d in range(n_days):
        cum, cnt, prev_z = 0.0, 0, 0.0
        for m in range(minutes):
            step = rng.gauss(0, 1.0)
            if abs(prev_z) >= 2.0:
                step += (-1.0 if prev_z > 0 else 1.0) * drift * atr
            px += step
            rows.append(_row(D0 + d * 86400 + m * 60, px))
            cum += px
            cnt += 1
            prev_z = (px - cum / cnt) / atr
    return rows


def _row_res(res, k, h):
    return next(r for r in res["rows"] if r["k"] == k and r["h"] == h)


def test_random_walk_no_effect():
    rows = _bars_walk()
    res = b2.analyze(rows, k_levels=(2, 3), horizons=(15, 30), draws=60, seed=0)
    for k in (2, 3):
        for h in (15, 30):
            row = _row_res(res, k, h)
            assert row["null_move"]["p"] is None or row["null_move"]["p"] > 0.05, (k, h, row["null_move"])
            assert row["null_touch"]["p"] is None or row["null_touch"]["p"] > 0.05, (k, h, row["null_touch"])


def test_reversion_drift_effect():
    rows = _bars_reversion()
    # draws=200 обязателен: p-value ограничен снизу 1/(draws+1) (common.pvalue_and_ci).
    res = b2.analyze(rows, k_levels=(2,), horizons=(30,), draws=200, seed=0)
    row = _row_res(res, 2, 30)
    assert row["n"] >= 10, row["n"]
    assert row["null_move"]["p"] < 0.01, row["null_move"]
    assert row["median"] > 0, row["median"]  # подписанный ход положителен = к VWAP


def test_hysteresis_one_event_per_excursion():
    k = 2.0
    z = [0.0, 0.4, 0.9, 1.1, 2.1, 2.4, 1.8, 2.05, 0.3, 0.6, 2.2, 2.6]
    events = b2._hysteresis_events(z, k, warmup=0)
    assert [i for i, _ in events] == [4, 10]
    assert [s for _, s in events] == [1, 1]
    # warmup гасит только регистрацию, не сбрасывает состояние гистерезиса:
    # событие на i=4 не попадает в список, но взвод/сброс отработал как обычно.
    events_w = b2._hysteresis_events(z, k, warmup=5)
    assert [i for i, _ in events_w] == [10]
