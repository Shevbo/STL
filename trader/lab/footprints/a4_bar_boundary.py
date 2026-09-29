"""A4: алгоритмы, торгующие по границам баров 5/15/30/60 мин.

След: в первую минуту после границы (минута_дня % g == 0, метка бара = его
начало) объём и |close-open| выше, чем в остальных минутах.

Статистика по сетке g: отношение медиан (на сетке / вне сетки) для объёма и
для |close-open|, по всем минутам окна.

Нуль: та же сетка, смещённая на случайное 1..g-1 минут, смещение своё у
каждого дня (shift_grid), draws розыгрышей. Один фиксированный сдвиг даёт
только g-1 нулей (p не ниже 1/g, для g=5 это 0.2), поэтому сдвиг
разыгрывается по дням. Интервал: бутстрап по дням. Первая и вторая половина
окна (по целым дням) отдельными строками.

Исключены с пометкой в notes (не молча): клиринги 14:00-14:05 и 18:45-19:05,
ночь 23:50-09:00, первая минута после перерыва > 5 мин (открытие сессии
падает на сетку 5 и сама по себе даёт всплеск объёма).

Ограничение: особые минуты расписания (10:00, 16:30, 19:00) лежат на сетках
30/60 и попадают в числитель; их отдельно меряет A5. Вложенные сетки тоже
связаны: эффект на 15 поднимет и сетку 5 (каждая третья её точка кратна 15).
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common

CLEARINGS = ((14 * 60, 14 * 60 + 5), (18 * 60 + 45, 19 * 60 + 5))
NIGHT = (23 * 60 + 50, 9 * 60)          # [23:50, 24:00) и [00:00, 09:00)
OPEN_GAP_S = 5 * 60


def _exclusion(r, prev_ts) -> str | None:
    m = common.minute_of_day(r[0])
    if any(a <= m < b for a, b in CLEARINGS):
        return "клиринг"
    if m >= NIGHT[0] or m < NIGHT[1]:
        return "ночь"
    if prev_ts is None or r[0] - prev_ts > OPEN_GAP_S:
        return "открытие после перерыва"
    return None


def prepare(rows: list[list]) -> tuple[dict, dict]:
    """Бары -> {день: [(минута, объём, |c-o|), ...]} и счётчик исключений."""
    days, excl = {}, {}
    for d, drows in common.by_day(rows).items():
        keep, prev = [], None
        for r in drows:
            why = _exclusion(r, prev)
            prev = r[0]
            if why:
                excl[why] = excl.get(why, 0) + 1
            else:
                keep.append((common.minute_of_day(r[0]), float(r[5]), abs(r[4] - r[1])))
        if keep:
            days[d] = keep
    return days, excl


def _ratios(blocks: list[list], g: int, offsets: list[int]) -> tuple[float | None, float | None]:
    """Отношения медиан на сетке к вне сетки (объём, |Δp|); offsets по блокам."""
    on_v, off_v, on_a, off_a = [], [], [], []
    for blk, off in zip(blocks, offsets):
        on = common.shift_grid(off)
        for m, v, a in blk:
            if on(m, g):
                on_v.append(v)
                on_a.append(a)
            else:
                off_v.append(v)
                off_a.append(a)

    def ratio(x, y):
        if not x or not y:
            return None
        den = statistics.median(y)
        return statistics.median(x) / den if den else None
    return ratio(on_v, off_v), ratio(on_a, off_a)


def grid_rows(days: dict, grids, draws: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    blocks = list(days.values())
    rows = []
    for g in grids:
        g = int(g)
        real_v, real_a = _ratios(blocks, g, [0] * len(blocks))
        null = [_ratios(blocks, g, [rng.randint(1, g - 1) for _ in blocks]) for _ in range(draws)]
        boot = common.bootstrap_days(days, lambda b, g=g: _ratios(b, g, [0] * len(b)), draws, rng)
        n_on = sum(1 for blk in blocks for m, _, _ in blk if m % g == 0)
        rows.append({
            "grid": g, "n_on": n_on, "n_off": sum(map(len, blocks)) - n_on,
            "volume": common.pvalue_and_ci(real_v, [x[0] for x in null], [x[0] for x in boot]),
            "absmove": common.pvalue_and_ci(real_a, [x[1] for x in null], [x[1] for x in boot]),
        })
    return rows


def analyze(rows: list[list], grids=(5, 15, 30, 60), draws: int = 200, seed: int = 0) -> dict:
    days, excl = prepare(rows)
    first, second = common.halves(rows)
    return {
        "rows": grid_rows(days, grids, draws, seed),
        "halves": {"first": grid_rows(prepare(first)[0], grids, draws, seed),
                   "second": grid_rows(prepare(second)[0], grids, draws, seed)},
        "n_days": len(days),
        "notes": [f"исключено {why}: {n} мин" for why, n in sorted(excl.items())]
                 + ["stat = медиана на сетке / медиана вне сетки; p односторонний"],
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "grids", "draws", "seed"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "A4", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows, tuple(arg.get("grids", (5, 15, 30, 60))),
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)))
    price = statistics.median(r[4] for r in rows)
    return common.report("A4", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
