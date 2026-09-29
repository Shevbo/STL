"""D1 (периодичность детских заявок) на синтетике (STRICT: реальных данных
здесь нет, считает только i9).

ПРОИЗВОДИТЕЛЬНОСТЬ. Спектр здесь -- прямой DFT без numpy (см. докстринг
d1_execution_periodicity.py): O(119*L) на один полный проход, L = длина окна
в секундах. Полный analyze() перебирает до 10 размеров-кандидатов и небыстро
укладывается в pytest.ini timeout=30 на тест, поэтому тесты ниже вызывают
_interval_row/_spectrum_row НАПРЯМУЮ для ОДНОЙ проверяемой строки вместо
analyze() целиком (тот же код, что использует analyze(), просто без 10
лишних кандидатов) -- окна и draws подобраны так, чтобы уложиться в лимит
(замер: тест с роботом ~10-15 с).

p<0.01 из задания требуется только для интервального теста (нужно
draws>=100, см. common.pvalue_and_ci); для спектра в задании -- только факт
пика на периоде 30 с, без порога по p, поэтому его null считается с малым
draws (дёшево по времени, спектр и так самый дорогой шаг)."""
from __future__ import annotations

import math
import random
from collections import Counter
from datetime import datetime, timezone

from trader.lab.footprints import common
from trader.lab.footprints import d1_execution_periodicity as d1

D0 = int(datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp())  # 10:00, до 16:00 -> одна половина


def _poisson(rng: random.Random, lam: float = 3.0) -> int:
    """Пуассоновское число событий (алгоритм Кнута, без numpy)."""
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= limit:
            return k - 1


def _geom(rng: random.Random, p: float = 0.5) -> int:
    """Геометрическое число >=1 (метод обратной функции распределения)."""
    u = rng.random()
    return max(1, int(math.log(1 - u) / math.log(1 - p)) + 1)


def _tape_random(duration_s: int, seed: int) -> list[list]:
    """Фон: пуассоновский поток ~3 сделки/с, размер геометрический, сторона 50/50."""
    rng = random.Random(seed)
    rows = []
    for s in range(duration_s):
        for _ in range(_poisson(rng)):
            rows.append([D0 + s, 100000.0, _geom(rng), 1 if rng.random() < 0.5 else 0])
    return rows


def _tape_robot(duration_s: int, seed: int, qty: int = 7, interval: int = 30,
                 robot_span_s: int = 3600) -> list[list]:
    """Фон + «робот»: покупка `qty` лотов ровно каждые `interval` с первые
    `robot_span_s` секунд окна."""
    rows = _tape_random(duration_s, seed)
    for s in range(0, min(robot_span_s, duration_s), interval):
        rows.append([D0 + s, 100000.0, qty, 1])
    rows.sort(key=lambda r: r[0])
    return rows


def test_random_walk_no_effect():
    """(1) Случайный фон без вставок -> интервальный и спектральный тест
    самого частого размера-кандидата не значимы (p > 0.05)."""
    rows = _tape_random(2400, seed=1)
    days_rows = common.by_day(rows)
    candidates = d1._candidate_sizes(rows)
    assert candidates, "нет размера-кандидата -- окно синтетики слишком короткое"
    qty = candidates[0][0]
    rng = random.Random(0)

    interval_row = d1._interval_row(rows, days_rows, qty, 1, peak_ratio=2.0, draws=30, rng=rng)
    spectrum_row = d1._spectrum_row(rows, days_rows, qty, qty, spec_ratio=4.0, draws=8, rng=rng)

    assert interval_row is not None and spectrum_row is not None
    assert interval_row["null"]["p"] > 0.05
    assert spectrum_row["null"]["p"] > 0.05


def test_robot_interval_and_spectrum_peak_detected():
    """(2) Покупка 7 лотов ровно каждые 30 с в течение часа -> у qty=7,
    side=buy пик гистограммы на dt=30 ratio>=2 и p<0.01; спектр qty=7 даёт
    пик на периоде 30 с."""
    rows = _tape_robot(5400, seed=2, qty=7, interval=30, robot_span_s=3600)
    days_rows = common.by_day(rows)
    n_qty7 = sum(1 for r in rows if r[2] == 7)
    assert n_qty7 >= d1.MIN_QTY_COUNT, f"qty=7 не набрал кандидатский порог: {n_qty7}"
    rng = random.Random(0)

    interval_row = d1._interval_row(rows, days_rows, 7, 1, peak_ratio=2.0, draws=120, rng=rng)
    assert interval_row is not None
    peak30 = next((p for p in interval_row["peaks"] if p["dt"] == 30), None)
    assert peak30 is not None, interval_row["peaks"]
    assert peak30["ratio"] >= 2.0
    assert interval_row["null"]["p"] < 0.01

    spectrum_row = d1._spectrum_row(rows, days_rows, 7, 7, spec_ratio=4.0, draws=6, rng=rng)
    assert spectrum_row is not None
    period30 = next((t for t in spectrum_row["top_periods"] if t["period_s"] == 30), None)
    assert period30 is not None, spectrum_row["top_periods"]


def test_shuffle_minute_preserves_per_minute_trade_count():
    """(3) Перестановка секунд внутри минуты не меняет число сделок в
    каждой минуте (только их расположение внутри неё)."""
    rows = _tape_random(1800, seed=3)
    rng = random.Random(5)
    shuffled = d1._shuffle_minute(rows, rng)

    assert len(shuffled) == len(rows)
    orig_per_minute = Counter(r[0] // 60 for r in rows)
    shuf_per_minute = Counter(r[0] // 60 for r in shuffled)
    assert orig_per_minute == shuf_per_minute
    # qty/side каждой строки не тронуты, только позиция ts_s внутри минуты
    assert Counter(r[2] for r in rows) == Counter(r[2] for r in shuffled)
    assert Counter(r[3] for r in rows) == Counter(r[3] for r in shuffled)
