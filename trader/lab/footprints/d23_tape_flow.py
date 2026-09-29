"""D2 (крупный принт) и D3 (подписанный поток OFI) по ленте сделок.

Реестр: docs/algo-footprints-registry.md, раздел D. Лента — единственный
источник с посделочным разрешением (метки секундные: ts_ms кратны 1000).
Загрузчик отдаёт словарь целиком (`code`, `ts_unit`, `side_buy`, `rows`), а
не только `rows`, поэтому `common.load_bars` не годится — свой `_fetch` тем
же HTTP-путём (`retro_reverse._load_bars`), см. `_prepare`.

СТОРОНА. `side_buy` — значение поля `side` для покупки (см. tape_digest.py).
Если пусто (None), сторона восстанавливается по тику: покупка, если цена
сделки >= цены предыдущей сделки, иначе продажа; первая сделка окна без
предыдущей цены считается покупкой (одна сделка на весь ряд, не влияет на
статистику).

ГОРИЗОНТ t+h. Секундное разрешение: h считается от последней сделки СЕКУНДЫ
t (t — секунда события) до последней сделки первой секунды >= t+h. Для D2
"price" в формуле — собственная цена события (крупной/контрольной сделки),
не "последняя цена секунды t" — так однозначно и не требует лишнего
поиска. Для D3 та же логика на минутах: h должен быть кратен 60 (иначе
горизонт в докладе как "1/5 минут" не имеет смысла), минуты без сделок
пропускаются молча только в подборе следующей минуты с данными (bisect),
не в счёте.

Внутреннее представление сделки после `_prepare`: `[ts_s, price, qty,
is_buy]` (ts в СЕКУНДАХ, is_buy 1/0) — тот же индекс времени r[0], что и у
барных модулей, поэтому `common.day_of/minute_of_day/by_day` переиспользуются
без изменений. Ряд уже отсортирован по времени источником (tape_digest.py);
здесь заново не сортируется.

КОНТРОЛЬ сохраняет структуру дня (перетасовка `common.shuffle_within_day`
внутри дня), см. докстринг common.py.
"""
from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common


def _fetch(key: str) -> dict:
    """Тот же HTTP-путь, что retro_reverse._load_bars, но лента отдаёт весь
    словарь (rows + side_buy + code), а не только rows."""
    import os
    import httpx
    api = os.environ.get("STL_API", "https://stl.shectory.ru").rstrip("/")
    r = httpx.get(f"{api}/api/v1/agent/bars/{key}",
                  headers={"X-Agent-Token": os.environ.get("OPT_AGENT_TOKEN", "")}, timeout=300)
    r.raise_for_status()
    return r.json()


def _prepare(data: dict) -> tuple[list[list], list[str]]:
    """Сырые строки ленты [ts, price, qty, side] -> [ts_s, price, qty, is_buy]."""
    notes = []
    raw = data.get("rows") or []
    div = 1000 if data.get("ts_unit", "ms") == "ms" else 1
    side_buy = data.get("side_buy")
    tick_fallback = side_buy is None
    if tick_fallback:
        notes.append("side_buy пуст: сторона по тику (цена >= предыдущей = покупка)")
    rows: list[list] = []
    prev_price = None
    for ts, price, qty, side in raw:
        price = float(price)
        if tick_fallback:
            is_buy = 1 if (prev_price is None or price >= prev_price) else 0
        else:
            is_buy = 1 if side == side_buy else 0
        rows.append([int(ts) // div, price, int(qty), is_buy])
        prev_price = price
    return rows, notes


def _pct(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))]


def _halves_rows(rows: list[list]) -> tuple[list[list], list[list]]:
    """Один день -> раздел по времени (до/после 16:00). Несколько дней ->
    common.halves (по целым дням)."""
    days = {common.day_of(r[0]) for r in rows}
    if len(days) <= 1:
        cut = 16 * 60
        first = [r for r in rows if common.minute_of_day(r[0]) < cut]
        second = [r for r in rows if common.minute_of_day(r[0]) >= cut]
        return first, second
    return common.halves(rows)


