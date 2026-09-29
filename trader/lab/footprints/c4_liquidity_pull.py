"""C4: отвод ликвидности стакана перед импульсом.

Реестр: docs/algo-footprints-registry.md, строка C4. Гипотеза: маркетмейкеры и
информированные участники снимают заявки со стороны, куда пойдёт цена —
глубина L1-L5 этой стороны падает за десятки секунд ДО хода. Если так, падение
глубины предсказывает направление.

ДАННЫЕ / ВРЕМЯ / ГЭПЫ — как у c1_book_imbalance (полная выжимка стакана,
МСК-стенка как UTC, дыра > 60 с внутри дня рвёт окно вперёд). Общее
переиспользуется импортом из common.py и c1, не копируется:
`_load_full_book`, `_segment_ends`, `_halves_book`, `_percentile`,
`_atr_minute_book`.

ИМПУЛЬС ДЕТЕКТИТСЯ ВПЕРЁД. `move60(t) = mid(t+60) - mid(t)`, порог — квантиль
дня. Из-за этого «первый снимок серии» импульсных t (начало импульса, см.
`_impulse_events`) механически стоит РОВНО на 60 с (горизонт) РАНЬШЕ самого
хода: если цена делает чистый шаг в момент T, то move60(t) превышает порог
для всех t из [T-60, T), и первый (самый ранний) из них — T-60, а не T-N при
произвольном N<60. Это не баг, а свойство forward-looking статистики; retro
тест ниже проверяет глубину ИМЕННО в этой точке (T-60), а не «прямо перед
скачком» в бытовом смысле — так и должно быть, раз мы мерим глубину «в начале
импульса», а не «прямо перед импульсом».

Тест 2 (предсказательный) НЕ зависит от этого механизма: событие «отвод» —
собственное, по порогу глубины (`_pull_events`), и его момент определяется
только тем, когда сторона реально истончилась.
"""
from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common
from trader.lab.footprints.c1_book_imbalance import (
    _atr_minute_book, _halves_book, _load_full_book, _percentile, _segment_ends,
)

MEDIAN_WINDOW_S = 300  # скользящая медиана глубины — 5 минут
MIN_PRIOR = 5  # снимков в окне медианы, прежде чем её вообще считать
IMPULSE_HORIZON_S = 60  # ход mid(t+60)-mid(t), см. докстринг модуля
MERGE_WINDOW_S = 60  # слияние соседних импульсных t; cooldown для «отвода»

DEFAULT_IMPULSE_Q = 0.99
DEFAULT_PULL_FRAC = 0.4
DEFAULT_HORIZONS_S = (15, 60, 300)


# --------------------------------------------------------------------------
# Подготовка: mid/spread/глубина/медиана/отношение по дням
# --------------------------------------------------------------------------

def _hour_of(ts_ms: int) -> int:
    return common.minute_of_day(ts_ms / 1000) // 60


def _rolling_median(ts: list[int], vals: list[float], window_ms: int) -> list[float | None]:
    """M(t) = медиана vals по прошлым снимкам в [t-window, t) — сам t не
    входит; < MIN_PRIOR точек в окне -> None.

    # ponytail: bisect.insort/pop двигают список на каждый снимок — O(окно)
    # на точку. Для большого объёма на i9 заменить на heap с ленивым
    # удалением, если станет узким местом; тест гоняет часы, не месяцы.
    """
    out: list[float | None] = []
    window: list[float] = []
    lo = 0
    for i in range(len(ts)):
        while lo < i and ts[lo] < ts[i] - window_ms:
            window.pop(bisect.bisect_left(window, vals[lo]))
            lo += 1
        out.append(statistics.median(window) if len(window) >= MIN_PRIOR else None)
        bisect.insort(window, vals[i])
    return out


