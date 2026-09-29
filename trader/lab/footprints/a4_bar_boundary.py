"""A4: алгоритмы, торгующие по границам баров 5/15/30/60 мин.

След: в первую минуту после границы (минута_дня % g == 0, метка бара = НАЧАЛО
минуты — граничная минута это первая минута нового бара старшего таймфрейма)
объём и |close-open| выше, чем в остальных минутах.

Статистика по сетке g: отношение медиан (на сетке / вне сетки) для объёма и
для |close-open|, по всем минутам окна; absmove_mean_ratio — то же самое, но
средним вместо медианы (см. ниже, почему).

Нуль: та же сетка, смещённая на случайное 1..g-1 минут, смещение своё у
каждого дня (shift_grid), draws розыгрышей. Один фиксированный сдвиг даёт
только g-1 нулей (p не ниже 1/g, для g=5 это 0.2), поэтому сдвиг
разыгрывается по дням. p = 1/(draws+1) — это ПОЛ разрешения тестом, а не
измеренное значение; рядом с p в каждом null-поле лежат квантили нулевого
распределения `null_q: [q50, q90, q95, q99]`, чтобы отличить «настоящий стат
за пределами всего нуля» от «стат ровно на полу». Интервал: бутстрап по дням.
Первая и вторая половина окна (по целым дням) отдельными строками.

Исключения с пометкой в notes (не молча): ночь 23:50-09:00, первая минута
после перерыва > 5 мин (открытие сессии падает на сетку 5 и сама по себе даёт
всплеск объёма). Клиринги 14:00-14:05 и 18:45-19:05 — ПО УМОЛЧАНИЮ БОЛЬШЕ НЕ
ИСКЛЮЧАЮТСЯ (проверено по RIZ6: в эти минуты стоят бары с реальным объёмом,
например 14:01 = 588 лотов в четверг 24.09 — рынок торгует, резать нельзя);
исключение включается явно через arg["exclude_clearing"]=True, счётчик
"клиринг" в notes считается только когда флаг включён.

09.2026, независимая проверка: `prepare` обновлял `prev` (для правила «первая
минута после перерыва») ДАЖЕ по исключённым барам (ночь/клиринг) — если в
данных есть бары 08:5x, минута 09:00 получала prev от них (разрыв всего 60 с)
и НЕ исключалась, хотя обязана. Фикс: `prev` берётся только от бара, который
реально остался в выборке (KEPT), не от любого пройденного.

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


def _exclusion(r, prev_ts, exclude_clearing: bool = False) -> str | None:
    m = common.minute_of_day(r[0])
    if exclude_clearing and any(a <= m < b for a, b in CLEARINGS):
        return "клиринг"
    if m >= NIGHT[0] or m < NIGHT[1]:
        return "ночь"
    if prev_ts is None or r[0] - prev_ts > OPEN_GAP_S:
        return "открытие после перерыва"
    return None


def prepare(rows: list[list], exclude_clearing: bool = False) -> tuple[dict, dict]:
    """Бары -> {день: [(минута, объём, |c-o|), ...]} и счётчик исключений.
    `prev` (для «открытия после перерыва») НЕ двигается по барам, исключённым
    как «ночь» — иначе случайный тик в 08:5x маскирует реальный разрыв, и
    09:00 незаслуженно остаётся в выборке. От любого другого бара (в т.ч.
    клиринга и самого «открытия после перерыва») prev двигается как обычно —
    иначе первый же исключённый бар дня обнуляет prev навсегда и исключает
    ВСЕ следующие бары каскадом (был баг именно в этом при первой правке)."""
    days, excl = {}, {}
    for d, drows in common.by_day(rows).items():
        keep, prev = [], None
        for r in drows:
            why = _exclusion(r, prev, exclude_clearing)
            if why:
                excl[why] = excl.get(why, 0) + 1
            else:
                keep.append((common.minute_of_day(r[0]), float(r[5]), abs(r[4] - r[1])))
            if why != "ночь":
                prev = r[0]
        if keep:
            days[d] = keep
    return days, excl


def _ratios(blocks: list[list], g: int, offsets: list[int]
           ) -> tuple[float | None, float | None, float | None]:
    """Отношения на сетке к вне сетки: (медиана объёма, медиана |Δp|, среднее
    |Δp|); offsets по блокам. Среднее — рядом с медианой, не вместо: на RI/BR
    |c-o| упирается в шаг тика (медиана берёт только 1/1.5/2/3), среднее нет."""
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

    def ratio_median(x, y):
        if not x or not y:
            return None
        den = statistics.median(y)
        return statistics.median(x) / den if den else None

    def ratio_mean(x, y):
        if not x or not y:
            return None
        den = statistics.fmean(y)
        return statistics.fmean(x) / den if den else None

    return ratio_median(on_v, off_v), ratio_median(on_a, off_a), ratio_mean(on_a, off_a)


def _with_null_q(verdict: dict, null_stats: list) -> dict:
    """Добавляет квантили нулевого распределения (q50/q90/q95/q99) в verdict
    common.pvalue_and_ci — p = 1/(draws+1) на полу теста ничем не отличается
    от p чуть выше пола без этого; квантили показывают, где стат на самом
    деле стоит относительно нуля."""
    vals = sorted(x for x in null_stats if x is not None)
    verdict = dict(verdict)
    verdict["null_q"] = [common._pct(vals, q) for q in (0.50, 0.90, 0.95, 0.99)] if vals else [None] * 4
    return verdict


def grid_rows(days: dict, grids, draws: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    blocks = list(days.values())
    rows = []
    for g in grids:
        g = int(g)
        real_v, real_a, real_am = _ratios(blocks, g, [0] * len(blocks))
        null = [_ratios(blocks, g, [rng.randint(1, g - 1) for _ in blocks]) for _ in range(draws)]
        boot = common.bootstrap_days(days, lambda b, g=g: _ratios(b, g, [0] * len(b)), draws, rng)
        n_on = sum(1 for blk in blocks for m, _, _ in blk if m % g == 0)
        null_v, null_a, null_am = [x[0] for x in null], [x[1] for x in null], [x[2] for x in null]
        boot_v, boot_a, boot_am = [x[0] for x in boot], [x[1] for x in boot], [x[2] for x in boot]
        rows.append({
            "grid": g, "n_on": n_on, "n_off": sum(map(len, blocks)) - n_on,
            "volume": _with_null_q(common.pvalue_and_ci(real_v, null_v, boot_v), null_v),
            "absmove": _with_null_q(common.pvalue_and_ci(real_a, null_a, boot_a), null_a),
            "absmove_mean_ratio": _with_null_q(common.pvalue_and_ci(real_am, null_am, boot_am), null_am),
        })
    return rows


def analyze(rows: list[list], grids=(5, 15, 30, 60), draws: int = 200, seed: int = 0,
            exclude_clearing: bool = False) -> dict:
    days, excl = prepare(rows, exclude_clearing)
    first, second = common.halves(rows)
    return {
        "rows": grid_rows(days, grids, draws, seed),
        "halves": {"first": grid_rows(prepare(first, exclude_clearing)[0], grids, draws, seed),
                   "second": grid_rows(prepare(second, exclude_clearing)[0], grids, draws, seed)},
        "n_days": len(days),
        "notes": [f"исключено {why}: {n} мин" for why, n in sorted(excl.items())] + [
            "stat = медиана на сетке / медиана вне сетки (absmove_mean_ratio — средним); p односторонний",
            "p = 1/(draws+1) — пол разрешения теста, не значение; см. null_q в каждом null-поле",
            f"клиринг исключён: {exclude_clearing}",
        ],
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "grids", "draws", "seed",
    "exclude_clearing"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "A4", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows, tuple(arg.get("grids", (5, 15, 30, 60))),
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)),
                  bool(arg.get("exclude_clearing", False)))
    price = statistics.median(r[4] for r in rows)
    return common.report("A4", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