# ---------------------------------------------------------------- D2 ----


def _second_index(day_rows: list[list]) -> tuple[list[int], dict[int, float]]:
    """(отсортированные секунды с сделками, секунда -> цена последней сделки)."""
    idx: dict[int, float] = {}
    for ts_s, price, _qty, _buy in day_rows:
        idx[ts_s] = price
    return sorted(idx), idx


def _price_at_or_after(secs_sorted: list[int], idx: dict[int, float], target: int) -> float | None:
    i = bisect.bisect_left(secs_sorted, target)
    return idx[secs_sorted[i]] if i < len(secs_sorted) else None


def _day_thresholds(day_rows: list[list], big_q: float) -> tuple[float, float, float]:
    qtys = sorted(r[2] for r in day_rows)
    if not qtys:
        return 0.0, 0.0, 0.0
    return _pct(qtys, big_q), _pct(qtys, 0.40), _pct(qtys, 0.60)


def _big_and_control(day_rows: list[list], q_big: float, q40: float, q60: float) -> tuple[list[int], list[int]]:
    """Индексы крупных сделок и контроля (40-60 перцентиль, ТЕ ЖЕ минуты, что и крупные)."""
    big_idx = [i for i, r in enumerate(day_rows) if r[2] >= q_big]
    big_minutes = {common.minute_of_day(day_rows[i][0]) for i in big_idx}
    ctrl_idx = [i for i, r in enumerate(day_rows)
                if q40 <= r[2] <= q60 and common.minute_of_day(r[0]) in big_minutes]
    return big_idx, ctrl_idx


def _signed_moves(day_rows: list[list], idx_list: list[int],
                   secs_sorted: list[int], sec_idx: dict[int, float], h: int) -> list[float]:
    out = []
    for i in idx_list:
        ts_s, price, _qty, is_buy = day_rows[i]
        target = _price_at_or_after(secs_sorted, sec_idx, ts_s + h)
        if target is None:
            continue
        sign = 1.0 if is_buy else -1.0
        out.append(sign * (target - price))
    return out


def _shuffle_qty(day_rows: list[list], day_label, rng: random.Random) -> list[list]:
    qtys = common.shuffle_within_day([r[2] for r in day_rows], [day_label] * len(day_rows), rng)
    return [[r[0], r[1], q, r[3]] for r, q in zip(day_rows, qtys)]


def _d2_boot_median(blocks: list[list], big_q: float, h: int) -> float | None:
    signed = []
    for r in blocks:
        secs_sorted, sec_idx = _second_index(r)
        big_idx, _ = _big_and_control(r, *_day_thresholds(r, big_q))
        signed += _signed_moves(r, big_idx, secs_sorted, sec_idx, h)
    return statistics.median(signed) if signed else None


def _d2_rows(days_rows: dict, big_q: float, horizons_s, draws: int, seed: int) -> list[dict]:
    if not days_rows:
        return []
    rng = random.Random(seed)
    thresholds = {d: _day_thresholds(r, big_q) for d, r in days_rows.items()}
    sec_indexes = {d: _second_index(r) for d, r in days_rows.items()}

    rows = []
    for h in horizons_s:
        real_signed, real_ctrl, n_big = [], [], 0
        for d, r in days_rows.items():
            secs_sorted, sec_idx = sec_indexes[d]
            big_idx, ctrl_idx = _big_and_control(r, *thresholds[d])
            n_big += len(big_idx)
            real_signed += _signed_moves(r, big_idx, secs_sorted, sec_idx, h)
            real_ctrl += _signed_moves(r, ctrl_idx, secs_sorted, sec_idx, h)
        real_med = statistics.median(real_signed) if real_signed else None
        real_pos = (sum(1 for x in real_signed if x > 0) / len(real_signed)) if real_signed else None
        ctrl_med = statistics.median(real_ctrl) if real_ctrl else None

        null = []
        for _ in range(draws):
            signed = []
            for d, r in days_rows.items():
                shuf = _shuffle_qty(r, d, rng)
                big_idx2, _ = _big_and_control(shuf, *thresholds[d])
                secs_sorted, sec_idx = sec_indexes[d]
                signed += _signed_moves(shuf, big_idx2, secs_sorted, sec_idx, h)
            null.append(statistics.median(signed) if signed else None)

        boot = common.bootstrap_days(days_rows, lambda blocks, h=h: _d2_boot_median(blocks, big_q, h),
                                      draws, rng)
        rows.append({
            "test": "D2", "h": h, "n_big": n_big, "median_signed": real_med,
            "pos_share": real_pos, "control_median": ctrl_med,
            "null": common.pvalue_and_ci(real_med, null, boot),
        })
    return rows


