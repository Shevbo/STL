"""D1: исполнительные алгоритмы (TWAP/VWAP-нарезка, айсберг-роботы) по ленте.

Реестр: docs/algo-footprints-registry.md, раздел D, строка D1. Лента — как у
D23 (d23_tape_flow.py): _fetch/_prepare/_halves_rows/_atr_min_pts
ПЕРЕИСПОЛЬЗУЮТСЯ импортом, не копируются (см. докстринг d23 про side_buy и
внутреннее представление [ts_s, price, qty, is_buy]).

СЛЕД. Крупный заказ режут на детские заявки одного размера с постоянным
интервалом. Для каждого размера-кандидата и стороны: гистограмма интервалов
dt=1..120 с (пик = локальный максимум против обоих соседей) и спектр ряда
"число сделок в секунду" (детренд скользящим средним 5 мин, мощность на
периодах 2..120 с против медианы окна ±10).

СПЕКТР БЕЗ NUMPY: пакета нет в окружении (проверено `python -c "import
numpy"` -- ModuleNotFoundError). Прямой DFT только на нужных целых периодах
2..120 с, O(119*L) на серию -- на дне длиной сутки (i9, ~36000 с) это ~4.3М
операций на серию, приемлемо для фонового задания очереди.

НУЛЕВОЕ РАСПРЕДЕЛЕНИЕ (_shuffle_minute): common.shuffle_within_day
переставляет ts_s сделок внутри каждой АБСОЛЮТНОЙ МИНУТЫ (группа = ts_s //
60) -- тот же хелпер, что и в common.py, только группа не день, а минута;
переиспользован, а не продублирован. Число сделок в минуте и её per-qty
состав сохраняются ТОЧНО (переставляются только секунды внутри минуты),
периодичность конкретного (qty, side) рвётся.

ponytail: спектр "all" (qty=None, все размеры разом) ИНВАРИАНТЕН к этому
контролю -- перестановка внутри минуты не меняет общий мультимножество
секунд дня, поэтому у строки test=spectrum, qty=all null.p всегда ~1
(контроль не бьёт по этому ряду, это отражено в notes отчёта). Показательны
per-qty строки, где контроль реально меняет то, ЧЕЙ (какого qty) трейд стоит
на какой секунде.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import Counter

from trader.lab.footprints import common
from trader.lab.footprints.d23_tape_flow import _atr_min_pts, _fetch, _halves_rows, _prepare

MIN_QTY_COUNT = 200
MAX_CANDIDATES = 10
DT_MAX = 120
SPEC_PERIOD_MIN = 2
SPEC_PERIOD_MAX = 120
SPEC_WINDOW = 10
SMOOTH_WINDOW_S = 300  # 5 минут
MIN_SHARE = 0.02
MIN_INTERVALS = 5
_EPS = 1e-9


def _candidate_sizes(rows: list[list]) -> list[tuple[int, int]]:
    """10 самых частых qty дня (кроме 1, кроме <200 сделок), по убыванию
    частоты; при равной частоте порядок по qty для детерминизма."""
    counts = Counter(r[2] for r in rows if r[2] != 1)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(q, c) for q, c in ranked if c >= MIN_QTY_COUNT][:MAX_CANDIDATES]


def _shuffle_minute(rows: list[list], rng: random.Random) -> list[list]:
    """Контроль: ts_s переставлены внутри каждой абсолютной минуты
    (common.shuffle_within_day, группа = минута вместо дня). qty/side каждой
    строки не трогаются."""
    minute_keys = [r[0] // 60 for r in rows]
    shuffled_ts = common.shuffle_within_day([r[0] for r in rows], minute_keys, rng)
    return [[t, r[1], r[2], r[3]] for t, r in zip(shuffled_ts, rows)]


def _boot_blocks(rows: list[list], days_rows: dict) -> dict:
    """Бутстрап по целым дням, когда их больше одного; при одном дне -- по
    его половинам (до/после 16:00, common.d23._halves_rows)."""
    if len(days_rows) <= 1:
        first, second = _halves_rows(rows)
        return {"h1": first, "h2": second}
    return days_rows


# ------------------------------------------------------------- интервалы ----


def _intervals(rows_sorted: list[list]) -> list[int]:
    """Целые интервалы между соседними ts_s уже отфильтрованной
    последовательности, 0 < dt <= DT_MAX (большие разрывы вне области гипотезы)."""
    out = []
    prev = None
    for ts, *_ in rows_sorted:
        if prev is not None:
            dt = ts - prev
            if 0 < dt <= DT_MAX:
                out.append(dt)
        prev = ts
    return out


def _dt_ratios(dts: list[int]) -> tuple[dict[int, float], dict[int, float], int]:
    """(ratio(dt)=count/соседи, share(dt)=count/n, n) для dt=1..DT_MAX.
    ratio(dt) сравнивается ТОЛЬКО с существующими соседями (dt-1, dt+1);
    знаменатель floor=0.5, чтобы не делить на ноль (без inf в JSON)."""
    n = len(dts)
    counts = [0] * (DT_MAX + 1)
    for dt in dts:
        counts[dt] += 1
    ratios, shares = {}, {}
    for dt in range(1, DT_MAX + 1):
        neigh = [counts[d] for d in (dt - 1, dt + 1) if 1 <= d <= DT_MAX]
        denom = max(neigh + [0.0], default=0.0)
        ratios[dt] = counts[dt] / max(denom, 0.5)
        shares[dt] = counts[dt] / n if n else 0.0
    return ratios, shares, n


def _interval_stat(rows: list[list], qty: int, side: int) -> tuple[dict, dict, int, float] | None:
    sub = [r for r in rows if r[2] == qty and r[3] == side]
    dts = _intervals(sub)
    if len(dts) < MIN_INTERVALS:
        return None
    ratios, shares, n = _dt_ratios(dts)
    return ratios, shares, n, max(ratios.values())


def _interval_max_ratio(rows: list[list], qty: int, side: int) -> float | None:
    stat = _interval_stat(rows, qty, side)
    return stat[3] if stat else None


def _interval_row(rows: list[list], days_rows: dict, qty: int, side: int,
                   peak_ratio: float, draws: int, rng: random.Random) -> dict | None:
    stat = _interval_stat(rows, qty, side)
    if stat is None:
        return None
    ratios, shares, n, max_ratio = stat
    peaks = sorted(
        ({"dt": dt, "share": round(shares[dt], 5), "ratio": round(r, 3)}
         for dt, r in ratios.items() if r >= peak_ratio and shares[dt] >= MIN_SHARE),
        key=lambda p: -p["ratio"],
    )

    null_stats = [_interval_max_ratio(_shuffle_minute(rows, rng), qty, side) for _ in range(draws)]
    boot_stats = common.bootstrap_days(
        _boot_blocks(rows, days_rows),
        lambda blocks: _interval_max_ratio([r for blk in blocks for r in blk], qty, side),
        draws, rng,
    )

    return {
        "test": "interval", "qty": qty, "side": "buy" if side else "sell", "n": n,
        "peaks": peaks, "max_ratio": round(max_ratio, 3),
        "null": common.pvalue_and_ci(max_ratio, null_stats, boot_stats),
    }


# --------------------------------------------------------------- спектр ----


def _per_second_counts(rows: list[list], qty: int | None) -> list[int]:
    if not rows:
        return []
    t0 = min(r[0] for r in rows)
    t1 = max(r[0] for r in rows)
    counts = [0] * (t1 - t0 + 1)
    for r in rows:
        if qty is None or r[2] == qty:
            counts[r[0] - t0] += 1
    return counts


def _detrend(counts: list[int], window: int = SMOOTH_WINDOW_S) -> list[float]:
    """Вычесть скользящее среднее (окно clamp-ится на краях ряда, без отражения)."""
    n = len(counts)
    if n == 0:
        return []
    prefix = [0.0] * (n + 1)
    for i, c in enumerate(counts):
        prefix[i + 1] = prefix[i] + c
    half = window // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(counts[i] - (prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def _dft_power(x: list[float], period: int) -> float | None:
    """Мощность прямого DFT на одном периоде (без numpy, см. докстринг модуля)."""
    n = len(x)
    if n < period * 2:
        return None
    w = 2 * math.pi / period
    re = sum(v * math.cos(w * i) for i, v in enumerate(x))
    im = sum(-v * math.sin(w * i) for i, v in enumerate(x))
    return (re * re + im * im) / n


def _spectrum_ratios(x: list[float]) -> dict[int, float]:
    """ratio(period) = мощность / медиана мощностей окна ±SPEC_WINDOW периодов
    (клампится в [SPEC_PERIOD_MIN, SPEC_PERIOD_MAX], без себя самого)."""
    powers = {}
    for period in range(SPEC_PERIOD_MIN, SPEC_PERIOD_MAX + 1):
        p = _dft_power(x, period)
        if p is not None:
            powers[period] = p
    ratios = {}
    for period, power in powers.items():
        neigh = [powers[t] for t in range(period - SPEC_WINDOW, period + SPEC_WINDOW + 1)
                 if t != period and t in powers]
        if not neigh:
            continue
        med = statistics.median(neigh)
        ratios[period] = power / max(med, _EPS)
    return ratios


def _spectrum_max_ratio(rows: list[list], qty: int | None) -> float | None:
    ratios = _spectrum_ratios(_detrend(_per_second_counts(rows, qty)))
    return max(ratios.values()) if ratios else None


def _spectrum_row(rows: list[list], days_rows: dict, qty_label, qty: int | None,
                   spec_ratio: float, draws: int, rng: random.Random) -> dict | None:
    ratios = _spectrum_ratios(_detrend(_per_second_counts(rows, qty)))
    if not ratios:
        return None
    max_ratio = max(ratios.values())
    top_periods = sorted(
        ({"period_s": period, "ratio": round(r, 3)}
         for period, r in ratios.items() if r >= spec_ratio),
        key=lambda p: -p["ratio"],
    )

    null_stats = [_spectrum_max_ratio(_shuffle_minute(rows, rng), qty) for _ in range(draws)]
    boot_stats = common.bootstrap_days(
        _boot_blocks(rows, days_rows),
        lambda blocks: _spectrum_max_ratio([r for blk in blocks for r in blk], qty),
        draws, rng,
    )

    return {
        "test": "spectrum", "qty": qty_label, "top_periods": top_periods,
        "max_ratio": round(max_ratio, 3),
        "null": common.pvalue_and_ci(max_ratio, null_stats, boot_stats),
    }


# ------------------------------------------------------------- analyze ----


def analyze(rows: list[list], peak_ratio: float = 2.0, spec_ratio: float = 4.0,
            draws: int = 200, seed: int = 0) -> dict:
    days_rows = common.by_day(rows)
    candidates = _candidate_sizes(rows)
    rng = random.Random(seed)

    rows_out = []
    for qty, _cnt in candidates:
        for side in (1, 0):
            row = _interval_row(rows, days_rows, qty, side, peak_ratio, draws, rng)
            if row is not None:
                rows_out.append(row)

    all_row = _spectrum_row(rows, days_rows, "all", None, spec_ratio, draws, rng)
    if all_row is not None:
        rows_out.append(all_row)
    for qty, _cnt in candidates:
        row = _spectrum_row(rows, days_rows, qty, qty, spec_ratio, draws, rng)
        if row is not None:
            rows_out.append(row)

    notes = [
        f"дней: {len(days_rows)}",
        "размеры-кандидаты (qty:частота): "
        + (", ".join(f"{q}:{c}" for q, c in candidates) if candidates else "нет"),
        "спектр 'all' инвариантен к контролю (перестановка секунд внутри минуты не "
        "меняет мультимножество секунд дня целиком) -- p для test=spectrum,qty=all не "
        "показателен, см. докстринг модуля",
    ]
    if len(days_rows) <= 1:
        notes.append("один день: бутстрап по половинам дня (до/после 16:00), не по дням")

    return {"rows": rows_out, "n_days": len(days_rows), "notes": notes}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "tape_key" (обязателен), "peak_ratio",
    "spec_ratio", "draws", "seed"}."""
    tape_key = arg["tape_key"]
    symbol_key = arg.get("symbol_key") or tape_key
    data = _fetch(tape_key)
    if not data.get("rows"):
        return {"id": "D1", "symbol": symbol_key, "window": [None, None], "error": "нет сделок в tape_key"}

    rows, prep_notes = _prepare(data)
    peak_ratio = float(arg.get("peak_ratio", 2.0))
    spec_ratio = float(arg.get("spec_ratio", 4.0))
    draws = int(arg.get("draws", 200))
    seed = int(arg.get("seed", 0))

    res = analyze(rows, peak_ratio, spec_ratio, draws, seed)
    first, second = _halves_rows(rows)
    halves = {
        "first": analyze(first, peak_ratio, spec_ratio, draws, seed)["rows"] if first else [],
        "second": analyze(second, peak_ratio, spec_ratio, draws, seed)["rows"] if second else [],
    }

    symbol = data.get("code") or symbol_key
    last_price = rows[-1][1]
    days = sorted({common.day_of(r[0]) for r in rows})
    window = [str(days[0]), str(days[-1])] if days else [None, None]

    return common.report("D1", symbol, window, res["rows"], prep_notes + res["notes"],
                          n_days=res["n_days"], halves=halves,
                          cost_pts=common.round_trip_cost_pts(symbol, last_price),
                          atr_min_pts=_atr_min_pts(rows))
