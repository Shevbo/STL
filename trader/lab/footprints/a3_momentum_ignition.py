"""A3: momentum ignition (разгон) — серия агрессивных сделок толкает цену,
втягивает трендовиков, затем откатывает.

Гипотеза и определения: docs/algo-footprints-registry.md, строка A3. Данные —
бары (лента для этой гипотезы в потоке `~/market-archive` пока не считается,
см. реестр).

Событие «разгон» на баре t дня: объём[t], нормированный на медиану объёма ДНЯ
(`volume[t]/day_median`), в верхнем `vol_q` квантиле дня, И `|close[t]-open[t]|
>= move_atr * ATR_min` (ATR_min — `common.atr_minute` по всей выборке вызова,
т.е. пересчитывается для целого окна и отдельно для каждой половины, как в
a2/c6). Знак события = знак хода бара. Соседние события одного дня ближе 5
минут сливаются — остаётся первое (сканирование по возрастанию времени,
расстояние меряется от последнего ОСТАВЛЕННОГО события).

Исходы, подписанные знаком разгона:
  - продолжение: sign * (close[t+h] - close[t]), h из `cont_horizons`;
  - откат: sign * (close[t+H] - close[t+5]), H из `rev_horizons` (H заведомо
    >= 5 в реестре; при H<5 запись просто не наберёт данных).
Оба считаются внутри дня — событие у конца дня без нужного горизонта
отбрасывается, счётчик идёт в notes (не выбрасывается молча).

Контроль 1 («объём без хода»): бары с тем же объёмным квантилем, но
`|close-open| < 0.5*ATR_min` (зона между 0.5 и move_atr множителями ATR
осознанно не участвует ни в событии, ни в контроле — она не разгон и не
«без хода»). У такого бара свой ход близок к нулю, поэтому знак берётся от
последнего НЕНУЛЕВОГО хода, начиная с самого бара и далее назад по дню
(`_sign_or_prior`) — иначе сравнивать было бы не с чем. control_median —
медиана тех же cont/rev формул от этих баров, для сравнения с колонкой n/median.

Нуль (контроль 2): `draws` розыгрышей случайных баров того же дня, в том же
количестве, что реальных событий в этот день; знак — знак хода САМОГО
случайного бара (условие «любой бар с ходом», без требования по объёму).
Бутстрап реальной медианы по дням — `common.bootstrap_days`, как в a2/c6.

Продолжение тестируется как есть (`common.pvalue_and_ci`, «нуль >=
настоящего» — ищем ХОД БОЛЬШЕ обычного). Откат ожидается ОТРИЦАТЕЛЬНЫМ,
поэтому `_pvalue_lower` кладёт в p нижний хвост (`p_low` из common).
Двусторонний `p_two` есть в каждом null-поле.

cost_pts в отчёте — по ПОСЛЕДНЕМУ close окна (`round_trip_cost_pts`), не по
медиане: разгон датируется последней ценой, а не средней за окно (так задано
протоколом строки A3, в отличие от a2/c6).

Сессия: бары вне 07:00-23:50 (common.session_rows) выброшены до разбивки по
dням, счётчик «до сессии»/«после сессии» в notes.
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common

_MERGE_GAP_S = 5 * 60


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[idx]


def _sign_or_prior(day_rows: list[list], i: int) -> int:
    """Знак последнего ненулевого хода, начиная с бара i и далее назад по дню."""
    for j in range(i, -1, -1):
        d = day_rows[j][4] - day_rows[j][1]
        if d:
            return 1 if d > 0 else -1
    return 0


def _events_for_day(day_rows: list[list], vol_q: float, move_atr: float,
                     atr_val: float) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """-> (события разгона [(i,sign)] слитые, контроль «объём без хода» [(i,sign)])."""
    vols = [r[5] for r in day_rows]
    med = statistics.median(vols) if vols else 0.0
    if not med:
        return [], []
    thr = _quantile(sorted(v / med for v in vols), vol_q)
    if thr is None:
        return [], []
    raw_ign, ctrl = [], []
    for i, r in enumerate(day_rows):
        if r[5] / med < thr:
            continue
        move = r[4] - r[1]
        if abs(move) >= move_atr * atr_val:
            raw_ign.append((i, 1 if move > 0 else -1))
        elif abs(move) < 0.5 * atr_val:
            sign = _sign_or_prior(day_rows, i)
            if sign:
                ctrl.append((i, sign))
    merged: list[tuple[int, int]] = []
    last_ts = None
    for i, sign in raw_ign:
        ts = day_rows[i][0]
        if last_ts is not None and ts - last_ts < _MERGE_GAP_S:
            continue
        merged.append((i, sign))
        last_ts = ts
    return merged, ctrl


def _cont_outcome(bars: list[list], i: int, h: int, sign: int) -> float | None:
    j = i + h
    if j >= len(bars):
        return None
    return sign * (bars[j][4] - bars[i][4])


def _rev_outcome(bars: list[list], i: int, big_h: int, sign: int) -> float | None:
    j5, jh = i + 5, i + big_h
    if j5 >= len(bars) or jh >= len(bars):
        return None
    return sign * (bars[jh][4] - bars[j5][4])


def _null_draws(days: dict, real_counts: dict, horizon: int, kind: str,
                 draws: int, rng: random.Random) -> list[float | None]:
    """Случайные бары дня в том же числе, что реальных событий; знак — знак
    хода САМОГО бара (любой бар с ходом, без объёмного условия)."""
    fn = _cont_outcome if kind == "cont" else _rev_outcome
    candidates = {d: [i for i, r in enumerate(blk) if r[4] != r[1]] for d, blk in days.items()}
    nulls: list[float | None] = []
    for _ in range(draws):
        outs = []
        for d, blk in days.items():
            k = real_counts.get(d, 0)
            cand = candidates.get(d) or []
            if not k or not cand:
                continue
            for i in rng.choices(cand, k=k):
                sign = 1 if blk[i][4] > blk[i][1] else -1
                v = fn(blk, i, horizon, sign)
                if v is not None:
                    outs.append(v)
        nulls.append(statistics.median(outs) if outs else None)
    return nulls


def _pvalue_lower(real: float | None, nulls: list[float | None],
                   boots: list[float | None]) -> dict:
    """Откат ожидается отрицательным: p = нижний хвост common.pvalue_and_ci."""
    res = common.pvalue_and_ci(real, nulls, boots)
    res["p"] = res["p_low"]
    return res


def _stat_row(days: dict, event_by_day: dict, control_by_day: dict, kind: str,
              horizon: int, draws: int, rng: random.Random, notes: list[str]) -> dict:
    fn = _cont_outcome if kind == "cont" else _rev_outcome
    outcomes_by_day, dropped, total = {}, 0, 0
    for d, blk in days.items():
        evs = event_by_day[d]
        total += len(evs)
        outs = []
        for i, sign in evs:
            v = fn(blk, i, horizon, sign)
            if v is None:
                dropped += 1
                continue
            outs.append(v)
        outcomes_by_day[d] = outs
    flat = [x for v in outcomes_by_day.values() for x in v]
    n_ev = len(flat)
    real_stat = statistics.median(flat) if flat else None

    ctrl_outs = []
    for d, blk in days.items():
        for i, sign in control_by_day.get(d, []):
            v = fn(blk, i, horizon, sign)
            if v is not None:
                ctrl_outs.append(v)

    null_stats = _null_draws(days, {d: len(e) for d, e in event_by_day.items()},
                              horizon, kind, draws, rng)
    boot_stats = common.bootstrap_days(
        outcomes_by_day,
        lambda blocks: statistics.median([x for b in blocks for x in b]) if any(blocks) else None,
        draws, rng)
    verdict = (_pvalue_lower(real_stat, null_stats, boot_stats) if kind == "rev"
               else common.pvalue_and_ci(real_stat, null_stats, boot_stats))

    if dropped:
        notes.append(f"{kind} h={horizon}: горизонт/база за пределы дня, отброшено {dropped} из {total}")

    return {
        "test": kind, "horizon": horizon, "n": n_ev,
        "median": real_stat,
        "mean": statistics.fmean(flat) if flat else None,
        "pos_share": (sum(1 for x in flat if x > 0) / n_ev) if n_ev else None,
        "control_median": statistics.median(ctrl_outs) if ctrl_outs else None,
        "null": verdict,
    }


def _rows_for(days_: dict, vol_q: float, move_atr: float, cont_horizons, rev_horizons,
              draws: int, seed: int) -> tuple[list[dict], list[str]]:
    day_blocks = {d: sorted(v, key=lambda r: r[0]) for d, v in days_.items()}
    if not day_blocks:
        return [], []
    flat = sorted((r for blk in day_blocks.values() for r in blk), key=lambda r: r[0])
    atr_val = common.atr_minute(flat)
    notes: list[str] = []
    if not atr_val:
        notes.append("ATR минуты недоступен (мало баров), событий не найдено")
        return [], notes
    event_by_day, control_by_day = {}, {}
    for d, blk in day_blocks.items():
        ev, ctrl = _events_for_day(blk, vol_q, move_atr, atr_val)
        event_by_day[d], control_by_day[d] = ev, ctrl
    rng = random.Random(seed)
    rows = []
    for h in cont_horizons:
        rows.append(_stat_row(day_blocks, event_by_day, control_by_day, "cont", int(h),
                               draws, rng, notes))
    for big_h in rev_horizons:
        rows.append(_stat_row(day_blocks, event_by_day, control_by_day, "rev", int(big_h),
                               draws, rng, notes))
    return rows, notes


def analyze(rows: list[list], vol_q: float = 0.99, move_atr: float = 2.0,
            cont_horizons=(1, 3, 5), rev_horizons=(15, 30, 60),
            draws: int = 200, seed: int = 0) -> dict:
    rows, sess_notes = common.session_rows(rows)
    days = common.by_day(rows)
    real_rows, notes = _rows_for(days, vol_q, move_atr, cont_horizons, rev_horizons, draws, seed)
    notes = sess_notes + notes
    first, second = common.halves(rows)
    first_rows, _ = _rows_for(common.by_day(first), vol_q, move_atr, cont_horizons,
                               rev_horizons, draws, seed) if first else ([], [])
    second_rows, _ = _rows_for(common.by_day(second), vol_q, move_atr, cont_horizons,
                                rev_horizons, draws, seed) if second else ([], [])
    return {
        "rows": real_rows,
        "halves": {"first": first_rows, "second": second_rows},
        "n_days": len(days),
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "vol_q", "move_atr",
    "cont_horizons", "rev_horizons", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "A3", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows,
                  vol_q=float(arg.get("vol_q", 0.99)),
                  move_atr=float(arg.get("move_atr", 2.0)),
                  cont_horizons=tuple(arg.get("cont_horizons", (1, 3, 5))),
                  rev_horizons=tuple(arg.get("rev_horizons", (15, 30, 60))),
                  draws=int(arg.get("draws", 200)),
                  seed=int(arg.get("seed", 0)))
    price = rows[-1][4]
    return common.report("A3", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
