"""C3: спуфинг и layering — крупные заявки на 2-5 уровне, снятые до касания.

Гипотеза и определения: docs/algo-footprints-registry.md, строка C3. Данные —
ПОЛНАЯ выжимка стакана (ключи book<КОД>f...), НЕ минутный дайджест
book_replay.load_digest: метка здесь в МИЛЛИСЕКУНДАХ и частота ~1 снимок/1.2 с
против 1/мин у дайджеста, common.load_book/halves на неё не рассчитаны
(докстринг common.py прямо это оговаривает) — свой загрузчик и свои day/halves.

Появление: на снимке t на уровне 2..5 (не топ книги) объём qty >= K*q_med
(q_med — медиана объёма уровней 2..5 обеих сторон ЗА ДЕНЬ), а на предыдущем
снимке объём по ТОЙ ЖЕ ЦЕНЕ был < K*q_med/2 либо цены не было среди 5 уровней.
Слежение — по цене, не по номеру уровня (уровень плавает вместе с книгой).

Исход слежения (сканирование вперёд от появления, не дальше T секунд,
проверки строго в этом порядке приоритета — тест C3 проверяет именно его):
  1. touched     — best той же стороны дошёл до цены P СО СВОЕЙ стороны
                   (bid1<=P — упал до заявки, ask1>=P — вырос до неё; P и
                   так между best и глубиной книги, поэтому не наоборот),
                   включая исполнение заявки. Проверяется ПЕРВОЙ: тронутая
                   стена не считается снятой, даже если её объём тоже упал.
  2. left_window — цена P исчезла из 5 уровней (ушла вглубь книги).
  3. pulled      — объём по P упал ниже половины ПИКОВОГО, пока не тронуто и
                   не ушло из окна.
  4. outlived    — ни одно из первых трёх не случилось за T секунд (в т.ч.
                   если данные дня/сегмента кончились раньше).
left_window и настоящий outlived репортятся одной долей share_outlived —
регистр просит только три доли (n_appear = pulled + touched + outlived).

Ход mid считается ТОЛЬКО для исхода pulled — это ядро гипотезы: во время
жизни заявки mid(pulled) - mid(appear), после снятия mid(pulled+T) -
mid(pulled), оба со знаком (+ крупный бид толкает вверх, - крупный аск вниз).

Контроль: `draws` случайных (день, снимок, случайная сторона) С ТЕМИ ЖЕ
elapsed/T, что у конкретного реального pulled-события бакета (регистр требует
совпадение продолжительностей) — пул случайных ходов; нулевое РАСПРЕДЕЛЕНИЕ
МЕДИАН строится вторым бутстрапом того же размера, что реальная выборка
(`_boot_median`), интервал — `common.bootstrap_days` по дням реальных pulled.

Дыры > 60 с внутри дня рвут именно СЛЕЖЕНИЕ (`_segments`): появление на
первом снимке после дыры не сравнивается со снимком до неё, активное
слежение не перескакивает дыру (падает в outlived вместе с концом дня).
Поиск mid(pulled+T) для move_after — просто ближайший снимок ДНЯ по времени,
дыру не проверяет: это справочный ход цены, не слежение за конкретной
заявкой.

Нестандартные решения:
- appearance/resolve считаются один раз на (K, T) для ВСЕХ уровней сразу;
  level_group ("2-3"/"4-5") — фильтр событий по рангу на момент появления,
  а не отдельный проход (одинаковый список появлений тратился бы дважды).
- move_after ожидается ОТРИЦАТЕЛЬНЫМ (откат) — common.pvalue_and_ci всегда
  тестирует «нуль >= настоящего» (эффект неожиданно ВЫСОКИЙ). Для move_after
  это неверная сторона, поэтому `_pvalue_lower` инвертирует знак перед
  вызовом и возвращает результат обратно в исходный масштаб (ci переставляет
  местами при инверсии). move_during использует common.pvalue_and_ci как есть
  — там ожидаемый эффект положителен по построению знака.
"""
from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common
from trader.lab.retro_reverse import _epoch, _load_bars

GAP_S = 60.0
LEVELS = 5


def _qty_at(levels, price, tol: float = 1e-6):
    for p, q in levels:
        if abs(p - price) < tol:
            return q
    return None


def _mid(snap) -> float:
    return (snap[1][0][0] + snap[2][0][0]) / 2.0