def _prep(rows: list[tuple]) -> dict:
    """rows -> {день: {ts, mid, spread, depth, median, ratio, block_end,
    hour_idx, hour_idx_all}}. depth/median/ratio — отдельно по bid и ask."""
    days: dict = defaultdict(list)
    for r in rows:
        days[common.day_of(r[0] / 1000)].append(r)
    window_ms = MEDIAN_WINDOW_S * 1000
    out = {}
    for d, drows in days.items():
        ts, mid, spread = [], [], []
        depth = {"bid": [], "ask": []}
        for ts_ms, bids, asks in drows:
            bid1, ask1 = bids[0][0], asks[0][0]
            ts.append(ts_ms)
            mid.append((bid1 + ask1) / 2)
            spread.append(ask1 - bid1)
            depth["bid"].append(sum(q for _, q in bids))
            depth["ask"].append(sum(q for _, q in asks))
        median = {s: _rolling_median(ts, depth[s], window_ms) for s in ("bid", "ask")}
        ratio = {s: [(depth[s][i] / median[s][i]) if median[s][i] else None
                     for i in range(len(ts))] for s in ("bid", "ask")}
        hour_idx: dict = defaultdict(lambda: {"bid": [], "ask": []})
        hour_idx_all = {"bid": [], "ask": []}
        for i, t in enumerate(ts):
            h = _hour_of(t)
            for s in ("bid", "ask"):
                if ratio[s][i] is not None:
                    hour_idx[h][s].append(i)
                    hour_idx_all[s].append(i)
        out[d] = {"ts": ts, "mid": mid, "spread": spread, "depth": depth,
                  "median": median, "ratio": ratio, "block_end": _segment_ends(ts),
                  "hour_idx": hour_idx, "hour_idx_all": hour_idx_all}
    return out


# --------------------------------------------------------------------------
# События: импульс (тест 1) и отвод глубины (тест 2)
# --------------------------------------------------------------------------

def _impulse_events(dd: dict, impulse_q: float) -> list[dict]:
    """{"i": индекс начала эпизода, "side": сторона удара ("ask" — ход
    вверх съедает аск)}. Соседние импульсные t (ближе MERGE_WINDOW_S)
    сливаются в одно событие с индексом первого t цепочки."""
    ts, mid, block_end = dd["ts"], dd["mid"], dd["block_end"]
    n = len(ts)
    h_ms = IMPULSE_HORIZON_S * 1000
    moves: list[float | None] = [None] * n
    for i in range(n):
        j = bisect.bisect_left(ts, ts[i] + h_ms, i)
        if j < n and j <= block_end[i]:
            moves[i] = mid[j] - mid[i]
    valid = sorted(abs(m) for m in moves if m is not None)
    if not valid:
        return []
    thr = _percentile(valid, impulse_q)
    events, last_i = [], None
    for i, m in enumerate(moves):
        if m is None or abs(m) < thr:
            continue
        if last_i is not None and ts[i] - ts[last_i] < MERGE_WINDOW_S * 1000:
            last_i = i
            continue
        events.append({"i": i, "side": "ask" if m > 0 else "bid"})
        last_i = i
    return events


def _pull_events(ts: list[int], ratio: list[float | None], pull_frac: float) -> list[int]:
    """Индексы «впервые за последние MERGE_WINDOW_S с» ratio[i] <= pull_frac
    (cooldown от последнего сработавшего события той же стороны)."""
    out, last_fired = [], None
    for i, r in enumerate(ratio):
        if r is None or r > pull_frac:
            continue
        if last_fired is not None and ts[i] - last_fired < MERGE_WINDOW_S * 1000:
            continue
        out.append(i)
        last_fired = ts[i]
    return out


def _other_side(side: str) -> str:
    return "bid" if side == "ask" else "ask"


# --------------------------------------------------------------------------
# Статистика
# --------------------------------------------------------------------------

def _one_sided_low(real: float | None, nulls: list, boots: list) -> dict:
    """p/ci для «стат аномально НИЗКИЙ». `common.pvalue_and_ci` меряет долю
    нулей >= настоящего (ждёт, что эффект — высокий real); здесь интересен
    низкий ratio (отвод глубины), поэтому считаем на отрицании и переводим
    результат обратно в единицы ratio, чтобы отчёт не путал знак."""
    def neg(x):
        return None if x is None else -x
    res = common.pvalue_and_ci(neg(real), [neg(x) for x in nulls], [neg(x) for x in boots])
    lo, hi = res["ci95"]
    ci = [neg(hi), neg(lo)] if lo is not None and hi is not None else [None, None]
    return {"stat": real, "p": res["p"], "ci95": ci, "n_null": res["n_null"]}


