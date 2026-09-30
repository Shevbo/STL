"""C6: охота за стопами — ложный пробой экстремума против закрепления.

След: фитиль бара t пробивает максимум/минимум последних N баров (внутри
дня), закрытие возвращается в диапазон, дальше ход в сторону ОТ экстремума
(sign=-1 для свипа вверх — ждём движения вниз, sign=+1 для свипа вниз).
Расширенный вариант (window=3) даёт закрытию 3 бара на возврат (t..t+2),
исход меряется со сдвигом w=2 (от t+2), чтобы не путать сам момент возврата
с горизонтом хода. По построению это НЕ дублирует базовый вариант (window=0)
для КОНТРОЛЯ-ПРОБОЯ: тот всегда решается по закрытию бара t и не зависит от
window — поэтому у window=3 сам возврат может случиться позже пробоя t
(строка t закрылась ЗА диапазоном, а t+1/t+2 вернулись) и формально
пересечься с группой «пробой»; это задокументированная особенность
расширенного варианта, а не ошибка (тест проверяет непересечение для
базового window=0, где оно гарантировано разбиением close[t] <=/> local_max).

Контроль 1 (пробой/закрепление): те же бары-кандидаты (пробой фитилём), где
закрытие t ушло ЗА диапазон — сравнение «вернулся» против «закрепился» тем
же знаком и тем же w, чтобы горизонты были сопоставимы.

Контроль 2 (нуль): случайные бары того же дня, того же количества, что и
реальные события в этот день; знак — ставка на разворот тренда за N баров
БЕЗ факта свипа (sign = -sign(close[t]-close[t-N])). draws розыгрышей,
бутстрап по дням даёт CI реальной медианы.

Протокол: docs/algo-footprints-registry.md, каркас — common.py, образец —
a4_bar_boundary.py.

Сессия: бары вне 07:00-23:50 (common.session_rows) выброшены до разбивки по
dням, счётчик «до сессии»/«после сессии» в notes.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common


def _local_extrema(day_rows: list[list], i: int, n: int) -> tuple[float, float]:
    highs = [day_rows[j][2] for j in range(i - n, i)]
    lows = [day_rows[j][3] for j in range(i - n, i)]
    return max(highs), min(lows)


def classify_bars(day_rows: list[list], n: int, thresh: float) -> dict:
    """Один день (бары по возрастанию времени), окно n внутри дня.

    -> {"sweep_up", "sweep_dn", "breakout_up", "breakout_dn"}: [(idx, extreme), ...].
    Свип и пробой одной стороны взаимно исключены разбиением close[t] <=/> extreme.
    """
    out: dict = {"sweep_up": [], "sweep_dn": [], "breakout_up": [], "breakout_dn": []}
    for i in range(n, len(day_rows)):
        hi, lo, close_i = day_rows[i][2], day_rows[i][3], day_rows[i][4]
        local_max, local_min = _local_extrema(day_rows, i, n)
        if hi > local_max + thresh:
            key = "sweep_up" if close_i <= local_max else "breakout_up"
            out[key].append((i, local_max))
        if lo < local_min - thresh:
            key = "sweep_dn" if close_i >= local_min else "breakout_dn"
            out[key].append((i, local_min))
    return out


def classify_bars_window3(day_rows: list[list], n: int, thresh: float) -> dict:
    """Расширенный свип: пробой фитилём на t, возврат закрытия за t..t+2."""
    out: dict = {"sweep_up": [], "sweep_dn": []}
    for i in range(n, len(day_rows) - 2):
        hi, lo = day_rows[i][2], day_rows[i][3]
        local_max, local_min = _local_extrema(day_rows, i, n)
        if hi > local_max + thresh and any(day_rows[j][4] <= local_max for j in (i, i + 1, i + 2)):
            out["sweep_up"].append((i, local_max))
        if lo < local_min - thresh and any(day_rows[j][4] >= local_min for j in (i, i + 1, i + 2)):
            out["sweep_dn"].append((i, local_min))
    return out


def _outcome(bars: list[list], i: int, w: int, h: int, sign: int) -> float | None:
    j0, j1 = i + w, i + w + h
    if j1 >= len(bars):
        return None
    return sign * (bars[j1][4] - bars[j0][4])


def _median_of_blocks(blocks: list[list[float]]) -> float | None:
    flat = [x for blk in blocks for x in blk]
    return statistics.median(flat) if flat else None


def _stat_row(day_bars: dict, day_vol_med: dict, n: int, delta: float, window: int, w: int,
              h: int, sweep_events: list[tuple], breakout_events: list[tuple],
              draws: int, rng: random.Random) -> dict:
    outcomes, vols, by_day = [], [], defaultdict(list)
    for day, i, sign in sweep_events:
        bars = day_bars[day]
        v = _outcome(bars, i, w, h, sign)
        if v is None:
            continue
        outcomes.append(v)
        by_day[day].append(v)
        vm = day_vol_med[day]
        if vm:
            vols.append(bars[i][5] / vm)

    bo_outcomes = []
    for day, i, sign in breakout_events:
        v = _outcome(day_bars[day], i, w, h, sign)
        if v is not None:
            bo_outcomes.append(v)

    real_stat = statistics.median(outcomes) if outcomes else None
    if by_day:
        boot = common.bootstrap_days(by_day, _median_of_blocks, draws, rng)
        null_stats = []
        for _ in range(draws):
            null_vals = []
            for day, count in ((d, len(v)) for d, v in by_day.items()):
                bars = day_bars[day]
                valid = [i for i in range(n, len(bars)) if i + w + h < len(bars)]
                if not valid:
                    continue
                picks = (rng.sample(valid, count) if count <= len(valid)
                         else rng.choices(valid, k=count))
                for i in picks:
                    close_i, close_ref = bars[i][4], bars[i - n][4]
                    sign = -1 if close_i > close_ref else (1 if close_i < close_ref else 0)
                    v = _outcome(bars, i, w, h, sign) if sign else 0.0
                    if v is not None:
                        null_vals.append(v)
            if null_vals:
                null_stats.append(statistics.median(null_vals))
    else:
        boot, null_stats = [], []

    return {
        "N": n, "delta": delta, "window": window, "h": h,
        "n": len(outcomes),
        "median": real_stat,
        "mean": statistics.mean(outcomes) if outcomes else None,
        "pos_share": (sum(1 for v in outcomes if v > 0) / len(outcomes)) if outcomes else None,
        "vol_ratio": statistics.median(vols) if vols else None,
        "breakout": {"n": len(bo_outcomes),
                     "median": statistics.median(bo_outcomes) if bo_outcomes else None},
        "null": common.pvalue_and_ci(real_stat, null_stats, boot),
    }


def _rows_for(rows: list[list], lookbacks, deltas, horizons, draws: int,
              seed: int) -> list[dict]:
    day_bars = {d: sorted(v, key=lambda r: r[0]) for d, v in common.by_day(rows).items()}
    if not day_bars:
        return []
    day_vol_med = {d: statistics.median(r[5] for r in bars) for d, bars in day_bars.items()}
    atr = common.atr_minute(rows) or 0.0
    rng = random.Random(seed)
    out = []
    for n in lookbacks:
        for delta in deltas:
            thresh = delta * atr
            classify0 = {d: classify_bars(bars, n, thresh) for d, bars in day_bars.items()}
            classify3 = {d: classify_bars_window3(bars, n, thresh) for d, bars in day_bars.items()}
            breakout_events = (
                [(d, i, -1) for d, c in classify0.items() for i, _ in c["breakout_up"]]
                + [(d, i, 1) for d, c in classify0.items() for i, _ in c["breakout_dn"]]
            )
            for window, classify in ((0, classify0), (3, classify3)):
                w = 0 if window == 0 else 2
                sweep_events = (
                    [(d, i, -1) for d, c in classify.items() for i, _ in c["sweep_up"]]
                    + [(d, i, 1) for d, c in classify.items() for i, _ in c["sweep_dn"]]
                )
                for h in horizons:
                    out.append(_stat_row(day_bars, day_vol_med, n, delta, window, w, h,
                                          sweep_events, breakout_events, draws, rng))
    return out


def analyze(rows: list[list], lookbacks=(30, 60, 120), deltas=(0.0, 0.5),
            horizons=(5, 15, 30, 60), draws: int = 200, seed: int = 0) -> dict:
    rows, sess_notes = common.session_rows(rows)
    n_days = len(common.by_day(rows))
    first, second = common.halves(rows)
    notes = sess_notes + [
        "sign: свип вверх ждёт хода вниз (-1), свип вниз — вверх (+1); "
        "пробой (закрепление) той же стороны меряется тем же знаком",
        "window=3: возврат закрытия за t..t+2 может формально пересекаться с "
        "пробоем t (закрытие t за диапазоном, t+1/t+2 вернулись) — не ошибка",
    ]
    if common.atr_minute(rows) is None:
        notes.append("ATR минуты недоступен (мало баров), delta*ATR = 0")
    return {
        "rows": _rows_for(rows, lookbacks, deltas, horizons, draws, seed),
        "halves": {
            "first": _rows_for(first, lookbacks, deltas, horizons, draws, seed) if first else [],
            "second": _rows_for(second, lookbacks, deltas, horizons, draws, seed) if second else [],
        },
        "n_days": n_days,
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "lookbacks", "deltas",
    "horizons", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "C6", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows,
                  lookbacks=tuple(arg.get("lookbacks", (30, 60, 120))),
                  deltas=tuple(arg.get("deltas", (0.0, 0.5))),
                  horizons=tuple(arg.get("horizons", (5, 15, 30, 60))),
                  draws=int(arg.get("draws", 200)),
                  seed=int(arg.get("seed", 0)))
    price = statistics.median(r[4] for r in rows)
    return common.report("C6", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
