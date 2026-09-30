"""A2: пробой канала (Donchian) N минут — крупные трендовые алгоритмы.

След: на первом баре, закрывшемся выше максимума (ниже минимума) предыдущих N
баров ЭТОГО ЖЕ торгового дня, объём выше обычного и ход продолжается в
сторону пробоя ещё h минут.

Статистика на (N, h): подписанный ход sign * (close[t+h] - close[t]) в
пунктах по всем событиям пробоя за N; n, медиана, среднее, доля
положительных, объёмное отношение (volume[t] / медиана объёма дня).

Нуль (контроль 1, "импульс без пробоя"): столько же случайных баров того же
дня, знак = знак close[t] - close[t-N] (тот же тренд, но без факта пробоя).
Розыгрыши по дням, p и 95% ДИ через common.pvalue_and_ci; интервал — бутстрап
РЕАЛЬНОЙ статистики по дням common.bootstrap_days.

Контроль 2 ("почти пробой"): тот же уровень, сдвинутый внутрь канала на 1
минутный ATR (common.atr_minute); исход рядом с настоящим, без отдельного
p-value (описательно).

Ограничения: окно N не пересекает ночной перерыв — бары берутся только из
того же дня (common.by_day разрезает по календарной дате МСК-стенки); бары
вне сессии 07:00-23:50 (common.session_rows) выброшены со счётчиком в notes. Событие не считается, если баров
ДО t в дне меньше N (недостаточная история) или бар t+h выходит за пределы
дня (недостаточный форвард) — число отброшенных по второй причине идёт в
notes, не выбрасывается молча.
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common


def _side(blk: list[list], i: int, n: int) -> int:
    """+1 пробой вверх, -1 пробой вниз, 0 нет пробоя (или не хватает истории)."""
    if i < n:
        return 0
    hi = max(r[2] for r in blk[i - n:i])
    lo = min(r[3] for r in blk[i - n:i])
    c = blk[i][4]
    if c > hi:
        return 1
    if c < lo:
        return -1
    return 0


def _breakout_events(blk: list[list], n: int) -> list[tuple[int, int]]:
    """Первые бары пробоя: (индекс, знак), без повтора на баре, где пробой уже шёл."""
    side = [_side(blk, i, n) for i in range(len(blk))]
    return [(i, s) for i, s in enumerate(side) if s and (i == 0 or side[i - 1] != s)]


def _near_events(blk: list[list], n: int, k: float) -> list[tuple[int, int]]:
    """Псевдопробои: close в полосе [уровень-k, уровень] — рядом, но не пробой."""
    if k <= 0 or n <= 0:
        return []

    def near(i: int) -> int:
        if i < n:
            return 0
        hi = max(r[2] for r in blk[i - n:i])
        lo = min(r[3] for r in blk[i - n:i])
        c = blk[i][4]
        if hi - k <= c <= hi:
            return 1
        if lo <= c <= lo + k:
            return -1
        return 0

    side = [near(i) for i in range(len(blk))]
    return [(i, s) for i, s in enumerate(side) if s and (i == 0 or side[i - 1] != s)]


def _control1_null(days: dict, n: int, h: int, event_counts: dict,
                    draws: int, rng: random.Random) -> list[float | None]:
    """Контроль 1: случайные бары дня в том же числе, знак = тренд без пробоя."""
    candidates = {d: list(range(n, len(blk))) for d, blk in days.items()}
    nulls: list[float | None] = []
    for _ in range(draws):
        outs = []
        for d, blk in days.items():
            k = event_counts.get(d, 0)
            cand = candidates[d]
            if not k or not cand:
                continue
            for i in rng.choices(cand, k=k):
                if i + h >= len(blk):
                    continue
                delta = blk[i][4] - blk[i - n][4]
                if not delta:
                    continue
                outs.append((1 if delta > 0 else -1) * (blk[i + h][4] - blk[i][4]))
        nulls.append(statistics.median(outs) if outs else None)
    return nulls


def _stat_row(days: dict, real_by_day: dict, near_by_day: dict, day_med_vol: dict,
              n: int, h: int, draws: int, rng: random.Random, drop_notes: list[str]) -> dict:
    outcomes_by_day, vol_ratios, dropped, total = {}, [], 0, 0
    for d, blk in days.items():
        evs = real_by_day[d]
        total += len(evs)
        outs = []
        for i, sign in evs:
            if i + h >= len(blk):
                dropped += 1
                continue
            outs.append(sign * (blk[i + h][4] - blk[i][4]))
            mv = day_med_vol.get(d)
            if mv:
                vol_ratios.append(blk[i][5] / mv)
        outcomes_by_day[d] = outs
    flat = [x for v in outcomes_by_day.values() for x in v]
    n_ev = len(flat)
    median = statistics.median(flat) if flat else None

    null_stats = _control1_null(days, n, h, {d: len(e) for d, e in real_by_day.items()},
                                 draws, rng)
    boot_stats = common.bootstrap_days(
        outcomes_by_day,
        lambda blocks: statistics.median([x for b in blocks for x in b])
        if any(blocks) else None,
        draws, rng)

    near_outs = [sign * (blk[i + h][4] - blk[i][4])
                 for d, blk in days.items()
                 for i, sign in near_by_day.get(d, [])
                 if i + h < len(blk)]

    if dropped:
        drop_notes.append(f"N={n} h={h}: горизонт за пределы дня, отброшено {dropped} из {total}")

    return {
        "N": n, "h": h, "n": n_ev, "median": median,
        "mean": statistics.fmean(flat) if flat else None,
        "pos_share": (sum(1 for x in flat if x > 0) / n_ev) if n_ev else None,
        "vol_ratio": statistics.median(vol_ratios) if vol_ratios else None,
        "null": common.pvalue_and_ci(median, null_stats, boot_stats),
        "near": {"n": len(near_outs),
                  "median": statistics.median(near_outs) if near_outs else None},
    }


def _rows_for(days: dict, lookbacks, horizons, draws: int, seed: int) -> tuple[list[dict], list[str]]:
    flat = sorted((r for blk in days.values() for r in blk), key=lambda r: r[0])
    atr_k = common.atr_minute(flat) or 0.0
    day_med_vol = {d: (statistics.median([r[5] for r in blk]) if blk else None)
                   for d, blk in days.items()}
    notes: list[str] = []
    rows = []
    for n in lookbacks:
        n = int(n)
        real_by_day = {d: _breakout_events(blk, n) for d, blk in days.items()}
        near_by_day = {d: _near_events(blk, n, atr_k) for d, blk in days.items()}
        rng = random.Random(seed * 1_000_003 + n)
        for h in horizons:
            rows.append(_stat_row(days, real_by_day, near_by_day, day_med_vol,
                                   n, int(h), draws, rng, notes))
    return rows, notes


def analyze(rows: list[list], lookbacks=(20, 60, 120, 240), horizons=(5, 15, 30, 60),
            draws: int = 200, seed: int = 0) -> dict:
    rows, sess_notes = common.session_rows(rows)
    days = common.by_day(rows)
    real_rows, notes = _rows_for(days, lookbacks, horizons, draws, seed)
    notes = sess_notes + notes + [
        "отрицательная медиана = откат после нового экстремума; сравнивать с near "
        "(псевдопробой): если откат тот же, это не пробой, а возврат после экстремума",
    ]
    first, second = common.halves(rows)
    first_rows, _ = _rows_for(common.by_day(first), lookbacks, horizons, draws, seed) \
        if first else ([], [])
    second_rows, _ = _rows_for(common.by_day(second), lookbacks, horizons, draws, seed) \
        if second else ([], [])
    return {
        "rows": real_rows,
        "halves": {"first": first_rows, "second": second_rows},
        "n_days": len(days),
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "lookbacks", "horizons", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "A2", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows,
                  tuple(arg.get("lookbacks", (20, 60, 120, 240))),
                  tuple(arg.get("horizons", (5, 15, 30, 60))),
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)))
    price = statistics.median(r[4] for r in rows)
    return common.report("A2", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
