"""E1: ведущий инструмент (Si/BR -> RI, RI -> MX и т.п.).

След: кросс-корреляция минутных приращений цены двух инструментов с лагом
+1..+5 мин (лидер ведёт последователя) и предсказуемость ЗНАКА хода
последователя, когда ход лидера крупный (верхний дециль дня).

Ряды выравниваются по ts (внутреннее пересечение меток обоих инструментов;
сколько баров потеряно с каждой стороны — в notes). Приращение r = close[t] -
close[t-1] в пунктах считается ТОЛЬКО внутри дня (день = day_of выровненной
метки): первый бар дня приращения не имеет, скачок через ночь никогда не
попадает в ряд (см. common.py: перетасовка ряда без сохранения дня искажает
контроль).

Нуль для каждого лага — перестановка ЦЕЛЫХ дней ряда ЛИДЕРА (common.permute_days),
последователь неизменен: день лидера уезжает под чужой день последователя,
пары строятся по позиции внутри дня с учётом лага (несовпадение длин дней
после перестановки просто уменьшает число пар этого дня, не роняет расчёт).
Интервал — бутстрап по дням (common.bootstrap_days) на НЕПЕРЕСТАВЛЕННЫХ данных.

Верхний дециль |r_lead| считается ПО ДНЮ (не по всему окну): у каждого дня
свой порог, так волатильность разных периодов не мешает друг другу.
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _corr(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs, ys = zip(*pairs)
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def _decile_threshold(values: list[float]) -> float:
    s = sorted(abs(v) for v in values)
    return s[min(len(s) - 1, max(0, int(0.9 * len(s))))]


def _align(lead_rows: list[list], follow_rows: list[list]) -> tuple[list[tuple], list[str]]:
    """(ts, close_lead, close_follow) по внутреннему пересечению меток, notes о потерях."""
    lead_close = {r[0]: r[4] for r in lead_rows}
    follow_close = {r[0]: r[4] for r in follow_rows}
    common_ts = sorted(set(lead_close) & set(follow_close))
    aligned = [(ts, lead_close[ts], follow_close[ts]) for ts in common_ts]
    notes = [f"выравнивание по ts: отброшено {len(lead_rows) - len(common_ts)} баров лидера, "
             f"{len(follow_rows) - len(common_ts)} баров последователя, осталось {len(common_ts)}"]
    return aligned, notes


def _increments_by_day(aligned: list[tuple]) -> tuple[dict, dict]:
    """Приращения close по дню выровненной метки; первый бар дня пропущен (см. докстринг)."""
    lead_by_day, follow_by_day = {}, {}
    for d, group in common.by_day(aligned).items():
        lead_by_day[d] = [group[i][1] - group[i - 1][1] for i in range(1, len(group))]
        follow_by_day[d] = [group[i][2] - group[i - 1][2] for i in range(1, len(group))]
    return lead_by_day, follow_by_day


def _pairs_from_blocks(blocks: list[tuple[list, list]], lag: int) -> list[tuple[float, float]]:
    pairs = []
    for lead, follow in blocks:
        for j, rl in enumerate(lead):
            idx = j + lag
            if 0 <= idx < len(follow):
                pairs.append((rl, follow[idx]))
    return pairs


def _corr_from_blocks(blocks: list[tuple[list, list]], lag: int) -> float | None:
    return _corr(_pairs_from_blocks(blocks, lag))


def _decile_from_blocks(blocks: list[tuple[list, list]], lag: int) -> tuple[float | None, float | None, int]:
    """Пары (r_lead, r_follow[t+lag]) при |r_lead| в верхнем дециле СВОЕГО дня."""
    pairs = []
    for lead, follow in blocks:
        if not lead:
            continue
        thresh = _decile_threshold(lead)
        for j, rl in enumerate(lead):
            if abs(rl) < thresh:
                continue
            idx = j + lag
            if 0 <= idx < len(follow):
                pairs.append((rl, follow[idx]))
    if not pairs:
        return None, None, 0
    agree = sum(1 for rl, rf in pairs if _sign(rl) == _sign(rf)) / len(pairs)
    moves = [_sign(rl) * rf for rl, rf in pairs]
    return agree, statistics.median(moves), len(pairs)


def _lag_rows(lead_by_day: dict, follow_by_day: dict, lags, draws: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    combined = {d: (lead_by_day[d], follow_by_day.get(d, [])) for d in lead_by_day}
    real_blocks = list(combined.values())
    rows = []
    for lag in lags:
        lag = int(lag)
        real_pairs = _pairs_from_blocks(real_blocks, lag)
        real_corr = _corr(real_pairs)
        null_corr = []
        for _ in range(draws):
            perm_lead = common.permute_days(lead_by_day, rng)
            perm_blocks = [(perm_lead[d], follow_by_day.get(d, [])) for d in perm_lead]
            null_corr.append(_corr_from_blocks(perm_blocks, lag))
        boot_corr = common.bootstrap_days(
            combined, lambda blocks, lag=lag: _corr_from_blocks(blocks, lag), draws, rng)
        row = {"lag": lag, "n": len(real_pairs), "corr": real_corr,
               "null": common.pvalue_and_ci(real_corr, null_corr, boot_corr)}
        if 1 <= lag <= 3:
            agree, move, _n = _decile_from_blocks(real_blocks, lag)
            null_move = []
            for _ in range(draws):
                perm_lead = common.permute_days(lead_by_day, rng)
                perm_blocks = [(perm_lead[d], follow_by_day.get(d, [])) for d in perm_lead]
                null_move.append(_decile_from_blocks(perm_blocks, lag)[1])
            boot_move = common.bootstrap_days(
                combined, lambda blocks, lag=lag: _decile_from_blocks(blocks, lag)[1], draws, rng)
            row["sign_agree_top_decile"] = agree
            row["median_signed_move_pts"] = move
            row["null_move"] = common.pvalue_and_ci(move, null_move, boot_move)
        rows.append(row)
    return rows


def analyze(lead_rows: list[list], follow_rows: list[list],
            lags=tuple(range(-5, 6)), draws: int = 200, seed: int = 0) -> dict:
    aligned, notes = _align(lead_rows, follow_rows)
    lead_by_day, follow_by_day = _increments_by_day(aligned)
    rows = _lag_rows(lead_by_day, follow_by_day, lags, draws, seed)
    first_rows, second_rows = common.halves([[ts] for ts, _, _ in aligned])
    first_days = {common.day_of(r[0]) for r in first_rows}
    second_days = {common.day_of(r[0]) for r in second_rows}
    half_first = _lag_rows({d: v for d, v in lead_by_day.items() if d in first_days},
                           {d: v for d, v in follow_by_day.items() if d in first_days}, lags, draws, seed)
    half_second = _lag_rows({d: v for d, v in lead_by_day.items() if d in second_days},
                            {d: v for d, v in follow_by_day.items() if d in second_days}, lags, draws, seed)
    return {"rows": rows, "halves": {"first": half_first, "second": half_second},
            "n_days": len(lead_by_day), "notes": notes}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"leader_key", "follower_key", "since", "until", "lags", "draws", "seed"}."""
    leader, follower = arg["leader_key"], arg["follower_key"]
    since, until = arg.get("since"), arg.get("until")
    symbol = f"{leader}->{follower}"
    lead_rows = common.load_bars(leader, since, until)
    follow_rows = common.load_bars(follower, since, until)
    if not lead_rows or not follow_rows:
        return {"id": "E1", "symbol": symbol, "window": [since, until], "error": "нет баров в окне"}
    lags = tuple(int(x) for x in arg.get("lags", range(-5, 6)))
    draws = int(arg.get("draws", 200))
    seed = int(arg.get("seed", 0))
    res = analyze(lead_rows, follow_rows, lags, draws, seed)
    return common.report("E1", symbol, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(follower, follow_rows[-1][4]),
                         atr_min_pts=common.atr_minute(follow_rows))