def _flat_median(blocks: list[list[float]]) -> float | None:
    vals = [v for blk in blocks for v in blk]
    return statistics.median(vals) if vals else None


def _retro_test(days_data: dict, events_by_day: dict, side_kind: str,
                draws: int, rng: random.Random) -> dict:
    """side_kind: "hit" — сторона удара, "opposite" — противоположная.
    Нуль: та же сторона в случайный момент ТОГО ЖЕ дня и часа дня."""
    real_vals, real_days, matched = [], [], []
    for d, events in events_by_day.items():
        dd = days_data[d]
        for ev in events:
            side = ev["side"] if side_kind == "hit" else _other_side(ev["side"])
            r = dd["ratio"][side][ev["i"]]
            if r is None:
                continue
            real_vals.append(r)
            real_days.append(d)
            matched.append((d, side, _hour_of(dd["ts"][ev["i"]])))
    if not real_vals:
        return {"test": "retro", "side": side_kind, "n_impulses": 0,
                "ratio_median": None, "null": _one_sided_low(None, [], [])}

    real_stat = statistics.median(real_vals)
    null_stats = []
    for _ in range(draws):
        draw_vals = []
        for d, side, h in matched:
            dd = days_data[d]
            pool = dd["hour_idx"][h][side] or dd["hour_idx_all"][side]
            if not pool:
                continue
            draw_vals.append(dd["ratio"][side][pool[rng.randrange(len(pool))]])
        null_stats.append(statistics.median(draw_vals) if draw_vals else None)

    rows_by_day: dict = defaultdict(list)
    for v, d in zip(real_vals, real_days):
        rows_by_day[d].append(v)
    boot_stats = common.bootstrap_days(rows_by_day, _flat_median, draws, rng)
    return {"test": "retro", "side": side_kind, "n_impulses": len(real_vals),
            "ratio_median": real_stat,
            "null": _one_sided_low(real_stat, null_stats, boot_stats)}


def _predict_test(days_data: dict, pull_events_by_day: dict, h: int, pull_frac: float,
                  draws: int, rng: random.Random) -> dict:
    """pull_events_by_day: день -> [(i, side), ...]. Исход — подписанный ход
    (+ для отвода аска) через h с. Нуль: перетасовка ratio внутри дня (mid не
    меняется), события «отвод» переоткрываются на перетасованном ряду."""
    h_ms = h * 1000
    real_moves, real_days, real_spreads = [], [], []
    for d, evs in pull_events_by_day.items():
        dd = days_data[d]
        ts, mid, spread, block_end = dd["ts"], dd["mid"], dd["spread"], dd["block_end"]
        n = len(ts)
        for i, side in evs:
            j = bisect.bisect_left(ts, ts[i] + h_ms, i)
            if j >= n or j > block_end[i]:
                continue
            sign = 1 if side == "ask" else -1
            real_moves.append(sign * (mid[j] - mid[i]))
            real_days.append(d)
            real_spreads.append(spread[i])
    n_events = len(real_moves)
    if n_events == 0:
        return {"test": "predict", "h": h, "n_events": 0, "median_signed_move": None,
                "pos_share": None, "null": common.pvalue_and_ci(None, [], []),
                "half_spread_median": None}

    real_stat = statistics.median(real_moves)
    pos_share = sum(1 for v in real_moves if v > 0) / n_events

    null_stats = []
    for _ in range(draws):
        draw_moves = []
        for d, dd in days_data.items():
            ts, mid, block_end = dd["ts"], dd["mid"], dd["block_end"]
            n = len(ts)
            days_label = [d] * n
            for side in ("bid", "ask"):
                shuf = common.shuffle_within_day(dd["ratio"][side], days_label, rng)
                sign = 1 if side == "ask" else -1
                for i in _pull_events(ts, shuf, pull_frac):
                    j = bisect.bisect_left(ts, ts[i] + h_ms, i)
                    if j >= n or j > block_end[i]:
                        continue
                    draw_moves.append(sign * (mid[j] - mid[i]))
        null_stats.append(statistics.median(draw_moves) if draw_moves else None)

    rows_by_day: dict = defaultdict(list)
    for v, d in zip(real_moves, real_days):
        rows_by_day[d].append(v)
    boot_stats = common.bootstrap_days(rows_by_day, _flat_median, draws, rng)
    return {"test": "predict", "h": h, "n_events": n_events,
            "median_signed_move": real_stat, "pos_share": pos_share,
            "null": common.pvalue_and_ci(real_stat, null_stats, boot_stats),
            "half_spread_median": statistics.median(real_spreads) / 2 if real_spreads else None}


