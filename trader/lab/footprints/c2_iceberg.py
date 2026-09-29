"""C2: айсберги — восстановление объёма на лучшей цене после съедания.

Реестр: docs/algo-footprints-registry.md, строка C2. Гипотеза: крупный
участник прячет объём — на лучшей цене P (bid1 или ask1) после «съедания»
видимая часть восстанавливается до прежнего размера. След — серия таких
восстановлений на одной цене, пока она держит best.

ДАННЫЕ. Та же ПОЛНАЯ выжимка стакана (ключи book<КОД>f...), что у C1/C3;
загрузчик `_load_full_book`, разрез полугодий `_halves_book` и справка о
дырах `_segment_ends`/`MAX_GAP_S` (>60 с внутри дня рвёт слежение) —
переиспользуются импортом из c1_book_imbalance, не копируются. `_percentile`
из c1 здесь не нужен: подбор контроля по возрасту сделан точным bisect по
отсортированному времени жизни обычных пробегов (см. `_matched_controls`),
это и есть подбор по квантилям жизни, без отдельного порогового шага.

ОПРЕДЕЛЕНИЯ (docstring реестра C2).
- Слежение — по СТОРОНЕ (bid/ask), не по цене заранее: P = текущий best этой
  стороны. Пробег (`_runs`) — максимальная последовательность снимков с
  неизменной P, разорванная сменой цены или дырой > MAX_GAP_S.
- «Съедание» — между СОСЕДНИМИ снимками пробега объём на P упал на >=
  min_drop лотов ИЛИ на >= 30% (не «и», проверяется соответствие условию
  «не выполнено ни то, ни другое», см. `_refills_in_run`).
- «Восстановление» — в течение refill_s секунд после съедания объём на той
  же P вернулся не ниже 90% значения ДО съедания (не до значения в момент
  съедания — до пикового «pre_qty»).
- Размер восстановленной ПОРЦИИ (видимая доля айсберга) — это ПРИРОСТ объёма
  при восстановлении (qty_после − qty_в_момент_съедания), а не абсолютный
  восстановленный уровень: живой уровень редко съедается до нуля, дискретный
  кусок айсберга виден именно в приросте. Проверено синтетикой теста 2: база
  10 лотов, проваливается до 2, восстанавливается до 10 → порция 8.
- «Кандидат-айсберг» на пробеге — момент, когда накопилось >= R восстановлений
  (R перебирается по arg["min_refills"]); момент обнаружения = ts R-го
  восстановления. Пробег с нулём восстановлений — «обычный» уровень (пул
  контроля Исхода 1).

ИСХОД 1 (защита уровня). «Цена прошла P» проверяется через BEST ПРОТИВОПОЛОЖНОЙ
стороны (не через сам трекаемый бид/аск — тот может просто переставиться):
для айсберга в биде уровень пройден, если ask1 в какой-то момент <= P (весь
рынок сложился ниже бывшего бида); для айсберга в аске — bid1 >= P. Контроль
— обычные (без восстановлений) пробеги той же стороны с СОПОСТАВИМЫМ временем
жизни ДО точки отсчёта: age = ts_обнаружения − ts_начала_пробега; из всех
обычных пробегов берутся с life_s >= age (пережили как минимум этот возраст,
иначе не сопоставимы), ближайшие по life_s к age (это и есть подбор по
квантилям жизни — точка отсчёта контроля берётся на том же возрасте age от
начала ЕГО пробега). См. `_matched_controls`.

ИСХОД 2 (ход mid). Подписанный ход mid(t+T) − mid(t) от момента обнаружения,
знак + для айсберга в биде (ожидание — рост). Нулевое распределение — как в
c1/c3: пул `draws` случайных (день, снимок, случайная сторона) той же формы,
из него бутстрапится нулевое распределение МЕДИАНЫ (`_boot_median`, тот же n,
что у реальной выборки), интервал реальной статистики — бутстрап по дням
(`common.bootstrap_days`). `common.pvalue_and_ci` берётся как есть — эффект
ожидается ПОЛОЖИТЕЛЬНЫМ, инверсии (как в c3 для отката) не нужно.

Дыры > 60 с внутри дня рвут и пробеги (`_runs`), и оба горизонта поиска
(`_segment_ends`/block_end ограничивает и поиск восстановления, и поиск
пересечения/хода через T).
"""
from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common
from trader.lab.footprints.c1_book_imbalance import (
    MAX_GAP_S,
    _halves_book,
    _load_full_book,
    _segment_ends,
)

MIN_DROP_DEFAULT = 5
REFILL_S_DEFAULT = 3.0
MIN_REFILLS_DEFAULT = (3, 5)
HORIZONS_S_DEFAULT = (60, 300)
DRAWS_DEFAULT = 200
MATCH_CAP = 10  # ближайших по времени жизни обычных пробегов на кандидата


