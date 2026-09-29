"""Каркас проверок отпечатков алгоритмов: загрузка, дни, контроли, отчёт.

Протокол и реестр гипотез: docs/algo-footprints-registry.md. Модули строк
реестра (a4_bar_boundary.py и следующие) берут отсюда всё общее и ничего не
дублируют.

ВРЕМЯ. Метки баров лаборатории и выжимки стакана = московская стенка,
проставленная как UTC (book_replay, book_digest). Поэтому день МСК и минута дня
берутся из метки как есть, БЕЗ сдвига +3 ч: сдвиг здесь перенёс бы вечернюю
сессию в следующий день.

КОНТРОЛИ сохраняют структуру дня: перетасовка внутри дня, перестановка целых
дней, смещение сетки. Полная перетасовка ряда рушит кластеризацию
волатильности и занижает шум (урок retro_reverse 29.09), её здесь нет.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Callable

from trader.lab.retro_reverse import _epoch, _load_bars

# Справка из реестра: цена филла RI по архивному стакану 9-12 пт (project_book_replay).
RI_BOOK_COST_PTS = 10.0


def _window(rows: list[list], since=None, until=None) -> list[list]:
    lo, hi = _epoch(since), _epoch(until, end=True)
    rows = sorted(rows, key=lambda r: r[0])
    return [r for r in rows if (lo is None or r[0] >= lo) and (hi is None or r[0] < hi)]


def load_bars(key: str, since=None, until=None) -> list[list]:
    """Бары [ts, o, h, l, c, v] агентским каналом, окно ISO-дата/epoch, until-дата включительно."""
    return _window(_load_bars(key), since, until)


def load_book(key: str, since=None, until=None) -> tuple[list[int], list[tuple]]:
    """Выжимка стакана (любой частоты) -> (times, books) как book_replay.load_digest."""
    from trader.lab.book_replay import load_digest
    return load_digest(_window(_load_bars(key), since, until))


def day_of(ts) -> date:
    """День МСК по метке бара (метка уже в МСК, см. докстринг модуля)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def minute_of_day(ts) -> int:
    return int(ts) % 86400 // 60


def by_day(rows: list[list]) -> dict[date, list]:
    """Разбивка по дню МСК по времени бара, порядок дней и строк сохраняется."""
    out: dict[date, list] = defaultdict(list)
    for r in rows:
        out[day_of(r[0])].append(r)
    return dict(out)


def shuffle_within_day(values: list, days: list, rng: random.Random) -> list:
    """Значения перетасованы внутри своего дня; days параллелен values."""
    idx: dict = defaultdict(list)
    for i, d in enumerate(days):
        idx[d].append(i)
    out = list(values)
    for pos in idx.values():
        vals = [values[i] for i in pos]
        rng.shuffle(vals)
        for i, v in zip(pos, vals):
            out[i] = v
    return out


def permute_days(rows_by_day: dict, rng: random.Random) -> dict:
    """Те же ключи-дни, содержимое целых дней переставлено между ними."""
    keys = list(rows_by_day)
    vals = [rows_by_day[k] for k in keys]
    rng.shuffle(vals)
    return dict(zip(keys, vals))


def shift_grid(offset: int) -> Callable[[int, int], bool]:
    """Предикат сетки со смещением: (минута_дня, шаг g) -> минута на сетке+offset."""
    return lambda minute, g: (minute - offset) % g == 0


def bootstrap_days(rows_by_day: dict, stat: Callable[[list], float | None],
                   draws: int, rng: random.Random) -> list[float]:
    """Бутстрап по дням: stat() от списка дневных блоков, выбранных с возвращением."""
    blocks = list(rows_by_day.values())
    out = []
    for _ in range(draws):
        s = stat([blocks[rng.randrange(len(blocks))] for _ in blocks])
        if s is not None:
            out.append(s)
    return out


def _pct(s: list[float], q: float) -> float:
    return s[min(len(s) - 1, max(0, int(q * len(s))))]


def pvalue_and_ci(real_stat, null_stats: list, boot_stats: list) -> dict:
    """Односторонний p (доля нулей >= настоящей, с поправкой +1) и 95% бутстрапа."""
    null = [x for x in null_stats if x is not None]
    boot = sorted(x for x in boot_stats if x is not None)
    p = None
    if real_stat is not None and null:
        p = (1 + sum(1 for x in null if x >= real_stat)) / (1 + len(null))
    ci = [_pct(boot, 0.025), _pct(boot, 0.975)] if boot else [None, None]
    return {"stat": real_stat, "p": p, "ci95": ci, "n_null": len(null)}


def halves(rows: list[list]) -> tuple[list[list], list[list]]:
    """Разрез по времени пополам по ЦЕЛЫМ дням: первая половина дней и вторая."""
    days = sorted({day_of(r[0]) for r in rows})
    cut = days[len(days) // 2] if days else None
    first = [r for r in rows if day_of(r[0]) < cut] if cut else []
    second = [r for r in rows if cut and day_of(r[0]) >= cut]
    return first, second


def atr_minute(rows: list[list], n: int = 60) -> float | None:
    """Справка масштаба: медиана скользящего n-барного среднего |high-low|."""
    rng_ = [abs(r[2] - r[3]) for r in rows]
    if len(rng_) < n:
        return None
    s = sum(rng_[:n])
    means = [s / n]
    for i in range(n, len(rng_)):
        s += rng_[i] - rng_[i - n]
        means.append(s / n)
    return statistics.median(means)


def round_trip_cost_pts(symbol: str, price: float | None = None) -> float | None:
    """Цена круга в пунктах. RI: справка реестра по стакану (10 пт). Прочие:
    две тейкерские комиссии commission.taker_points при известной цене, БЕЗ
    спреда (замера стакана по ним нет). Нет цены = None, числа не выдумываем."""
    from trader.lab.commission import _base_ticker, taker_points
    if _base_ticker(symbol) == "RI":
        return RI_BOOK_COST_PTS
    if not price:
        return None
    return 2 * taker_points(symbol, price, 1)


def report(hypothesis_id: str, symbol: str, window, stat_rows: list[dict], notes: list[str],
           *, n_days: int, halves: dict | None = None, cost_pts: float | None = None,
           atr_min_pts: float | None = None) -> dict:
    """Единый JSON-совместимый результат модуля отпечатка."""
    notes = list(notes)
    if cost_pts is None:
        notes.append("цена круга: источника нет (None)")
    elif not symbol.upper().startswith("RI"):
        notes.append("цена круга: только комиссия commission.py, без спреда")
    return {"id": hypothesis_id, "symbol": symbol, "window": list(window),
            "n_days": n_days, "rows": stat_rows, "halves": halves or {},
            "cost_pts": cost_pts, "atr_min_pts": atr_min_pts, "notes": notes}