def analyze(rows: list[tuple], *, impulse_q: float = DEFAULT_IMPULSE_Q,
           pull_frac: float = DEFAULT_PULL_FRAC, horizons_s=DEFAULT_HORIZONS_S,
           draws: int = 200, seed: int = 0) -> dict:
    """rows = [(ts_ms, bids, asks), ...] (см. c1._load_full_book) ->
    {"rows", "n_days", "notes"}. rows: 2 строки retro (hit/opposite) +
    по одной predict-строке на горизонт."""
    days_data = _prep(rows)
    events_by_day = {d: _impulse_events(dd, impulse_q) for d, dd in days_data.items()}
    pull_events_by_day = {
        d: sorted(
            ((i, s) for s in ("bid", "ask") for i in _pull_events(dd["ts"], dd["ratio"][s], pull_frac)),
            key=lambda x: dd["ts"][x[0]],
        )
        for d, dd in days_data.items()
    }

    out_rows = []
    rng_retro = random.Random(f"{seed}:retro")
    for side_kind in ("hit", "opposite"):
        out_rows.append(_retro_test(days_data, events_by_day, side_kind, draws, rng_retro))
    for h in horizons_s:
        rng_h = random.Random(f"{seed}:predict:{h}")
        out_rows.append(_predict_test(days_data, pull_events_by_day, int(h), pull_frac, draws, rng_h))

    return {"rows": out_rows, "n_days": len(days_data), "notes": []}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "book_key" (обязателен), "since",
    "until", "impulse_q", "pull_frac", "horizons_s", "draws", "seed"}."""
    book_key = arg.get("book_key")
    if not book_key:
        return {"id": "C4", "error": "book_key обязателен"}
    symbol_key = arg.get("symbol_key") or book_key
    since, until = arg.get("since"), arg.get("until")
    rows, dropped = _load_full_book(book_key, since, until)
    if not rows:
        return {"id": "C4", "symbol": symbol_key, "window": [since, until],
                "error": "нет снимков стакана в окне"}

    impulse_q = float(arg.get("impulse_q", DEFAULT_IMPULSE_Q))
    pull_frac = float(arg.get("pull_frac", DEFAULT_PULL_FRAC))
    horizons = tuple(int(x) for x in arg.get("horizons_s", DEFAULT_HORIZONS_S))
    draws = int(arg.get("draws", 200))
    seed = int(arg.get("seed", 0))

    res = analyze(rows, impulse_q=impulse_q, pull_frac=pull_frac, horizons_s=horizons,
                 draws=draws, seed=seed)
    first_rows, second_rows = _halves_book(rows)
    halves = {
        "first": analyze(first_rows, impulse_q=impulse_q, pull_frac=pull_frac, horizons_s=horizons,
                         draws=draws, seed=seed)["rows"] if len(first_rows) > 1 else [],
        "second": analyze(second_rows, impulse_q=impulse_q, pull_frac=pull_frac, horizons_s=horizons,
                          draws=draws, seed=seed)["rows"] if len(second_rows) > 1 else [],
    }
    notes = [f"снимков отброшено (нет обеих сторон / цена<=0): {dropped}",
             "halves — свой разрез _halves_book (common.halves ждёт секунды, здесь ts_ms)"]
    last_bid1, last_ask1 = rows[-1][1][0][0], rows[-1][2][0][0]
    last_mid = (last_bid1 + last_ask1) / 2
    return common.report("C4", symbol_key, [since, until], res["rows"], notes,
                         n_days=res["n_days"], halves=halves,
                         cost_pts=common.round_trip_cost_pts(symbol_key, last_mid),
                         atr_min_pts=_atr_minute_book(rows))