# --------------------------------------------------------------------------
# Подготовка: дневные массивы, пробеги, восстановления
# --------------------------------------------------------------------------

def _day_arrays(day_rows: list[tuple]) -> dict:
    ts = [r[0] for r in day_rows]
    bid = [r[1][0] for r in day_rows]  # (price, qty) лучшего бида
    ask = [r[2][0] for r in day_rows]  # (price, qty) лучшего аска
    mid = [(b[0] + a[0]) / 2.0 for b, a in zip(bid, ask)]
    return {"ts": ts, "bid": bid, "ask": ask, "mid": mid, "block_end": _segment_ends(ts)}


def _runs(ts: list[int], best: list[tuple]) -> list[dict]:
    """Пробеги неизменной P: [{price, start, end, start_ts, end_ts}], индексы
    глобальные включительно; разрыв — смена цены ИЛИ дыра > MAX_GAP_S."""
    n = len(ts)
    out = []
    start = 0
    gap_ms = MAX_GAP_S * 1000
    for i in range(1, n + 1):
        broke = i == n or best[i][0] != best[start][0] or (ts[i] - ts[i - 1]) > gap_ms
        if broke:
            out.append({"price": best[start][0], "start": start, "end": i - 1,
                        "start_ts": ts[start], "end_ts": ts[i - 1]})
            start = i
    return out


def _refills_in_run(ts: list[int], qty: list[int], min_drop: int, refill_s: float) -> list[dict]:
    """Восстановления внутри пробега: [{idx (локальный индекс восстановления),
    ts, size}], size = прирост объёма при восстановлении (см. докстринг)."""
    n = len(qty)
    refill_ms = refill_s * 1000
    out = []
    for j in range(1, n):
        drop = qty[j - 1] - qty[j]
        if drop < min_drop and qty[j] > qty[j - 1] * 0.7:
            continue  # ни абсолютный, ни относительный порог не пройден
        pre_qty = qty[j - 1]
        trough_qty = qty[j]
        deadline = ts[j] + refill_ms
        for m in range(j + 1, n):
            if ts[m] > deadline:
                break
            if qty[m] >= 0.9 * pre_qty:
                out.append({"idx": m, "ts": ts[m], "size": qty[m] - trough_qty})
                break
    out.sort(key=lambda e: e["ts"])
    return out


def _side_runs(day: dict, side: str, min_drop: int, refill_s: float) -> list[dict]:
    ts, best = day["ts"], day[side]
    runs = _runs(ts, best)
    for r in runs:
        s, e = r["start"], r["end"]
        if e <= s:
            r["refills"] = []
            continue
        local_ts = ts[s:e + 1]
        local_qty = [q for _, q in best[s:e + 1]]
        refs = _refills_in_run(local_ts, local_qty, min_drop, refill_s)
        for rf in refs:
            rf["idx"] += s  # глобальный индекс
        r["refills"] = refs
    return runs


def _candidates(all_runs: list[dict], min_refills: tuple[int, ...]) -> dict[int, list[dict]]:
    """R -> список кандидатов: {day, price, start, start_ts, detect_idx,
    detect_ts, life_s, refills_total, sizes}."""
    out: dict[int, list[dict]] = {R: [] for R in min_refills}
    for r in all_runs:
        refills = r["refills"]
        if not refills:
            continue
        life_s = (r["end_ts"] - r["start_ts"]) / 1000.0
        for R in min_refills:
            if len(refills) < R:
                continue
            det = refills[R - 1]
            out[R].append({
                "day": r["day"], "price": r["price"], "start": r["start"],
                "start_ts": r["start_ts"], "detect_idx": det["idx"], "detect_ts": det["ts"],
                "life_s": life_s, "refills_total": len(refills),
                "sizes": [x["size"] for x in refills],
            })
    return out


def _regular_runs_sorted(all_runs: list[dict]) -> tuple[list[dict], list[float]]:
    regs = [r for r in all_runs if not r["refills"]]
    regs.sort(key=lambda r: (r["end_ts"] - r["start_ts"]))
    life = [(r["end_ts"] - r["start_ts"]) / 1000.0 for r in regs]
    return regs, life


def _matched_controls(regs_sorted: list[dict], regs_life: list[float],
                       age_s: float, cap: int) -> list[dict]:
    i = bisect.bisect_left(regs_life, age_s)
    return regs_sorted[i:i + cap]


# --------------------------------------------------------------------------
# Исходы: пересечение уровня, ход mid
# --------------------------------------------------------------------------

def _idx_at_or_after(ts: list[int], start_idx: int, target_ms: int, limit_idx: int) -> int | None:
    j = bisect.bisect_left(ts, target_ms, start_idx, limit_idx + 1)
    return j if j <= limit_idx else None