def _load_full_book(key: str, since=None, until=None) -> tuple[list[tuple], int]:
    """Ключ полной выжимки стакана -> [(ts_ms, bids, asks), ...], отсортировано.

    Строка канала: [ts_ms, bid1,q1..bid5,q5, ask1,q1..ask5,q5], метка в
    МИЛЛИСЕКУНДАХ. Окно since/until фильтруется по ts_ms // 1000, как
    common.load_bars. Битые строки пропускаются, счётчик возвращается.
    """
    lo, hi = _epoch(since), _epoch(until, end=True)
    out: list[tuple] = []
    bad = 0
    for r in _load_bars(key):
        try:
            ts_ms = int(r[0])
            ts_s = ts_ms // 1000
            if (lo is not None and ts_s < lo) or (hi is not None and ts_s >= hi):
                continue
            vals = r[1:]
            bids = [(float(vals[2 * i]), int(vals[2 * i + 1])) for i in range(LEVELS)]
            off = 2 * LEVELS
            asks = [(float(vals[off + 2 * i]), int(vals[off + 2 * i + 1])) for i in range(LEVELS)]
        except (IndexError, TypeError, ValueError):
            bad += 1
            continue
        if bids[0][0] <= 0 or asks[0][0] <= 0:
            bad += 1
            continue
        out.append((ts_ms, bids, asks))
    out.sort(key=lambda s: s[0])
    return out, bad


