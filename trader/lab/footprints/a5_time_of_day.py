"""A5: алгоритмы по расписанию внутри дня (клиринги, открытия, закрытие).

След: одна и та же минута дня m систематически даёт ход одного знака за
горизонт h (close[t+h] - close[t]), либо систематически большой |ход| или
объём. В отличие от A4 (граница M-минутных баров) минуты клиринга и открытий
здесь НЕ исключаются -- они и есть предмет проверки (10:00, 14:00, 14:05,
16:30, 18:45, 19:00, 23:45).

Статистика по минуте m и горизонту h: n, mean, median, t = mean/(std/sqrt(n))
по подписанному ходу; pos_share -- доля положительных ходов; absmove_ratio --
медиана |ход|/медиана |ход| того же дня (та же выборка минут при этом h);
vol_ratio -- медиана объём(m)/медиана объёма дня. Ratio-поля справочные
(масштаб эффекта), p-value за ними не считается -- задача просит p только
для t (см. реестр).

Множественные сравнения (~890 минут): нуль для t строится циклическим
сдвигом ряда ПРИРАЩЕНИЙ внутри каждого дня на случайное число позиций
(сохраняет автокорреляцию и хвосты дня, рвёт привязку к минуте), пересчётом
t по всем минутам и взятием max|t| за розыгрыш. p_adj минуты = доля
розыгрышей с max|t| >= |t| минуты (поправка на перебор). p_named -- та же
доля, но без max: только нулевые |t| ЭТОЙ минуты (минуты названы заранее --
поправка на перебор для них не нужна).

halves (первая/вторая половина окна по целым дням, common.halves) даёт ТОЛЬКО
реальные mean/t именованных минут, без розыгрышей -- нужен лишь знак, не
p-value, розыгрыши тут были бы тратой счёта i9 впустую.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common

MINUTE_LO = 9 * 60              # 540, 09:00
MINUTE_HI = 23 * 60 + 50        # 1430, 23:50 (граница ночи, исключена)
NAMED_MINUTES = (600, 840, 845, 990, 1125, 1140, 1425)  # 10:00 14:00 14:05 16:30 18:45 19:00 23:45


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def prepare(rows: list[list]) -> tuple[dict, int]:
    """Бары -> {день: {pos,close,vol,vol_base}} в окне 09:00-23:49; счётчик ночных баров."""
    days, excl_night = {}, 0
    for d, drows in common.by_day(rows).items():
        drows = sorted(drows, key=lambda r: r[0])
        minutes, closes, vols = [], [], []
        for r in drows:
            m = common.minute_of_day(r[0])
            if not (MINUTE_LO <= m < MINUTE_HI):
                excl_night += 1
                continue
            minutes.append(m)
            closes.append(float(r[4]))
            vols.append(float(r[5]))
        if len(minutes) >= 2:
            days[d] = {
                "pos": {m: i for i, m in enumerate(minutes)},
                "close": closes,
                "vol": vols,
                "vol_base": statistics.median(vols),
            }
    return days, excl_night


def _valid_pairs(days: dict, h: int) -> dict:
    """minute -> [(день, поз_m, поз_(m+h)), ...]: обе минуты есть, m+h не выходит за день."""
    pairs = defaultdict(list)
    for dk, dd in days.items():
        pos = dd["pos"]
        for m, i in pos.items():
            mh = m + h
            if mh >= MINUTE_HI:
                continue
            j = pos.get(mh)
            if j is not None:
                pairs[m].append((dk, i, j))
    return pairs


def _day_absmove_baseline(days: dict, pairs: dict) -> dict:
    per_day = defaultdict(list)
    for lst in pairs.values():
        for dk, i, j in lst:
            per_day[dk].append(abs(days[dk]["close"][j] - days[dk]["close"][i]))
    return {dk: statistics.median(v) for dk, v in per_day.items() if v}


def _minute_real_stats(days: dict, pairs: dict, baseline: dict) -> dict:
    out = {}
    for m, lst in pairs.items():
        moves = [days[dk]["close"][j] - days[dk]["close"][i] for dk, i, j in lst]
        n = len(moves)
        if n < 2:
            continue
        std = statistics.stdev(moves)
        if std <= 0:
            continue
        aratios = [abs(days[dk]["close"][j] - days[dk]["close"][i]) / baseline[dk]
                   for dk, i, j in lst if baseline.get(dk)]
        vratios = [days[dk]["vol"][i] / days[dk]["vol_base"] for dk, i, _ in lst if days[dk]["vol_base"]]
        mean = statistics.mean(moves)
        out[m] = {
            "n": n, "mean": mean, "median": statistics.median(moves),
            "t": mean / (std / math.sqrt(n)),
            "pos_share": sum(1 for v in moves if v > 0) / n,
            "absmove_ratio": statistics.median(aratios) if aratios else None,
            "vol_ratio": statistics.median(vratios) if vratios else None,
        }
    return out


def _shift_close(closes: list[float], k: int) -> list[float]:
    """Синтетический close дня: ряд приращений циклически сдвинут на k позиций."""
    n = len(closes)
    if n < 2:
        return list(closes)
    inc = [closes[i] - closes[i - 1] for i in range(1, n)]
    k %= len(inc)
    if k:
        inc = inc[-k:] + inc[:-k]
    out = [closes[0]]
    for v in inc:
        out.append(out[-1] + v)
    return out


def _null_draw(days: dict, pairs: dict, rng: random.Random) -> dict:
    """Один розыгрыш: свой случайный сдвиг на каждый день, |t| по всем минутам."""
    shifted = {dk: _shift_close(dd["close"], rng.randrange(max(1, len(dd["close"]))))
               for dk, dd in days.items()}
    out = {}
    for m, lst in pairs.items():
        moves = [shifted[dk][j] - shifted[dk][i] for dk, i, j in lst]
        n = len(moves)
        if n < 2:
            continue
        std = statistics.stdev(moves)
        if std <= 0:
            continue
        out[m] = abs(statistics.mean(moves) / (std / math.sqrt(n)))
    return out


def _null_stats(days: dict, pairs: dict, draws: int, rng: random.Random) -> tuple[dict, list]:
    per_minute, maxes = defaultdict(list), []
    for _ in range(draws):
        draw = _null_draw(days, pairs, rng)
        maxes.append(max(draw.values()) if draw else 0.0)
        for m, v in draw.items():
            per_minute[m].append(v)
    return per_minute, maxes


def _grid(days: dict, h: int, draws: int, rng: random.Random) -> dict:
    """minute -> строка отчёта (с p_named/p_adj) для одного горизонта h."""
    pairs = _valid_pairs(days, h)
    real = _minute_real_stats(days, pairs, _day_absmove_baseline(days, pairs))
    per_minute_null, max_null = _null_stats(days, pairs, draws, rng)
    rows = {}
    for m, st in real.items():
        abs_t = abs(st["t"])
        rows[m] = {
            "minute": m, "hhmm": _hhmm(m), "h": h, "n": st["n"],
            "mean": st["mean"], "median": st["median"], "t": st["t"],
            "pos_share": st["pos_share"], "absmove_ratio": st["absmove_ratio"],
            "vol_ratio": st["vol_ratio"],
            "p_named": common.pvalue_and_ci(abs_t, per_minute_null.get(m, []), [])["p"],
            "p_adj": common.pvalue_and_ci(abs_t, max_null, [])["p"],
        }
    return rows


def _named_signs(rows: list[list], horizons, named) -> list[dict]:
    """Быстрая проверка знака именованных минут в половине окна, без розыгрышей."""
    days, _ = prepare(rows)
    out = []
    for h in horizons:
        pairs = _valid_pairs(days, h)
        real = _minute_real_stats(days, pairs, _day_absmove_baseline(days, pairs))
        for m in named:
            if m in real:
                st = real[m]
                out.append({"minute": m, "hhmm": _hhmm(m), "h": h, "n": st["n"],
                            "mean": st["mean"], "median": st["median"], "t": st["t"],
                            "pos_share": st["pos_share"]})
    return out


def analyze(rows: list[list], horizons=(1, 5, 15), named=NAMED_MINUTES,
            draws: int = 200, seed: int = 0) -> dict:
    days, excl_night = prepare(rows)
    rng = random.Random(seed)
    all_rows, seen = [], set()
    for h in horizons:
        grid = _grid(days, h, draws, rng)
        top10 = sorted(grid.values(), key=lambda r: -abs(r["t"]))[:10]
        for row in top10 + [grid[m] for m in named if m in grid]:
            key = (row["minute"], row["h"])
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
    first, second = common.halves(rows)
    notes = [
        f"ночь исключена: {excl_night} баров",
        "клиринги 14:00-14:05 и 18:45-19:05 НЕ исключены: это предмет проверки A5",
        "p_named: своя минута без поправки; p_adj: max|t| по всем минутам за розыгрыш",
        "halves: только знак mean/t именованных минут, без розыгрышей (экономия счёта)",
    ]
    return {
        "rows": all_rows,
        "halves": {"first": _named_signs(first, horizons, named),
                   "second": _named_signs(second, horizons, named)},
        "n_days": len(days),
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "horizons", "named", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "A5", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    horizons = tuple(arg.get("horizons", (1, 5, 15)))
    named = tuple(arg.get("named", NAMED_MINUTES))
    res = analyze(rows, horizons, named, int(arg.get("draws", 200)), int(arg.get("seed", 0)))
    return common.report("A5", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, rows[-1][4]),
                         atr_min_pts=common.atr_minute(rows))