def _crossed(day: dict, side: str, price: float, start_idx: int, horizon_ms: int) -> bool | None:
    """True — best противоположной стороны пересёк P за horizon_ms; False —
    горизонт полностью прожит без пересечения; None — дыра/конец дня раньше
    горизонта (точка не наблюдаема, исключается из доли)."""
    ts = day["ts"]
    limit = day["block_end"][start_idx]
    end_ms = ts[start_idx] + horizon_ms
    opp = day["ask"] if side == "bid" else day["bid"]
    for i in range(start_idx, limit + 1):
        if ts[i] > end_ms:
            return False
        opp_price = opp[i][0]
        if (side == "bid" and opp_price <= price) or (side == "ask" and opp_price >= price):
            return True
    return None if ts[limit] < end_ms else False


def _mid_move(day: dict, side: str, start_idx: int, horizon_ms: int) -> float | None:
    ts, mid = day["ts"], day["mid"]
    limit = day["block_end"][start_idx]
    j = _idx_at_or_after(ts, start_idx, ts[start_idx] + horizon_ms, limit)
    if j is None:
        return None
    sign = 1.0 if side == "bid" else -1.0
    return sign * (mid[j] - mid[start_idx])


def _null_moves(days: dict, horizon_ms: int, draws: int, rng: random.Random) -> list[float]:
    """Нулевой пул: случайный день/снимок/сторона, тот же горизонт (см. c1/c3)."""
    day_list = list(days.values())
    out = []
    for _ in range(draws):
        if not day_list:
            break
        day = day_list[rng.randrange(len(day_list))]
        n = len(day["ts"])
        if n < 2:
            continue
        i = rng.randrange(n)
        side = "bid" if rng.random() < 0.5 else "ask"
        mv = _mid_move(day, side, i, horizon_ms)
        if mv is not None:
            out.append(mv)
    return out


# --------------------------------------------------------------------------
# Статистика строки
# --------------------------------------------------------------------------