def _by_day(snaps: list[tuple]) -> dict:
    days: dict = defaultdict(list)
    for s in snaps:
        days[common.day_of(s[0] // 1000)].append(s)
    return dict(days)


def _segments(day_snaps: list[tuple]) -> list[list[tuple]]:
    """Снимки дня, разрезанные по дырам > GAP_S с (слежение их не пересекает)."""
    segs, cur, prev = [], [], None
    for s in day_snaps:
        if prev is not None and (s[0] - prev) / 1000.0 > GAP_S:
            segs.append(cur)
            cur = []
        cur.append(s)
        prev = s[0]
    if cur:
        segs.append(cur)
    return segs


def _q_med(day_snaps: list[tuple]) -> float:
    vals = []
    for _, bids, asks in day_snaps:
        vals.extend(q for _, q in bids[1:LEVELS])
        vals.extend(q for _, q in asks[1:LEVELS])
    return statistics.median(vals) if vals else 0.0


def _appearances(segs: list[list[tuple]], k: float, q_med: float) -> list[dict]:
    thr_hi, thr_lo = k * q_med, k * q_med / 2.0
    out = []
    for seg in segs:
        for i in range(1, len(seg)):
            prev, cur = seg[i - 1], seg[i]
            for side, idx in (("bid", 1), ("ask", 2)):
                cur_levels, prev_levels = cur[idx], prev[idx]
                for lvl in range(1, LEVELS):          # индекс 1..4 == уровень 2..5
                    price, qty = cur_levels[lvl]
                    if qty < thr_hi:
                        continue
                    prev_qty = _qty_at(prev_levels, price)
                    if prev_qty is not None and prev_qty >= thr_lo:
                        continue
                    out.append({"seg": seg, "i": i, "side": side, "level": lvl + 1,
                                "price": price, "vol": qty, "mid_appear": _mid(cur),
                                "t_appear": cur[0]})
    return out


def _resolve(ev: dict, t_limit: float) -> dict:
    seg, side, price = ev["seg"], ev["side"], ev["price"]
    idx = 1 if side == "bid" else 2
    peak = ev["vol"]
    for j in range(ev["i"] + 1, len(seg)):
        snap = seg[j]
        elapsed = (snap[0] - ev["t_appear"]) / 1000.0
        if elapsed > t_limit:
            break
        best = snap[idx][0][0]
        # P (уровень 2-5) структурно ПО ТУ СТОРОНУ от best (bid: P<=best,
        # ask: P>=best) в момент появления. «Дошёл» = best дотянулся до P
        # СО СВОЕЙ стороны: для бида упал до P или ниже, для аска вырос до P
        # или выше — не наоборот (иначе «дошёл» было бы истинно с первого
        # же снимка, P и так между best и глубиной книги).
        touched = best <= price if side == "bid" else best >= price
        if touched:
            return {"outcome": "touched", "elapsed": elapsed}
        qty = _qty_at(snap[idx], price)
        if qty is None:
            return {"outcome": "left_window", "elapsed": elapsed}
        peak = max(peak, qty)
        if qty < peak / 2.0:
            return {"outcome": "pulled", "elapsed": elapsed, "mid": _mid(snap), "ts": snap[0]}
    return {"outcome": "outlived", "elapsed": t_limit}


def _mid_at_or_after(ts_list, mid_list, target_ms):
    i = bisect.bisect_left(ts_list, target_ms)
    return mid_list[i] if i < len(ts_list) else None


def _controls(rng: random.Random, pulled: list[dict], day_ts: dict, day_mid: dict,
              draws: int) -> tuple[list[float], list[float]]:
    """Пул случайных (день, снимок, случайная сторона) с elapsed/T реального
    pulled-события бакета, розыгрышей `draws` (см. докстринг модуля)."""
    if not pulled:
        return [], []
    during, after = [], []
    for _ in range(draws):
        p = pulled[rng.randrange(len(pulled))]
        ts_list, mid_list = day_ts.get(p["day"]), day_mid.get(p["day"])
        if not ts_list or len(ts_list) < 2:
            continue
        i0 = rng.randrange(len(ts_list))
        start_ts, start_mid = ts_list[i0], mid_list[i0]
        sign = 1.0 if rng.random() < 0.5 else -1.0
        m1 = _mid_at_or_after(ts_list, mid_list, start_ts + int(p["elapsed"] * 1000))
        if m1 is None:
            continue
        during.append(sign * (m1 - start_mid))
        m2 = _mid_at_or_after(ts_list, mid_list, start_ts + int((p["elapsed"] + p["t"]) * 1000))
        if m2 is not None:
            after.append(sign * (m2 - m1))
    return during, after


def _stat(vals: list[float]):
    return statistics.median(vals) if vals else None


def _boot(by_day: dict, draws: int, rng: random.Random) -> list:
    if not by_day:
        return []
    return common.bootstrap_days(
        by_day, lambda blocks: _stat([v for b in blocks for v in b]), draws, rng)


def _boot_median(pool: list[float], n: int, draws: int, rng: random.Random) -> list[float]:
    if not pool or not n:
        return []
    return [statistics.median(rng.choices(pool, k=n)) for _ in range(draws)]


def _pvalue_lower(real, nulls: list[float], boots: list[float]) -> dict:
    """common.pvalue_and_ci тестирует «нуль >= настоящего»; здесь нужна
    обратная сторона (см. докстринг модуля). Инверсия знака туда-обратно."""
    res = common.pvalue_and_ci(-real if real is not None else None,
                                [-x for x in nulls], [-x for x in boots])
    res["stat"] = real
    lo, hi = res["ci95"]
    res["ci95"] = [-hi if hi is not None else None, -lo if lo is not None else None]
    return res


def _field(real_vals: list[float], ctrl_pool: list[float], by_day: dict,
           draws: int, rng: random.Random, lower_tail: bool = False) -> dict:
    med = _stat(real_vals)
    pos = (sum(1 for v in real_vals if v > 0) / len(real_vals)) if real_vals else None
    nulls = _boot_median(ctrl_pool, len(real_vals), draws, rng)
    boots = _boot(by_day, draws, rng)
    verdict = _pvalue_lower(med, nulls, boots) if lower_tail else common.pvalue_and_ci(med, nulls, boots)
    return {"median": med, "pos_share": pos, "null": verdict}


def _row(k, t, group, n_appear, n_days, n_pulled, n_touched, n_other, pulled,
         day_ts, day_mid, draws, rng) -> dict:
    during_ctrl, after_ctrl = _controls(rng, pulled, day_ts, day_mid, draws)
    during_real = [p["move_during"] for p in pulled]
    after_real = [p["move_after"] for p in pulled if p["move_after"] is not None]
    during_by_day, after_by_day = defaultdict(list), defaultdict(list)
    for p in pulled:
        during_by_day[p["day"]].append(p["move_during"])
        if p["move_after"] is not None:
            after_by_day[p["day"]].append(p["move_after"])
    return {
        "k": k, "t": t, "level_group": group,
        "n_appear": n_appear, "per_day": (n_appear / n_days) if n_days else None,
        "share_pulled": (n_pulled / n_appear) if n_appear else None,
        "share_touched": (n_touched / n_appear) if n_appear else None,
        "share_outlived": (n_other / n_appear) if n_appear else None,
        "move_during": _field(during_real, during_ctrl, dict(during_by_day), draws, rng),
        "move_after": _field(after_real, after_ctrl, dict(after_by_day), draws, rng, lower_tail=True),
    }


def _compute(days: dict, k_factors, lifetimes_s, draws: int, rng: random.Random) -> list[dict]:
    if not days:
        return []
    day_ts = {d: [s[0] for s in snaps] for d, snaps in days.items()}
    day_mid = {d: [_mid(s) for s in snaps] for d, snaps in days.items()}
    day_qmed = {d: _q_med(snaps) for d, snaps in days.items()}
    day_segs = {d: _segments(snaps) for d, snaps in days.items()}
    n_days = len(days)
    rows = []
    for k in k_factors:
        events = []
        for d, segs in day_segs.items():
            for ev in _appearances(segs, k, day_qmed[d]):
                ev["day"] = d
                events.append(ev)
        for t in lifetimes_s:
            resolved = [(ev, _resolve(ev, t)) for ev in events]
            for group, lvls in (("2-3", (2, 3)), ("4-5", (4, 5))):
                bucket = [(ev, r) for ev, r in resolved if ev["level"] in lvls]
                n_appear = len(bucket)
                n_pulled = sum(1 for _, r in bucket if r["outcome"] == "pulled")
                n_touched = sum(1 for _, r in bucket if r["outcome"] == "touched")
                pulled = []
                for ev, r in bucket:
                    if r["outcome"] != "pulled":
                        continue
                    sign = 1.0 if ev["side"] == "bid" else -1.0
                    move_during = sign * (r["mid"] - ev["mid_appear"])
                    mid_after = _mid_at_or_after(day_ts[ev["day"]], day_mid[ev["day"]],
                                                  r["ts"] + int(t * 1000))
                    move_after = sign * (mid_after - r["mid"]) if mid_after is not None else None
                    pulled.append({"day": ev["day"], "elapsed": r["elapsed"], "t": t,
                                    "move_during": move_during, "move_after": move_after})
                rows.append(_row(k, t, group, n_appear, n_days, n_pulled, n_touched,
                                  n_appear - n_pulled - n_touched, pulled, day_ts, day_mid,
                                  draws, rng))
    return rows


def _atr_minute_mid(snapshots: list[tuple]):
    buckets: dict = defaultdict(list)
    for ts_ms, bids, asks in snapshots:
        buckets[ts_ms // 1000 // 60].append((bids[0][0] + asks[0][0]) / 2.0)
    ranges = [max(v) - min(v) for v in buckets.values() if len(v) >= 2]
    return statistics.median(ranges) if ranges else None


def analyze(snapshots: list[tuple], k_factors=(5, 10), lifetimes_s=(10, 30, 120),
            draws: int = 200, seed: int = 0) -> dict:
    rng = random.Random(seed)
    days = _by_day(snapshots)
    rows = _compute(days, k_factors, lifetimes_s, draws, rng)
    day_list = sorted(days)
    cut = day_list[len(day_list) // 2] if day_list else None
    first = {d: v for d, v in days.items() if cut and d < cut}
    second = {d: v for d, v in days.items() if cut and d >= cut}
    halves = {"first": _compute(first, k_factors, lifetimes_s, draws, rng),
              "second": _compute(second, k_factors, lifetimes_s, draws, rng)}
    mids = [_mid(s) for s in snapshots]
    return {"rows": rows, "halves": halves, "n_days": len(days), "notes": [],
            "atr_min_pts": _atr_minute_mid(snapshots),
            "median_mid": statistics.median(mids) if mids else None}


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "book_key" (обязателен), "since",
    "until", "k_factors", "lifetimes_s", "draws", "seed"}."""
    book_key = arg["book_key"]
    symbol = arg.get("symbol_key", book_key)
    since, until = arg.get("since"), arg.get("until")
    snapshots, bad = _load_full_book(book_key, since, until)
    if not snapshots:
        return {"id": "C3", "symbol": symbol, "window": [since, until], "error": "нет снимков в окне"}
    res = analyze(snapshots, tuple(arg.get("k_factors", (5, 10))),
                  tuple(arg.get("lifetimes_s", (10, 30, 120))),
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)))
    notes = list(res["notes"])
    if bad:
        notes.append(f"пропущено битых снимков выжимки: {bad}")
    return common.report("C3", symbol, [since, until], res["rows"], notes,
                          n_days=res["n_days"], halves=res["halves"],
                          cost_pts=common.round_trip_cost_pts(symbol, res["median_mid"]),
                          atr_min_pts=res["atr_min_pts"])