# ---------------------------------------------------------------- D3 ----


def _minute_index(day_rows: list[list]) -> tuple[dict[int, float], dict[int, float]]:
    """минута дня -> (OFI = сумма buy qty - сумма sell qty, цена последней сделки минуты)."""
    ofi: dict[int, float] = {}
    last_price: dict[int, float] = {}
    for ts_s, price, qty, is_buy in day_rows:
        m = common.minute_of_day(ts_s)
        ofi[m] = ofi.get(m, 0.0) + (qty if is_buy else -qty)
        last_price[m] = price
    return ofi, last_price


def _minute_price_at_or_after(minutes_sorted: list[int], last_price: dict[int, float], target: int) -> float | None:
    i = bisect.bisect_left(minutes_sorted, target)
    return last_price[minutes_sorted[i]] if i < len(minutes_sorted) else None


def _d3_day_pairs(day_rows: list[list], h_min: int) -> list[tuple[int, float, float]]:
    """(минута, OFI, сырой ход цены к минуте >= m+h_min) по дню, только где ход определён."""
    ofi, last_price = _minute_index(day_rows)
    minutes_sorted = sorted(last_price)
    out = []
    for m in sorted(ofi):
        mv = _minute_price_at_or_after(minutes_sorted, last_price, m + h_min)
        if mv is not None:
            out.append((m, ofi[m], mv - last_price[m]))
    return out


def _d3_corr_and_topmed(day_labels: list, ofi_vals: list[float], move_vals: list[float]) -> tuple[float | None, float | None]:
    by_day_abs: dict = defaultdict(list)
    for d, o in zip(day_labels, ofi_vals):
        by_day_abs[d].append(abs(o))
    thr = {d: _pct(sorted(v), 0.90) for d, v in by_day_abs.items()}
    top_signed = []
    for d, o, mv in zip(day_labels, ofi_vals, move_vals):
        if abs(o) >= thr[d]:
            sign = 1.0 if o > 0 else (-1.0 if o < 0 else 0.0)
            top_signed.append(sign * mv)
    corr = None
    if len(ofi_vals) >= 2 and len(set(ofi_vals)) > 1 and len(set(move_vals)) > 1:
        corr = statistics.correlation(ofi_vals, move_vals)
    topmed = statistics.median(top_signed) if top_signed else None
    return corr, topmed


def _d3_boot_stat(blocks: list[list], which: str) -> float | None:
    day_labels, ofi_vals, move_vals = [], [], []
    for i, blk in enumerate(blocks):
        for m, o, mv in blk:
            day_labels.append(i)
            ofi_vals.append(o)
            move_vals.append(mv)
    corr, topmed = _d3_corr_and_topmed(day_labels, ofi_vals, move_vals)
    return corr if which == "corr" else topmed


