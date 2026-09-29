"""C1: дисбаланс стакана / microprice против хода mid на 5 с - 5 мин.

Реестр: docs/algo-footprints-registry.md, строка C1. Гипотеза: HFT-маркетмейкеры
двигают цену вслед за дисбалансом заявок, след — дисбаланс L1..k (и microprice)
предсказывает ход mid на коротких горизонтах.

ДАННЫЕ. `common.load_book`/`common.load_bars` тут не годятся: первый ждёт
поминутную выжимку стакана (`book_digest.py`), второй — бары. Этой гипотезе
нужна ПОЛНАЯ частота снимков (`scripts/book_full_digest.py`, ~1/1.2 с, 5
уровней, ключ вида `bookRIZ6f0929`), поэтому загрузчик здесь свой —
`_load_full_book`. Формат строки агентского канала: `[ts_ms, bid1,q1 ...
bid5,q5, ask1,q1 ... ask5,q5]`, bid по убыванию цены, ask по возрастанию,
короткая сторона добита ценой худшего уровня с qty=0 (см. book_full_digest.py).
Время — МСК-стенка как UTC, уже сделано при сборке (тот же сдвиг +3 ч, что и у
book_digest.py), поэтому `common.day_of` применяется без поправок, как в a4.

ГЭПЫ. Дыра > 60 с внутри дня (архив её знает: 25.09 12:44 — 27.09 00:02) рвёт
окно вперёд: если между точкой отсчёта t и первым снимком на t+h есть
внутренний разрыв больше MAX_GAP_S, точка не считается. Реализовано как
`_segment_ends`: для каждого снимка дня — самый дальний индекс, до которого
можно дотянуться без разрыва > MAX_GAP_S; выход искомого t+h снимка за эту
границу (или за пределы дня) — точка отбрасывается, дни не пересекаются
(здесь это не нужно объяснять отдельно: группировка по `common.day_of`).

HALVES. `common.halves` читает `r[0]` как эпоху в СЕКУНДАХ; здесь строки —
`(ts_ms, bids, asks)`, поэтому первая/вторая половина считаются своей
`_halves_book` той же логикой (разрез по целым дням), а не через common.halves.

ПРОРЕЖИВАНИЕ. Если снимков в окне больше `max_rows`, точки ОТСЧЁТА t берутся
через шаг (глобально по хронологии), а ход вперёд всё равно ищется по
ПОЛНОМУ (непрореженному) ряду снимков — иначе ближайший снимок на t+h после
прореживания может не найтись там, где на самом деле есть непрерывные данные.
"""
from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common
from trader.lab.retro_reverse import _epoch, _load_bars

LEVELS = 5
DEPTHS = (1, 3, 5)
HORIZONS_S = (5, 15, 60, 300)
MAX_GAP_S = 60
MAX_ROWS_DEFAULT = 400_000


# --------------------------------------------------------------------------
# Загрузка полной выжимки стакана
# --------------------------------------------------------------------------

def _load_full_book(key: str, since=None, until=None) -> tuple[list[tuple], int]:
    """Ключ полной выжимки (bookXXXfMMDD) -> ([(ts_ms, bids, asks), ...], dropped).

    bids/asks = [(price, qty)] * LEVELS, как их пишет book_full_digest.py. Окно
    since/until — та же ISO-дата/epoch, что у common.load_bars (см. _epoch);
    until-дата включается целиком. Строка без обеих сторон или с ценой <= 0 у
    лучшего уровня пропускается, dropped — их число (для notes, не молча)."""
    raw = _load_bars(key)
    lo, hi = _epoch(since), _epoch(until, end=True)
    dropped = 0
    out: list[tuple] = []
    for r in raw:
        ts_ms = r[0]
        t_s = ts_ms / 1000
        if (lo is not None and t_s < lo) or (hi is not None and t_s >= hi):
            continue
        vals = r[1:]
        if len(vals) < 4 * LEVELS:
            dropped += 1
            continue
        bids = [(float(vals[2 * i]), int(vals[2 * i + 1])) for i in range(LEVELS)]
        off = 2 * LEVELS
        asks = [(float(vals[off + 2 * i]), int(vals[off + 2 * i + 1])) for i in range(LEVELS)]
        if bids[0][0] <= 0 or asks[0][0] <= 0:
            dropped += 1
            continue
        out.append((int(ts_ms), bids, asks))
    out.sort(key=lambda x: x[0])
    return out, dropped