def _stat(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def _boot_median(pool: list[float], n: int, draws: int, rng: random.Random) -> list[float]:
    if not pool or not n:
        return []
    return [statistics.median(rng.choices(pool, k=n)) for _ in range(draws)]


def _boot_by_day(by_day: dict, draws: int, rng: random.Random) -> list[float]:
    if not by_day:
        return []
    return common.bootstrap_days(by_day, lambda blocks: _stat([v for b in blocks for v in b]),
                                  draws, rng)


def _row(side: str, R: int, T: int, candidates: list[dict], regs_sorted: list[dict],
         regs_life: list[float], days: dict, draws: int, rng: random.Random) -> dict:
    T_ms = T * 1000
    n_days = len(days)

    crossed_flags: list[bool] = []
    ctrl_flags: list[bool] = []
    real_moves: list[float] = []
    move_by_day: dict = defaultdict(list)

    for c in candidates:
        day = days[c["day"]]
        fl = _crossed(day, side, c["price"], c["detect_idx"], T_ms)
        if fl is not None:
            crossed_flags.append(fl)

        mv = _mid_move(day, side, c["detect_idx"], T_ms)
        if mv is not None:
            real_moves.append(mv)
            move_by_day[c["day"]].append(mv)

        age_s = (c["detect_ts"] - c["start_ts"]) / 1000.0
        for ctrl in _matched_controls(regs_sorted, regs_life, age_s, MATCH_CAP):
            ctrl_day = days[ctrl["day"]]
            ref_idx = _idx_at_or_after(ctrl_day["ts"], ctrl["start"],
                                       ctrl["start_ts"] + int(age_s * 1000),
                                       ctrl_day["block_end"][ctrl["start"]])
            if ref_idx is None:
                continue
            cfl = _crossed(ctrl_day, side, ctrl["price"], ref_idx, T_ms)
            if cfl is not None:
                ctrl_flags.append(cfl)

    null_pool = _null_moves(days, T_ms, draws, rng)
    med = _stat(real_moves)
    pos_share = (sum(1 for v in real_moves if v > 0) / len(real_moves)) if real_moves else None
    null_dist = _boot_median(null_pool, len(real_moves), draws, rng)
    boot_dist = _boot_by_day(dict(move_by_day), draws, rng)

    life_vals = [c["life_s"] for c in candidates]
    refill_vals = [c["refills_total"] for c in candidates]
    sizes = [s for c in candidates for s in c["sizes"]]

    return {
        "side": side, "R": R, "horizon_s": T,
        "n_icebergs": len(candidates),
        "per_day": (len(candidates) / n_days) if n_days else None,
        "refills_median": statistics.median(refill_vals) if refill_vals else None,
        "slice_mode": statistics.mode(sizes) if sizes else None,
        "life_median_s": statistics.median(life_vals) if life_vals else None,
        "share_crossed": (sum(crossed_flags) / len(crossed_flags)) if crossed_flags else None,
        "control_share_crossed": (sum(ctrl_flags) / len(ctrl_flags)) if ctrl_flags else None,
        "move_after": {"median": med, "pos_share": pos_share,
                       "null": common.pvalue_and_ci(med, null_dist, boot_dist)},
    }


# --------------------------------------------------------------------------
# analyze / run
# --------------------------------------------------------------------------

def _atr_minute(rows: list[tuple]) -> float | None:
    buckets: dict = defaultdict(list)
    for ts_ms, bids, asks in rows:
        buckets[ts_ms // 60000].append((bids[0][0] + asks[0][0]) / 2.0)
    ranges = [max(v) - min(v) for v in buckets.values() if len(v) >= 2]
    return statistics.median(ranges) if ranges else None


def analyze(rows: list[tuple], *, min_drop: int = MIN_DROP_DEFAULT,
           refill_s: float = REFILL_S_DEFAULT, min_refills: tuple[int, ...] = MIN_REFILLS_DEFAULT,
           horizons_s: tuple[int, ...] = HORIZONS_S_DEFAULT, draws: int = DRAWS_DEFAULT,
           seed: int = 0) -> dict:
    """rows = [(ts_ms, bids, asks), ...] (см. c1._load_full_book) -> {rows,
    n_days, notes}. rows по (side, R, T) — поля см. docs C2."""
    days_raw: dict = defaultdict(list)
    for r in rows:
        days_raw[common.day_of(r[0] / 1000)].append(r)
    days = {d: _day_arrays(drows) for d, drows in days_raw.items()}

    out_rows = []
    for side in ("bid", "ask"):
        all_runs = []
        for d, day in days.items():
            runs = _side_runs(day, side, min_drop, refill_s)
            for r in runs:
                r["day"] = d
            all_runs.extend(runs)
        regs_sorted, regs_life = _regular_runs_sorted(all_runs)
        cands_by_R = _candidates(all_runs, min_refills)
        for R in min_refills:
            candidates = cands_by_R[R]
            for T in horizons_s:
                rng = random.Random(f"{seed}:{side}:{R}:{T}")
                out_rows.append(_row(side, R, int(T), candidates, regs_sorted, regs_life,
                                     days, draws, rng))
    return {"rows": out_rows, "n_days": len(days), "notes": []}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "book_key" (обязателен), "since",
    "until", "min_drop", "refill_s", "min_refills", "horizons_s", "draws", "seed"}."""
    book_key = arg.get("book_key")
    if not book_key:
        return {"id": "C2", "error": "book_key обязателен"}
    symbol_key = arg.get("symbol_key") or book_key
    since, until = arg.get("since"), arg.get("until")
    rows, dropped = _load_full_book(book_key, since, until)
    if not rows:
        return {"id": "C2", "symbol": symbol_key, "window": [since, until],
                "error": "нет снимков стакана в окне"}

    min_drop = int(arg.get("min_drop", MIN_DROP_DEFAULT))
    refill_s = float(arg.get("refill_s", REFILL_S_DEFAULT))
    min_refills = tuple(int(x) for x in arg.get("min_refills", MIN_REFILLS_DEFAULT))
    horizons_s = tuple(int(x) for x in arg.get("horizons_s", HORIZONS_S_DEFAULT))
    draws = int(arg.get("draws", DRAWS_DEFAULT))
    seed = int(arg.get("seed", 0))

    res = analyze(rows, min_drop=min_drop, refill_s=refill_s, min_refills=min_refills,
                 horizons_s=horizons_s, draws=draws, seed=seed)
    first_rows, second_rows = _halves_book(rows)
    halves = {
        "first": analyze(first_rows, min_drop=min_drop, refill_s=refill_s, min_refills=min_refills,
                         horizons_s=horizons_s, draws=draws, seed=seed)["rows"] if len(first_rows) > 1 else [],
        "second": analyze(second_rows, min_drop=min_drop, refill_s=refill_s, min_refills=min_refills,
                          horizons_s=horizons_s, draws=draws, seed=seed)["rows"] if len(second_rows) > 1 else [],
    }
    notes = [f"снимков отброшено (нет обеих сторон / цена<=0): {dropped}", *res["notes"]]
    last_bid1, last_ask1 = rows[-1][1][0][0], rows[-1][2][0][0]
    last_mid = (last_bid1 + last_ask1) / 2
    return common.report("C2", symbol_key, [since, until], res["rows"], notes,
                         n_days=res["n_days"], halves=halves,
                         cost_pts=common.round_trip_cost_pts(symbol_key, last_mid),
                         atr_min_pts=_atr_minute(rows))