def _d3_rows(days_rows: dict, horizons_s, draws: int, seed: int) -> list[dict]:
    if not days_rows:
        return []
    rng = random.Random(seed)
    h_minutes = sorted({h // 60 for h in horizons_s if h >= 60 and h % 60 == 0})

    rows = []
    for h_min in h_minutes:
        pairs_by_day = {d: _d3_day_pairs(r, h_min) for d, r in days_rows.items()}
        day_labels, ofi_vals, move_vals = [], [], []
        for d, pairs in pairs_by_day.items():
            for m, o, mv in pairs:
                day_labels.append(d)
                ofi_vals.append(o)
                move_vals.append(mv)
        real_corr, real_top = _d3_corr_and_topmed(day_labels, ofi_vals, move_vals)

        null_corr, null_move = [], []
        for _ in range(draws):
            shuf_ofi = common.shuffle_within_day(ofi_vals, day_labels, rng)
            c, t = _d3_corr_and_topmed(day_labels, shuf_ofi, move_vals)
            null_corr.append(c)
            null_move.append(t)

        boot_corr = common.bootstrap_days(pairs_by_day, lambda b: _d3_boot_stat(b, "corr"), draws, rng)
        boot_move = common.bootstrap_days(pairs_by_day, lambda b: _d3_boot_stat(b, "move"), draws, rng)

        rows.append({
            "test": "D3", "h": h_min * 60, "n_min": len(ofi_vals), "corr": real_corr,
            "median_signed_top_decile": real_top,
            "null_corr": common.pvalue_and_ci(real_corr, null_corr, boot_corr),
            "null_move": common.pvalue_and_ci(real_top, null_move, boot_move),
        })
    return rows


# ------------------------------------------------------------ analyze ----


def _atr_min_pts(rows: list[list]) -> float | None:
    """Медиана размаха цены сделок (max-min) за минуту, по всем дням окна."""
    lo: dict[tuple, float] = {}
    hi: dict[tuple, float] = {}
    for ts_s, price, _qty, _buy in rows:
        key = (common.day_of(ts_s), common.minute_of_day(ts_s))
        lo[key] = min(lo.get(key, price), price)
        hi[key] = max(hi.get(key, price), price)
    ranges = [hi[k] - lo[k] for k in hi]
    return statistics.median(ranges) if ranges else None


def analyze(rows: list[list], big_q: float = 0.99, horizons_s=(10, 60, 300),
            draws: int = 200, seed: int = 0) -> dict:
    days_rows = common.by_day(rows)
    first, second = _halves_rows(rows)

    notes = ["D2: price = собственная цена сделки; last(t+h) = цена последней сделки "
             "в первой секунде >= t+h"]
    skipped = sorted(h for h in horizons_s if not (h >= 60 and h % 60 == 0))
    if skipped:
        notes.append(f"D3 пропущены горизонты не кратные минуте: {skipped}")
    if len(days_rows) <= 1:
        notes.append("один день: половины по времени дня (до/после 16:00), не по дням")

    return {
        "rows": _d2_rows(days_rows, big_q, horizons_s, draws, seed)
                + _d3_rows(days_rows, horizons_s, draws, seed),
        "halves": {
            "first": _d2_rows(common.by_day(first), big_q, horizons_s, draws, seed)
                     + _d3_rows(common.by_day(first), horizons_s, draws, seed),
            "second": _d2_rows(common.by_day(second), big_q, horizons_s, draws, seed)
                      + _d3_rows(common.by_day(second), horizons_s, draws, seed),
        },
        "n_days": len(days_rows),
        "notes": notes,
    }


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "tape_key" (обязателен), "big_q",
    "horizons_s", "draws", "seed"}."""
    tape_key = arg["tape_key"]
    symbol_key = arg.get("symbol_key") or tape_key
    data = _fetch(tape_key)
    if not data.get("rows"):
        return {"id": "D23", "symbol": symbol_key, "window": [None, None], "error": "нет сделок в tape_key"}

    rows, prep_notes = _prepare(data)
    big_q = float(arg.get("big_q", 0.99))
    horizons_s = tuple(arg.get("horizons_s", (10, 60, 300)))
    draws = int(arg.get("draws", 200))
    seed = int(arg.get("seed", 0))

    res = analyze(rows, big_q, horizons_s, draws, seed)
    symbol = data.get("code") or symbol_key
    last_price = rows[-1][1]
    days = sorted({common.day_of(r[0]) for r in rows})
    window = [str(days[0]), str(days[-1])] if days else [None, None]

    return common.report("D23", symbol, window, res["rows"], prep_notes + res["notes"],
                         n_days=res["n_days"], halves=res["halves"],
                         cost_pts=common.round_trip_cost_pts(symbol, last_price),
                         atr_min_pts=_atr_min_pts(rows))