def _halves_book(rows: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """Как common.halves (разрез по целым дням), но по ts_ms — common.halves
    ждёт секунды в r[0], здесь строки (ts_ms, bids, asks)."""
    days = sorted({common.day_of(r[0] / 1000) for r in rows})
    if not days:
        return [], []
    cut = days[len(days) // 2]
    first = [r for r in rows if common.day_of(r[0] / 1000) < cut]
    second = [r for r in rows if common.day_of(r[0] / 1000) >= cut]
    return first, second


def _atr_minute_book(rows: list[tuple]) -> float | None:
    """Справка масштаба: медиана минутного размаха mid (common.atr_minute ждёт
    бары o/h/l/c, здесь тиковый ряд — считаем размах по минутным корзинам)."""
    buckets: dict = defaultdict(list)
    for ts_ms, bids, asks in rows:
        buckets[ts_ms // 60000].append((bids[0][0] + asks[0][0]) / 2)
    ranges = [max(v) - min(v) for v in buckets.values() if len(v) >= 2]
    return statistics.median(ranges) if ranges else None


# --------------------------------------------------------------------------
# Подготовка: mid/spread/microprice/I_k по дням
# --------------------------------------------------------------------------

def _segment_ends(ts_ms: list[int]) -> list[int]:
    """block_end[i] = наибольший j>=i такой, что все соседние разрывы ts[i..j]
    не превышают MAX_GAP_S. Считается справа налево, за один проход."""
    n = len(ts_ms)
    block_end = [0] * n
    if n:
        block_end[n - 1] = n - 1
        gap_ms = MAX_GAP_S * 1000
        for i in range(n - 2, -1, -1):
            block_end[i] = i if ts_ms[i + 1] - ts_ms[i] > gap_ms else block_end[i + 1]
    return block_end


def _prep(rows: list[tuple], depths) -> dict:
    """rows -> {день: {ts, mid, spread, micro_i, I: {k: [...]}, block_end}}."""
    days: dict = defaultdict(list)
    for r in rows:
        days[common.day_of(r[0] / 1000)].append(r)
    out = {}
    for d, drows in days.items():
        ts, mid, spread, micro_i = [], [], [], []
        depth_i = {k: [] for k in depths}
        for ts_ms, bids, asks in drows:
            bid1, qb1 = bids[0]
            ask1, qa1 = asks[0]
            m = (bid1 + ask1) / 2
            sp = ask1 - bid1
            ts.append(ts_ms)
            mid.append(m)
            spread.append(sp)
            mp = (bid1 * qa1 + ask1 * qb1) / (qb1 + qa1) if (qb1 + qa1) else None
            micro_i.append((mp - m) / sp if (mp is not None and sp > 0) else None)
            for k in depths:
                sb = sum(q for _, q in bids[:k])
                sa = sum(q for _, q in asks[:k])
                depth_i[k].append((sb - sa) / (sb + sa) if (sb + sa) else None)
        out[d] = {"ts": ts, "mid": mid, "spread": spread, "micro_i": micro_i,
                  "I": depth_i, "block_end": _segment_ends(ts)}
    return out


def _pairs(days_data: dict, k, h_s: int, anchor_step: int) -> tuple[list, list, list, list]:
    """(I, ход, spread_в_t, день) для валидных точек отсчёта k/h.

    Точки отсчёта t прорежены шагом anchor_step (глобально, по хронологии);
    сам ход ищется по полному ряду снимков дня (bisect по ts)."""
    Is: list = []
    moves: list = []
    spreads: list = []
    dlabels: list = []
    gi = 0
    h_ms = h_s * 1000
    for d, dd in days_data.items():
        ts = dd["ts"]
        mid = dd["mid"]
        block_end = dd["block_end"]
        series = dd["micro_i"] if k == "micro" else dd["I"][k]
        n = len(ts)
        for i in range(n):
            take = gi % anchor_step == 0
            gi += 1
            if not take:
                continue
            iv = series[i]
            if iv is None:
                continue
            j = bisect.bisect_left(ts, ts[i] + h_ms, i)
            if j >= n or j > block_end[i]:
                continue
            Is.append(iv)
            moves.append(mid[j] - mid[i])
            spreads.append(dd["spread"][i])
            dlabels.append(d)
    return Is, moves, spreads, dlabels


# --------------------------------------------------------------------------
# Статистика
# --------------------------------------------------------------------------

def _percentile(sorted_vals: list[float], q: float) -> float:
    # ponytail: тот же приём, что common._pct, но common._pct приватный —
    # не переиспользуем internal чужого модуля, две строки дешевле импорта.
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[idx]


def _safe_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def _sign_agree(Is: list[float], moves: list[float]) -> float | None:
    if not Is:
        return None
    agree = sum(1 for iv, mv in zip(Is, moves) if (iv > 0 and mv > 0) or (iv < 0 and mv < 0))
    return agree / len(Is)


def _top_quintile_signed_moves(Is: list[float], moves: list[float], dlabels: list) -> list[float]:
    """sign(I)*ход в точках, где |I| в верхнем квинтиле СВОЕГО дня (порог по
    дню, отбор и медиана — пулом по всем дням)."""
    by_day: dict = defaultdict(list)
    for i, d in enumerate(dlabels):
        by_day[d].append(i)
    out = []
    for idxs in by_day.values():
        thr = _percentile(sorted(abs(Is[i]) for i in idxs), 0.8)
        for i in idxs:
            if abs(Is[i]) >= thr:
                out.append((1 if Is[i] > 0 else -1 if Is[i] < 0 else 0) * moves[i])
    return out


def _corr_stat(blocks: list[list[tuple]]) -> float | None:
    xs = [x[0] for blk in blocks for x in blk]
    ys = [x[1] for blk in blocks for x in blk]
    return _safe_corr(xs, ys)


def _median_stat(blocks: list[list[tuple]]) -> float | None:
    out = []
    for blk in blocks:
        if not blk:
            continue
        thr = _percentile(sorted(abs(x[0]) for x in blk), 0.8)
        out.extend((1 if x[0] > 0 else -1 if x[0] < 0 else 0) * x[1] for x in blk if abs(x[0]) >= thr)
    return statistics.median(out) if out else None


def analyze(rows: list[tuple], *, depths=DEPTHS, horizons_s=HORIZONS_S,
           draws: int = 200, seed: int = 0, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """rows = [(ts_ms, bids, asks), ...] (см. _load_full_book) -> {rows, n_days, notes}.

    rows по (k, h): k пробегает depths и отдельно "micro" (I_micro у L1). Поля —
    как в реестре: n, corr, null_corr (перетасовка I внутри дня + бутстрап по
    дням через common.*), median_signed_move_top_quintile, null_move (тот же
    контроль), sign_agree, half_spread_median."""
    days_data = _prep(rows, depths)
    total = sum(len(dd["ts"]) for dd in days_data.values())
    step = max(1, -(-total // max_rows)) if max_rows else 1
    out_rows = []
    for k in list(depths) + ["micro"]:
        for h in horizons_s:
            h = int(h)
            rng = random.Random(f"{seed}:{k}:{h}")
            Is, moves, spreads, dlabels = _pairs(days_data, k, h, step)
            real_corr = _safe_corr(Is, moves)
            real_med_list = _top_quintile_signed_moves(Is, moves, dlabels)
            real_med = statistics.median(real_med_list) if real_med_list else None

            null_corr, null_move = [], []
            for _ in range(draws):
                shuf = common.shuffle_within_day(Is, dlabels, rng)
                null_corr.append(_safe_corr(shuf, moves))
                nm = _top_quintile_signed_moves(shuf, moves, dlabels)
                null_move.append(statistics.median(nm) if nm else None)

            rows_by_day: dict = defaultdict(list)
            for iv, mv, d in zip(Is, moves, dlabels):
                rows_by_day[d].append((iv, mv))
            boot_corr = common.bootstrap_days(rows_by_day, _corr_stat, draws, rng)
            boot_move = common.bootstrap_days(rows_by_day, _median_stat, draws, rng)

            out_rows.append({
                "k": k, "horizon_s": h, "n": len(Is),
                "corr": real_corr,
                "null_corr": common.pvalue_and_ci(real_corr, null_corr, boot_corr),
                "median_signed_move_top_quintile": real_med,
                "null_move": common.pvalue_and_ci(real_med, null_move, boot_move),
                "sign_agree": _sign_agree(Is, moves),
                "half_spread_median": (statistics.median(spreads) / 2) if spreads else None,
            })
    notes = [f"прореживание точек отсчёта: каждый {step}-й снимок (max_rows={max_rows})"] if step > 1 else []
    return {"rows": out_rows, "n_days": len(days_data), "notes": notes}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "book_key" (обязателен), "since",
    "until", "depths", "horizons_s", "draws", "seed", "max_rows"}."""
    book_key = arg.get("book_key")
    if not book_key:
        return {"id": "C1", "error": "book_key обязателен"}
    symbol_key = arg.get("symbol_key") or book_key
    since, until = arg.get("since"), arg.get("until")
    rows, dropped = _load_full_book(book_key, since, until)
    if not rows:
        return {"id": "C1", "symbol": symbol_key, "window": [since, until],
                "error": "нет снимков стакана в окне"}

    depths = tuple(int(x) for x in arg.get("depths", DEPTHS))
    horizons = tuple(int(x) for x in arg.get("horizons_s", HORIZONS_S))
    draws = int(arg.get("draws", 200))
    seed = int(arg.get("seed", 0))
    max_rows = int(arg.get("max_rows", MAX_ROWS_DEFAULT))

    res = analyze(rows, depths=depths, horizons_s=horizons, draws=draws, seed=seed, max_rows=max_rows)
    first_rows, second_rows = _halves_book(rows)
    halves = {
        "first": analyze(first_rows, depths=depths, horizons_s=horizons, draws=draws,
                         seed=seed, max_rows=max_rows)["rows"] if len(first_rows) > 1 else [],
        "second": analyze(second_rows, depths=depths, horizons_s=horizons, draws=draws,
                          seed=seed, max_rows=max_rows)["rows"] if len(second_rows) > 1 else [],
    }
    notes = [f"снимков отброшено (нет обеих сторон / цена<=0): {dropped}", *res["notes"],
             "halves — свой разрез _halves_book (common.halves ждёт секунды, здесь ts_ms)"]
    last_bid1, last_ask1 = rows[-1][1][0][0], rows[-1][2][0][0]
    last_mid = (last_bid1 + last_ask1) / 2
    return common.report("C1", symbol_key, [since, until], res["rows"], notes,
                         n_days=res["n_days"], halves=halves,
                         cost_pts=common.round_trip_cost_pts(symbol_key, last_mid),
                         atr_min_pts=_atr_minute_book(rows))
