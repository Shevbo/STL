"""EXEC1: поверхность издержек взятия ликвидности по полной выжимке стакана.

Программа: docs/execution-cost-program.md, шаг 1. Чем платит тейкер за филл
разных размеров в разное время: спред и цена прохода рынком N лотов (VWAP
съедаемых уровней минус mid), по минуте дня, секунде внутри минуты и классу
минуты (граница часа/получаса, событие расписания, будни/выходные). Только
описательная поверхность — нулевых распределений и p-значений здесь не нужно,
ответ на «когда входить дорого», не «есть ли эффект».

ДАННЫЕ И ЗАГРУЗКА. Та же полная выжимка стакана, что у C1 (ключ вида
`book<КОД>f0929`, 5 уровней, ~1 снимок/1.2 с, МСК-стенка как UTC) — загрузчик
`_load_full_book` и разрез по половинам окна `_halves_book` переиспользованы
оттуда импортом, не дублируются.

ЦЕНА ПРОХОДА. VWAP N лотов по встречной стороне минус mid, знак — издержка
(положительное = хуже для нас): для покупки идём по asks, для продажи — по
bids. Проход по уровням и пометка `deep` (не хватило 5 уровней, остаток по
худшему уровню) — тот же алгоритм, что у BookRuntime._walk в book_replay.py
(тоже переиспользован импортом, не переписан).

КЛАСС МИНУТЫ. h:00 | h:30 | событие (10:00, 15:30, 16:30, 17:00, 23:00) |
остальные — четыре ВЗАИМОИСКЛЮЧАЮЩИХ класса. Событийная минута приоритетнее
h:00/h:30 (17:00 и 23:00 иначе ушли бы в h:00, 16:30 — в h:30): иначе часовой
эффект молча впитывает событийный, как предупреждает a4_bar_boundary про
пересечение сеток 30/60 с расписанием.

СХЕМА СТРОК. Единая для всех cut: {"cut", "key", "side", "size", "n",
"spread_med", "cost_med", "cost_p90", "deep_share"}. Строка про спред не несёт
side/size (оба None, cost_* тоже None); строка про cost(N) не несёт spread_med
(None) — поля общие ради одной таблицы, неприменимые просто пустые.

ПРОРЕЖИВАНИЕ. Снимков в окне больше max_rows — берётся каждый k-й (сдвиг
фазы от seed, чтобы прореживание не било систематически в одну секунду),
k в notes.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from trader.lab.book_replay import BookRuntime
from trader.lab.footprints import common
from trader.lab.footprints.c1_book_imbalance import _halves_book, _load_full_book

SIZES_DEFAULT = (1, 2, 5, 10, 20)
MAX_ROWS_DEFAULT = 400_000
EVENT_MINUTES = {10 * 60, 15 * 60 + 30, 16 * 60 + 30, 17 * 60, 23 * 60}


def _minute_class(minute: int) -> str:
    if minute in EVENT_MINUTES:
        return "событие"
    if minute % 60 == 0:
        return "h:00"
    if minute % 60 == 30:
        return "h:30"
    return "остальные"


# --------------------------------------------------------------------------
# Подготовка: по снимку -> спред + cost(N) по стороне, ключи разрезов
# --------------------------------------------------------------------------

def _prep(rows: list[tuple], sizes: tuple[int, ...]) -> tuple[list[dict], int]:
    """rows (см. _load_full_book) -> ([снимок вне сессии выброшен], excluded)."""
    out = []
    excluded = 0
    for ts_ms, bids, asks in rows:
        t_s = ts_ms / 1000
        minute = common.minute_of_day(t_s)
        if not common.in_session(minute):
            excluded += 1
            continue
        bid1, ask1 = bids[0][0], asks[0][0]
        mid = (bid1 + ask1) / 2
        costs = {}
        for n in sizes:
            vwap_ask, deep_buy = BookRuntime._walk(asks, n)
            vwap_bid, deep_sell = BookRuntime._walk(bids, n)
            costs[("buy", n)] = (vwap_ask - mid, deep_buy)
            costs[("sell", n)] = (mid - vwap_bid, deep_sell)
        day = common.day_of(t_s)
        out.append({
            "day": day, "minute": minute, "second": int(t_s) % 60, "hour": minute // 60,
            "boundary": minute % 30 == 0, "cls": _minute_class(minute),
            "weekend": day.weekday() >= 5,
            "spread": ask1 - bid1, "costs": costs,
        })
    return out, excluded


# --------------------------------------------------------------------------
# Агрегация
# --------------------------------------------------------------------------

def _pct(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))]


def _stats(vals: list[float]) -> tuple[float | None, float | None, int]:
    if not vals:
        return None, None, 0
    s = sorted(vals)
    return statistics.median(s), _pct(s, 0.90), len(s)


def _group_by(points: list[dict], keyfn) -> dict:
    out = defaultdict(list)
    for p in points:
        out[keyfn(p)].append(p)
    return out


def _rows_for_group(cut: str, key, pts: list[dict], sizes) -> list[dict]:
    med, p90, n = _stats([p["spread"] for p in pts])
    rows = [{"cut": cut, "key": key, "side": None, "size": None, "n": n,
             "spread_med": med, "cost_med": None, "cost_p90": None, "deep_share": None}]
    for size in sizes:
        for side in ("buy", "sell"):
            vals = [p["costs"][(side, size)][0] for p in pts]
            deep_n = sum(1 for p in pts if p["costs"][(side, size)][1])
            c_med, c_p90, n2 = _stats(vals)
            rows.append({"cut": cut, "key": key, "side": side, "size": size, "n": n2,
                         "spread_med": None, "cost_med": c_med, "cost_p90": c_p90,
                         "deep_share": (deep_n / n2) if n2 else None})
    return rows


def _cut(points: list[dict], keyfn, sizes, cut: str) -> list[dict]:
    rows = []
    for key, pts in sorted(_group_by(points, keyfn).items(), key=lambda kv: str(kv[0])):
        rows += _rows_for_group(cut, key, pts, sizes)
    return rows


def analyze(rows: list[tuple], *, sizes: tuple[int, ...] = SIZES_DEFAULT,
            max_rows: int = MAX_ROWS_DEFAULT, seed: int = 0) -> dict:
    """rows = [(ts_ms, bids, asks), ...] -> {rows, n_days, notes}."""
    points, excluded = _prep(rows, sizes)
    total = len(points)
    step = max(1, -(-total // max_rows)) if max_rows else 1
    sample = points[seed % step::step] if step > 1 else points

    minute_sizes = tuple(n for n in (1, 10) if n in sizes)
    out_rows = (
        _cut(sample, lambda p: p["minute"], minute_sizes, "minute")
        + _cut(sample, lambda p: f"{'boundary' if p['boundary'] else 'other'}:{p['second']:02d}",
               sizes, "second")
        + _cut(sample, lambda p: f"{p['cls']}/{'выходные' if p['weekend'] else 'будни'}", sizes, "class")
        + _cut(sample, lambda p: p["hour"], sizes, "hour")
    )

    deep_total = sum(1 for p in sample for _c, d in p["costs"].values() if d)
    cost_total = sum(len(p["costs"]) for p in sample)
    notes = [f"вне сессии отброшено: {excluded} снимков"]
    notes.append(f"доля deep (не хватило 5 уровней книги): {deep_total / cost_total:.3f}"
                 if cost_total else "доля deep: н/д (снимков нет)")
    if step > 1:
        notes.append(f"прореживание: каждый {step}-й снимок (max_rows={max_rows})")
    return {"rows": out_rows, "n_days": len({p["day"] for p in points}), "notes": notes}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "book_key" (обязателен), "since",
    "until", "sizes", "seed", "max_rows"}."""
    book_key = arg.get("book_key")
    if not book_key:
        return {"id": "EXEC1", "error": "book_key обязателен"}
    symbol_key = arg.get("symbol_key") or book_key
    since, until = arg.get("since"), arg.get("until")
    rows, dropped = _load_full_book(book_key, since, until)
    if not rows:
        return {"id": "EXEC1", "symbol": symbol_key, "window": [since, until],
                "error": "нет снимков стакана в окне"}

    sizes = tuple(int(x) for x in arg.get("sizes", SIZES_DEFAULT))
    seed = int(arg.get("seed", 0))
    max_rows = int(arg.get("max_rows", MAX_ROWS_DEFAULT))

    res = analyze(rows, sizes=sizes, max_rows=max_rows, seed=seed)
    first_rows, second_rows = _halves_book(rows)
    halves = {
        "first": analyze(first_rows, sizes=sizes, max_rows=max_rows, seed=seed)["rows"]
                 if len(first_rows) > 1 else [],
        "second": analyze(second_rows, sizes=sizes, max_rows=max_rows, seed=seed)["rows"]
                  if len(second_rows) > 1 else [],
    }
    notes = [f"снимков отброшено (нет обеих сторон / цена<=0): {dropped}", *res["notes"]]
    last_bid1, last_ask1 = rows[-1][1][0][0], rows[-1][2][0][0]
    last_mid = (last_bid1 + last_ask1) / 2
    return common.report("EXEC1", symbol_key, [since, until], res["rows"], notes,
                         n_days=res["n_days"], halves=halves,
                         cost_pts=common.round_trip_cost_pts(symbol_key, last_mid))
