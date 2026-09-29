"""A4b: продолжение A4 — границы баров по часам/выходным и направление хода.

A4 нашёл аномальный объём/ход в первую минуту после границы сетки 5/15/30/60
(метка бара = НАЧАЛО минуты, «граничная минута» — это первая минута нового
бара старшего таймфрейма). Три открытые задачи (docs/algo-footprints-registry.md,
запись A4; поправки координатора 30.09.2026 после независимой проверки A4):

1. Алгоритмы по закрытию бара (эффект равномерен на ВСЕХ часах И в выходные,
   когда новостей нет) или расписание новостей/сессий (эффект сидит в
   нескольких конкретных минутах и пропадает в выходные)?
   Часть 1a (`minute_class`): минута дня относится к одному из пяти классов —
   `h:00`, `h:30`, `h:15,h:45`, «остальные кратные 5», «прочие» (не кратно 5);
   stat класса = медиана объёма/|ход| класса к медиане БАЗОВОГО класса
   «прочие» того же дня (баз. класс сам с собой тривиален 1.0 — не выводится).
   Нуль — сдвиг ВСЕЙ разметки классов на случайное 1..59 минут, своё у
   каждого дня, draws розыгрышей (не вложенные сетки — так один тест сразу
   отвечает, особые ли ТОЛЬКО h:00/h:30 или и h:15/h:05 и прочие 5-минутки).
   Часть 1b (`ex_events`): stat A4 (a4_bar_boundary._ratios, переиспользуется
   импортом) без `event_minutes` с ОБЕИХ сторон сравнения; stat_full — тот же
   расчёт без исключения, для сравнения (сидит ли эффект в событийных
   минутах). Обе части 1a/1b считаются ОТДЕЛЬНО для будних и выходных дней
   (`group`): FORTS торгует выходные, новости по расписанию — нет, поэтому
   избыток на границах в выходные = алгоритм по бару, а не по расписанию.
2. Направление: ход границы продолжается или откатывается? (`follow`,
   subgroup="all") Подписанный ход sign(close[m]-open[m]) * (close[m+h]-
   close[m]) по горизонтам h против контроля — неграничных минут того же дня
   с ходом сопоставимой величины (квартиль |ход| дня), чтобы сравнивать
   равные импульсы, а не любой шум.
3. Предсказывает ли объём границы продолжение/откат? (`follow`,
   subgroup="top_vol"/"low_vol") Тот же тест 2 отдельно для верхнего и
   нижнего квартиля объёма граничных минут дня.

Нуль везде — сдвиг разметки (класса или сетки) на случайную величину, свою у
каждого дня. Интервал — бутстрап по дням (common.bootstrap_days). p
односторонний (common.pvalue_and_ci: «нуль >= настоящего»; p=1/(draws+1) —
пол разрешения теста, не измеренное значение). Ожидаемый ход может быть и
продолжением (положительный stat), и откатом (отрицательный) — для
отрицательного стата используется обратная сторона тем же приёмом, что
`_pvalue_lower` в c3_spoofing.py (инверсия знака туда-обратно).

absmove на RI/BR упирается в шаг тика (медиана |c-o| берёт только 1/1.5/2/3
от базы) — `ex_events` рядом с медианной absmove добавляет поле
"absmove_mean" (среднее вместо медианы, из a4_ratios) через тот же
переиспользованный `_ratios`; в `minute_class` используется медиана (там
знаменатель — конкретный базовый класс, не общий фон, устойчивость к тику
не так критична, но при желании считать средним минимум тот же принцип).

Исключения (ночь, первая минута после перерыва) — как у A4, `_exclusion`
переиспользуется импортом; клиринг 14:00-14:05/18:45-19:05 по умолчанию НЕ
исключается (по RIZ6 в эти минуты реальные объёмы, вырезать нельзя) —
`arg["exclude_clearing"]` включает старое поведение явно.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common
from trader.lab.footprints.a4_bar_boundary import _exclusion
from trader.lab.footprints.a4_bar_boundary import _ratios as a4_ratios

DEFAULT_GRIDS = (30, 60)
DEFAULT_HORIZONS = (1, 3, 5, 15)
# 10:00, 15:30, 16:30, 17:00 (открытие США 10:00 ET), 23:00, в минутах от
# полуночи. 19:00 (клиринг) не отдельным событием — клиринг уже свой класс
# исключений (exclude_clearing), дублировать в событиях незачем.
DEFAULT_EVENT_MINUTES = (600, 930, 990, 1020, 1380)

CLASSES = ("h:00", "h:30", "h:15,h:45", "остальные кратные 5", "прочие")
_BASELINE_CLASS = "прочие"


def _minute_class(m: int, offset: int = 0) -> str:
    r = (m - offset) % 60
    if r == 0:
        return "h:00"
    if r == 30:
        return "h:30"
    if r in (15, 45):
        return "h:15,h:45"
    if r % 5 == 0:
        return "остальные кратные 5"
    return "прочие"


def prepare(rows: list[list], exclude_clearing: bool = False) -> tuple[dict, dict]:
    """Бары -> {день: [(минута, open, close, объём), ...]}; исключения как у
    A4 (a4_bar_boundary._exclusion): prev для «открытия после перерыва» не
    двигается по барам «ночь» (не маскировать разрыв случайным тиком), но
    двигается по любым другим исключённым — иначе первый же исключённый бар
    дня каскадом исключает всё, что после него (баг первой правки A4)."""
    days, excl = {}, {}
    for d, drows in common.by_day(rows).items():
        keep, prev = [], None
        for r in drows:
            why = _exclusion(r, prev, exclude_clearing)
            if why:
                excl[why] = excl.get(why, 0) + 1
            else:
                keep.append((common.minute_of_day(r[0]), float(r[1]), float(r[4]), float(r[5])))
            if why != "ночь":
                prev = r[0]
        if keep:
            days[d] = keep
    return days, excl


def _to_a4_format(days: dict) -> dict:
    """(минута, open, close, объём) -> (минута, объём, |close-open|) для a4_ratios."""
    return {d: [(m, v, abs(c - o)) for m, o, c, v in recs] for d, recs in days.items()}


def _split_weekend(days: dict) -> tuple[dict, dict]:
    """Будни (пн-пт) и выходные (сб-вс) отдельными наборами дней."""
    weekday = {d: r for d, r in days.items() if d.weekday() < 5}
    weekend = {d: r for d, r in days.items() if d.weekday() >= 5}
    return weekday, weekend


# ---------------------------------------------------------------- часть 1a ---

def _class_stat(days: dict, offsets: dict) -> dict:
    """{класс: (vol_ratio, absmove_ratio)} к базовому классу «прочие» той же
    разметки; offsets.get(день, 0) сдвигает разметку классов дня."""
    by_v: dict[str, list] = defaultdict(list)
    by_a: dict[str, list] = defaultdict(list)
    for d, recs in days.items():
        off = offsets.get(d, 0)
        for m, o, c, v in recs:
            cls = _minute_class(m, off)
            by_v[cls].append(v)
            by_a[cls].append(abs(c - o))
    base_v, base_a = by_v.get(_BASELINE_CLASS, []), by_a.get(_BASELINE_CLASS, [])
    med_base_v = statistics.median(base_v) if base_v else None
    med_base_a = statistics.median(base_a) if base_a else None
    out = {}
    for cls in CLASSES:
        v, a = by_v.get(cls, []), by_a.get(cls, [])
        vol_ratio = (statistics.median(v) / med_base_v) if v and med_base_v else None
        abs_ratio = (statistics.median(a) / med_base_a) if a and med_base_a else None
        out[cls] = (vol_ratio, abs_ratio)
    return out


def class_rows(days: dict, draws: int, seed: int, group: str) -> list[dict]:
    if not days:
        return []
    rng = random.Random(seed)
    real = _class_stat(days, {})
    null_draws = [_class_stat(days, {d: rng.randint(1, 59) for d in days}) for _ in range(draws)]
    boot = common.bootstrap_days(
        days, lambda blocks: _class_stat({i: b for i, b in enumerate(blocks)}, {}), draws, rng)
    n_days = len(days)

    rows = []
    for cls in CLASSES:
        if cls == _BASELINE_CLASS:
            continue  # класс против самого себя тривиально 1.0 — без информации
        real_v, real_a = real[cls]
        null_v = [nd[cls][0] for nd in null_draws]
        null_a = [nd[cls][1] for nd in null_draws]
        boot_v = [b[cls][0] for b in boot]
        boot_a = [b[cls][1] for b in boot]
        rows.append({"test": "minute_class", "group": group, "class": cls, "field": "volume",
                     "n_days": n_days, "stat": real_v,
                     "null": common.pvalue_and_ci(real_v, null_v, boot_v)})
        rows.append({"test": "minute_class", "group": group, "class": cls, "field": "absmove",
                     "n_days": n_days, "stat": real_a,
                     "null": common.pvalue_and_ci(real_a, null_a, boot_a)})
    return rows


# ---------------------------------------------------------------- часть 1b ---

def ex_events_rows(a4_days: dict, grids, event_minutes: set, draws: int, seed: int,
                   group: str) -> list[dict]:
    """Тот же stat, что a4_bar_boundary.grid_rows (volume/absmove/absmove_mean
    из a4_ratios), но с event_minutes, убранными из ОБЕИХ сторон сравнения;
    stat_full — исходный расчёт A4 без исключения, для сравнения (сидит ли
    эффект в событийных минутах или размазан по всем часам)."""
    if not a4_days:
        return []
    rng = random.Random(seed)
    blocks_full = list(a4_days.values())
    ex_days = {d: [t for t in recs if t[0] not in event_minutes] for d, recs in a4_days.items()}
    ex_blocks = list(ex_days.values())

    rows = []
    for g in grids:
        g = int(g)
        full = a4_ratios(blocks_full, g, [0] * len(blocks_full))
        real = a4_ratios(ex_blocks, g, [0] * len(ex_blocks))
        null = [a4_ratios(ex_blocks, g, [rng.randint(1, g - 1) for _ in ex_blocks])
                for _ in range(draws)]
        boot = common.bootstrap_days(
            ex_days, lambda b, g=g: a4_ratios(b, g, [0] * len(b)), draws, rng)
        for idx, field in ((0, "volume"), (1, "absmove"), (2, "absmove_mean")):
            verdict = common.pvalue_and_ci(real[idx], [x[idx] for x in null], [x[idx] for x in boot])
            rows.append({"test": "ex_events", "group": group, "grid": g, "field": field,
                         "stat": real[idx], "null": verdict, "stat_full": full[idx]})
    return rows


# ---------------------------------------------------------------- часть 2/3 ---

def _lookup_by_day(days: dict) -> dict:
    """{день: {минута: (open, close)}}, построено один раз и переиспользуется
    во всех draws/подгруппах — не строить заново на каждой итерации (30 дней
    x sessions x draws иначе доминирует по времени)."""
    return {d: {m: (o, c) for m, o, c, v in recs} for d, recs in days.items()}


def _boundary_events(days: dict, g: int, offsets: dict) -> dict:
    """{день: [(m,o,c,v), ...]} на сетке g со сдвигом offsets.get(день, 0)."""
    out = {}
    for d, recs in days.items():
        off = offsets.get(d, 0)
        out[d] = [(m, o, c, v) for m, o, c, v in recs if (m - off) % g == 0]
    return out


def _outcomes_for_events(lookup_by_day: dict, on_by_day: dict, h: int) -> dict:
    """Подписанный ход sign(c-o)*(close[m+h]-close[m]) по минутам on_by_day,
    только с ненулевым ходом и валидным горизонтом (минута m+h есть в дне)."""
    out = {}
    for d, on in on_by_day.items():
        lookup = lookup_by_day.get(d, {})
        outs = []
        for m, o, c, v in on:
            if c == o:
                continue
            fut = lookup.get(m + h)
            if fut is None:
                continue
            sign = 1.0 if c > o else -1.0
            outs.append(sign * (fut[1] - c))
        out[d] = outs
    return out


def _quantile_bin(edges: list[float], x: float) -> int:
    i = 0
    for e in edges:
        if x > e:
            i += 1
    return i


def _control_outcomes(lookup_by_day: dict, days: dict, exclude_minutes: dict,
                       real_on: dict, h: int) -> list[float]:
    """Контроль для real_on: неграничные минуты того же дня (вне exclude_minutes)
    с ходом сопоставимой величины — подбор по квартилю |ход| дня, чтобы
    сравнивать равные импульсы, не любой шум."""
    out = []
    for d, recs in days.items():
        excl = exclude_minutes.get(d, set())
        lookup = lookup_by_day.get(d, {})
        pool = []
        for m, o, c, v in recs:
            if m in excl or c == o:
                continue
            fut = lookup.get(m + h)
            if fut is None:
                continue
            sign = 1.0 if c > o else -1.0
            pool.append((abs(c - o), sign * (fut[1] - c)))
        if not pool:
            continue
        abs_moves = sorted(x for x, _ in pool)
        edges = statistics.quantiles(abs_moves, n=4) if len(abs_moves) >= 4 else []
        bins: dict[int, list[float]] = defaultdict(list)
        for am, oc in pool:
            bins[_quantile_bin(edges, am)].append(oc)
        for m, o, c, v in real_on.get(d, []):
            if c == o:
                continue
            fut = lookup.get(m + h)
            if fut is None:
                continue
            out.extend(bins.get(_quantile_bin(edges, abs(c - o)), []))
    return out


def _select_all(on_by_day: dict) -> dict:
    return on_by_day


def _select_vol(top: bool):
    """Подгруппа по квартилю объёма ГРАНИЧНЫХ минут дня (не всех минут)."""
    def select(on_by_day: dict) -> dict:
        out = {}
        for d, on in on_by_day.items():
            if len(on) < 4:
                continue
            vols = sorted(v for _, _, _, v in on)
            q1, _, q3 = statistics.quantiles(vols, n=4)
            chosen = [t for t in on if (t[3] >= q3 if top else t[3] <= q1)]
            if chosen:
                out[d] = chosen
        return out
    return select


def _pvalue_lower(real, nulls: list, boots: list) -> dict:
    """common.pvalue_and_ci тестирует «нуль >= настоящего»; для ожидаемого
    ОТРИЦАТЕЛЬНОГО эффекта (откат) нужна обратная сторона — тот же приём, что
    `_pvalue_lower` в c3_spoofing.py (инверсия знака туда-обратно)."""
    def neg(xs):
        return [-x for x in xs if x is not None]
    res = common.pvalue_and_ci(-real if real is not None else None, neg(nulls), neg(boots))
    res["stat"] = real
    lo, hi = res["ci95"]
    res["ci95"] = [-hi if hi is not None else None, -lo if lo is not None else None]
    return res


def _null_verdict(real, nulls: list, boots: list) -> dict:
    """Знак решает, какая сторона теста нужна: продолжение (real>=0) —
    common.pvalue_and_ci как есть; откат (real<0) — _pvalue_lower."""
    if real is not None and real < 0:
        return _pvalue_lower(real, nulls, boots)
    return common.pvalue_and_ci(real, nulls, boots)


def _row_for(days: dict, lookup_by_day: dict, g: int, h: int, subgroup: str, select,
             draws: int, rng: random.Random) -> dict:
    on_full = _boundary_events(days, g, {})
    real_on = select(on_full)
    outcomes = _outcomes_for_events(lookup_by_day, real_on, h)
    flat = [x for v in outcomes.values() for x in v]
    med = statistics.median(flat) if flat else None

    null_stats = []
    for _ in range(draws):
        offsets = {d: rng.randint(1, g - 1) for d in days}
        shifted = select(_boundary_events(days, g, offsets))
        sh_flat = [x for v in _outcomes_for_events(lookup_by_day, shifted, h).values() for x in v]
        null_stats.append(statistics.median(sh_flat) if sh_flat else None)

    boot = common.bootstrap_days(
        outcomes,
        lambda blocks: statistics.median([x for b in blocks for x in b]) if any(blocks) else None,
        draws, rng)

    exclude = {d: {m for m, *_ in on} for d, on in on_full.items()}
    ctrl = _control_outcomes(lookup_by_day, days, exclude, real_on, h)

    return {
        "test": "follow", "grid": g, "h": h, "subgroup": subgroup,
        "n": len(flat), "median": med,
        "mean": statistics.fmean(flat) if flat else None,
        "pos_share": (sum(1 for x in flat if x > 0) / len(flat)) if flat else None,
        "control_median": statistics.median(ctrl) if ctrl else None,
        "null": _null_verdict(med, null_stats, boot),
    }


def follow_rows(days: dict, grids, horizons, draws: int, seed: int) -> list[dict]:
    if not days:
        return []
    rng = random.Random(seed)
    lookup_by_day = _lookup_by_day(days)
    rows = []
    for g in grids:
        g = int(g)
        for h in horizons:
            h = int(h)
            rows.append(_row_for(days, lookup_by_day, g, h, "all", _select_all, draws, rng))
            rows.append(_row_for(days, lookup_by_day, g, h, "top_vol", _select_vol(True), draws, rng))
            rows.append(_row_for(days, lookup_by_day, g, h, "low_vol", _select_vol(False), draws, rng))
    return rows


# --------------------------------------------------------------- сборка ---

def analyze(rows: list[list], grids=DEFAULT_GRIDS, horizons=DEFAULT_HORIZONS,
            event_minutes=DEFAULT_EVENT_MINUTES, draws: int = 200, seed: int = 0,
            exclude_clearing: bool = False) -> dict:
    days, excl = prepare(rows, exclude_clearing)
    ev_set = {int(m) for m in event_minutes}
    wd_days, we_days = _split_weekend(days)

    out_rows: list[dict] = []
    for group, gdays in (("будни", wd_days), ("выходные", we_days)):
        out_rows.extend(class_rows(gdays, draws, seed, group))
        out_rows.extend(ex_events_rows(_to_a4_format(gdays), grids, ev_set, draws, seed, group))
    out_rows.extend(follow_rows(days, grids, horizons, draws, seed))

    first, second = common.halves(rows)
    halves_out = {
        "first": follow_rows(prepare(first, exclude_clearing)[0], grids, horizons, draws, seed),
        "second": follow_rows(prepare(second, exclude_clearing)[0], grids, horizons, draws, seed),
    }

    return {
        "rows": out_rows,
        "halves": halves_out,
        "n_days": len(days),
        "notes": [f"исключено {why}: {n} мин" for why, n in sorted(excl.items())] + [
            f"клиринг исключён: {exclude_clearing}",
            f"будни: {len(wd_days)} дн., выходные: {len(we_days)} дн.",
            "minute_class: h:00/h:30/h:15,h:45/остальные кратные 5 против баз. класса "
            "«прочие»; нуль — сдвиг всей разметки классов на 1..59 мин по дням",
            "ex_events: event_minutes исключены из обеих сторон сравнения; absmove_mean — "
            "средним вместо медианы (тик RI/BR держит медиану ratio на 1/1.5/2/3)",
            "часть 1 считается отдельно для будних и выходных дней: в выходные новостей "
            "нет, избыток на границах там — довод за алгоритм по бару, а не расписание",
            "follow: sign(close[m]-open[m])*(close[m+h]-close[m]); контроль — "
            "неграничные минуты того же дня, подобранные по квартилю |ход| дня",
            "p односторонний; отрицательный стат (откат) тестируется нижним хвостом; "
            "p=1/(draws+1) — пол разрешения теста, не значение",
        ],
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "grids", "horizons",
    "event_minutes", "draws", "seed", "exclude_clearing"}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "A4b", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    res = analyze(rows, tuple(arg.get("grids", DEFAULT_GRIDS)),
                  tuple(arg.get("horizons", DEFAULT_HORIZONS)),
                  tuple(arg.get("event_minutes", DEFAULT_EVENT_MINUTES)),
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)),
                  bool(arg.get("exclude_clearing", False)))
    price = statistics.median(r[4] for r in rows)
    return common.report("A4b", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(key, price),
                         atr_min_pts=common.atr_minute(rows))
