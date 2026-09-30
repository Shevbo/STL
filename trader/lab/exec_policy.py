"""Шаг 2 программы исполнения (docs/execution-cost-program.md): политики
доведения заявки на снимках стакана — «сразу рынок» против «лимит у планки
T секунд, потом рынок» и «лимит + погоня C секунд».

ЗАЧЕМ. Экзекьютор живого робота (trader/quik/exec_profiles.py, зона
real-trade) — это тот же рычаг: hold_sec у планки, chase_sec погони, дальше
рынок. Здесь измеряем, что этот рычаг ДАЁТ на архивных снимках, прежде чем
советовать real-trade секунды.

НАЛИВКА БЕЗ ОЧЕРЕДИ. По снимкам стакана мы не знаем позицию нашей заявки в
очереди на цене, поэтому наливка пассивной заявки даётся ГРАНИЦАМИ:
  оптимистично — первый снимок, где встречный best дошёл до нашей цены
    (коснулся) ИЛИ объём на нашей цене на своей стороне упал до нуля/цена
    исчезла из своей стороны книги, оставаясь ниже встречного best (уровень
    целиком съеден чужими сделками, наша заявка внутри него);
  пессимистично — только когда цена прошла наш уровень НАСКВОЗЬ (встречный
    best ушёл строго за нашу цену), то есть точно достаточно объёма прошло,
    чтобы съесть и нас, и ещё немного.
ponytail: видимость — 5 уровней снимка. Если наша цена за время ожидания
уходит глубже 5-го видимого уровня (а не съедается), это неотличимо от
«съедена» — граница чуть оптимистичнее в этом редком случае, upgrade —
модель очереди по ленте (шаг 3 программы).

ДАННЫЕ. Полная выжимка стакана — тот же формат, что у C1
(`_load_full_book`), гэпы > 60 с внутри дня режут окно вперёд той же
`_segment_ends`, половины через `_halves_book` — переиспользуются импортом,
не дублируются.

СТОИМОСТЬ И ЗНАК. Как в book_replay.BookRuntime: sign = +1 покупка, -1
продажа; издержка = sign * (цена_исполнения - mid(t)) — положительное
«хуже для нас». VWAP съедания N лотов считает BookRuntime._walk (тот же
рычаг, что исполняет обычный бэктест по стакану).

НЕБЛАГОПРИЯТНЫЙ ОТБОР. После пассивной наливки сообщаем ход mid через 60 с,
тем же знаком «хуже для нас»: adverse = sign * (наша_цена - mid(t_fill+60)).
Считается только для оптимистичной границы (пессимистичных наливок мало,
второе поле было бы почти всегда пустым) — см. cost_p90_pess и
adverse_60s_med в строке отчёта.
"""
from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict

from trader.lab.book_replay import BookRuntime
from trader.lab.footprints import common
from trader.lab.footprints.c1_book_imbalance import _halves_book, _load_full_book, _segment_ends

DEFAULT_SIZES = (1, 5, 10)
DEFAULT_HOLD_S = (0, 5, 10, 30, 60, 180)
DEFAULT_CHASE_S = (0, 10, 180)
DEFAULT_CHASE_EVERY_S = 10
DEFAULT_N_SAMPLES = 3000
DEFAULT_DRAWS = 200

# Класс минуты: событийные минуты (10:00, 15:30, 16:30, 17:00, 23:00) важнее
# границы часа/получаса — они проверяются первыми.
EVENT_MINUTES = {10 * 60, 15 * 60 + 30, 16 * 60 + 30, 17 * 60, 23 * 60}

_PRICE_EPS = 1e-9


# --------------------------------------------------------------------------
# Подготовка: группировка по дню, классы момента
# --------------------------------------------------------------------------

def _prep_days(rows: list[tuple]) -> dict:
    """rows = [(ts_ms, bids, asks), ...] (см. _load_full_book) -> {день: {...}}."""
    days: dict = defaultdict(list)
    for r in rows:
        days[common.day_of(r[0] / 1000)].append(r)
    out = {}
    for d, drows in days.items():
        ts = [r[0] for r in drows]
        out[d] = {"ts": ts, "bids": [r[1] for r in drows], "asks": [r[2] for r in drows],
                  "block_end": _segment_ends(ts)}
    return out


def _minute_class(minute: int) -> str:
    if minute in EVENT_MINUTES:
        return "event"
    if minute % 60 == 0:
        return "h:00"
    if minute % 60 == 30:
        return "h:30"
    return "other"


def _weekend_label(day) -> str:
    return "выходной" if day.weekday() >= 5 else "будни"


def _sample_anchors(days_data: dict, n_samples: int, seed: int) -> list[tuple]:
    """Стратифицированная выборка точек отсчёта t: (класс_минуты, будни/выходной),
    только сессия [07:00, 23:50). Равная квота на страту, остаток обрезается."""
    rng = random.Random(seed)
    strata: dict = defaultdict(list)
    for day, dd in days_data.items():
        wl = _weekend_label(day)
        for i, ts_ms in enumerate(dd["ts"]):
            minute = common.minute_of_day(ts_ms // 1000)
            if not common.in_session(minute):
                continue
            strata[(_minute_class(minute), wl)].append((day, i))
    keys = list(strata)
    if not keys:
        return []
    per = -(-n_samples // len(keys))  # ceil
    out = []
    for key in keys:
        pool = strata[key]
        out.extend(rng.sample(pool, min(per, len(pool))))
    if len(out) > n_samples:
        rng.shuffle(out)
        out = out[:n_samples]
    return out


# --------------------------------------------------------------------------
# Границы наливки на одной цене
# --------------------------------------------------------------------------

def _level_qty(levels: list[tuple], price: float) -> float:
    for p, q in levels:
        if abs(p - price) < _PRICE_EPS:
            return q
    return 0


def _opt_filled(bids: list[tuple], asks: list[tuple], side: str, price: float) -> bool:
    if side == "buy":
        if asks[0][0] <= price:
            return True
        return _level_qty(bids, price) <= 0 and asks[0][0] > price
    if bids[0][0] >= price:
        return True
    return _level_qty(asks, price) <= 0 and bids[0][0] < price


def _pess_filled(bids: list[tuple], asks: list[tuple], side: str, price: float) -> bool:
    return asks[0][0] < price if side == "buy" else bids[0][0] > price


def _scan_fill(dd: dict, lo: int, hi: int, side: str, price: float, mode: str) -> int | None:
    if lo > hi:
        return None
    check = _opt_filled if mode == "opt" else _pess_filled
    bids, asks = dd["bids"], dd["asks"]
    for j in range(lo, hi + 1):
        if check(bids[j], asks[j], side, price):
            return j
    return None


def _snapshot_at_or_after(dd: dict, target_ms: int, lo: int) -> int | None:
    """Первый снимок с ts >= target_ms, не дальше конца непрерывного блока от lo
    (та же гэп-семантика, что у _segment_ends: без дыры > 60 с)."""
    ts = dd["ts"]
    j = bisect.bisect_left(ts, target_ms, lo)
    if j >= len(ts) or j > dd["block_end"][lo]:
        return None
    return j


def _chase_legs(chase_s: int, chase_every_s: int) -> list[int]:
    if chase_s <= 0:
        return []
    if chase_every_s <= 0:
        return [chase_s]
    legs = [chase_every_s] * (chase_s // chase_every_s)
    rem = chase_s % chase_every_s
    if rem:
        legs.append(rem)
    return legs


def _simulate(dd: dict, i: int, side: str, price0: float, hold_s: int, chase_s: int,
             chase_every_s: int, mode: str) -> tuple[bool, float, int | None]:
    """(налилась?, цена_на_момент_исхода, индекс_снимка_исхода|None).

    Индекс — снимок наливки (если налилась) или снимок для рыночного добора
    (если нет); None = снимка для добора нет (дыра/конец дня) — анкер дропается."""
    legs = [d for d in ([hold_s] + _chase_legs(chase_s, chase_every_s)) if d > 0]
    if not legs:
        return False, price0, i  # hold(0)+chase(0) = рынок немедленно, тот же снимок t
    cur_idx = i
    price = price0
    ts = dd["ts"]
    for leg_no, dur in enumerate(legs):
        target_ms = ts[cur_idx] + dur * 1000
        hi = min(bisect.bisect_right(ts, target_ms, cur_idx) - 1, dd["block_end"][cur_idx])
        fill_j = _scan_fill(dd, cur_idx + 1, hi, side, price, mode)
        if fill_j is not None:
            return True, price, fill_j
        nxt_idx = _snapshot_at_or_after(dd, target_ms, cur_idx)
        if nxt_idx is None:
            return False, price, None
        if leg_no == len(legs) - 1:
            return False, price, nxt_idx
        cur_idx = nxt_idx
        price = dd["bids"][cur_idx][0][0] if side == "buy" else dd["asks"][cur_idx][0][0]
    return False, price, None  # недостижимо: legs непустой и последний leg всегда возвращает


def _adverse_60s(dd: dict, idx: int, side: str, price: float, sign: int) -> float | None:
    ts = dd["ts"]
    j = bisect.bisect_left(ts, ts[idx] + 60_000, idx)
    if j >= len(ts) or j > dd["block_end"][idx]:
        return None
    bids_j, asks_j = dd["bids"][j], dd["asks"][j]
    mid_later = (bids_j[0][0] + asks_j[0][0]) / 2
    return sign * (price - mid_later)


def _eval_policy(dd: dict, i: int, side: str, size: int, hold_s: int, chase_s: int,
                 chase_every_s: int, mid0: float) -> dict | None:
    """Пара (opt, pess) для hold/hold_chase. None = анкер дропнут целиком (нет
    снимка для рыночного добора хотя бы в одной из границ) — n держим общим
    для opt и pess, частичных строк не бывает."""
    bids0, asks0 = dd["bids"][i], dd["asks"][i]
    sign = 1 if side == "buy" else -1
    price0 = bids0[0][0] if side == "buy" else asks0[0][0]
    out: dict = {}
    for mode in ("opt", "pess"):
        filled, price, idx = _simulate(dd, i, side, price0, hold_s, chase_s, chase_every_s, mode)
        if idx is None:
            return None
        if filled:
            cost = sign * (price - mid0)
            adverse = _adverse_60s(dd, idx, side, price, sign) if mode == "opt" else None
        else:
            levels = dd["asks"][idx] if side == "buy" else dd["bids"][idx]
            vwap, _deep = BookRuntime._walk(levels, size)
            cost = sign * (vwap - mid0)
            adverse = None
        out[f"filled_{mode}"] = filled
        out[f"cost_{mode}"] = cost
        if mode == "opt":
            out["adverse_opt"] = adverse
    return out


# --------------------------------------------------------------------------
# Статистика по строкам
# --------------------------------------------------------------------------

def _pctl(sorted_vals: list[float], q: float) -> float:
    # ponytail: тот же приём, что common._pct/c1._percentile — private чужого
    # модуля не импортируем, две строки дешевле импорта.
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[idx]


def _median_of_blocks(blocks: list[list[float]]) -> float | None:
    vals = [x for blk in blocks for x in blk]
    return statistics.median(vals) if vals else None


def _finalize(buckets: dict, seed: int, draws: int) -> list[dict]:
    out_rows = []
    for (pname, hold_s, chase_s, size, side, mclass, wlabel), b in buckets.items():
        if b["n"] == 0:
            continue
        rng = random.Random(f"{seed}:{pname}:{hold_s}:{chase_s}:{size}:{side}:{mclass}:{wlabel}")
        rows_by_day: dict = defaultdict(list)
        for d, v in zip(b["days"], b["diff_pess"]):
            rows_by_day[d].append(v)
        boot = common.bootstrap_days(rows_by_day, _median_of_blocks, draws, rng)
        boot_sorted = sorted(boot)
        ci = [_pctl(boot_sorted, 0.025), _pctl(boot_sorted, 0.975)] if boot_sorted else [None, None]
        out_rows.append({
            "policy": pname, "hold_s": hold_s, "chase_s": chase_s, "size": size, "side": side,
            "minute_class": mclass, "weekend": wlabel, "n": b["n"],
            "fill_share_opt": b["fill_opt"] / b["n"],
            "fill_share_pess": b["fill_pess"] / b["n"],
            "cost_med_opt": statistics.median(b["cost_opt"]),
            "cost_med_pess": statistics.median(b["cost_pess"]),
            "cost_mean_opt": statistics.fmean(b["cost_opt"]),
            "cost_mean_pess": statistics.fmean(b["cost_pess"]),
            "cost_p90_pess": _pctl(sorted(b["cost_pess"]), 0.9),
            "adverse_60s_med": statistics.median(b["adverse"]) if b["adverse"] else None,
            "vs_market_med_opt": statistics.median(b["diff_opt"]),
            "vs_market_med_pess": statistics.median(b["diff_pess"]),
            "vs_market_ci95_pess": ci,
        })
    return out_rows


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------

def analyze(rows: list[tuple], *, sizes=DEFAULT_SIZES, hold_s_list=DEFAULT_HOLD_S,
           chase_s_list=DEFAULT_CHASE_S, chase_every_s: int = DEFAULT_CHASE_EVERY_S,
           n_samples: int = DEFAULT_N_SAMPLES, draws: int = DEFAULT_DRAWS,
           seed: int = 0) -> dict:
    """rows = [(ts_ms, bids, asks), ...] -> {rows, n_days, n_used, dropped,
    half_spread_med, notes}. Тестируемое ядро (см. c1_book_imbalance.analyze) —
    без сети, реальные данные грузит только run()."""
    days_data = _prep_days(rows)
    anchors = _sample_anchors(days_data, n_samples, seed)
    policies = [("market", 0, 0)]
    policies += [("hold", t, 0) for t in hold_s_list]
    policies += [("hold_chase", t, c) for t in hold_s_list for c in chase_s_list if c > 0]

    buckets: dict = defaultdict(lambda: {"n": 0, "fill_opt": 0, "fill_pess": 0,
                                          "cost_opt": [], "cost_pess": [], "adverse": [],
                                          "diff_opt": [], "diff_pess": [], "days": []})
    half_spreads = []
    dropped = 0
    for day, i in anchors:
        dd = days_data[day]
        bids0, asks0 = dd["bids"][i], dd["asks"][i]
        mid0 = (bids0[0][0] + asks0[0][0]) / 2
        half_spreads.append((asks0[0][0] - bids0[0][0]) / 2)
        mclass = _minute_class(common.minute_of_day(dd["ts"][i] // 1000))
        wlabel = _weekend_label(day)
        for side in ("buy", "sell"):
            sign = 1 if side == "buy" else -1
            for size in sizes:
                vwap, _deep = BookRuntime._walk(asks0 if side == "buy" else bids0, size)
                cost_market = sign * (vwap - mid0)
                for pname, hold_s, chase_s in policies:
                    key = (pname, hold_s, chase_s, size, side, mclass, wlabel)
                    b = buckets[key]
                    if pname == "market":
                        b["n"] += 1
                        b["fill_opt"] += 1
                        b["fill_pess"] += 1
                        b["cost_opt"].append(cost_market)
                        b["cost_pess"].append(cost_market)
                        b["diff_opt"].append(0.0)
                        b["diff_pess"].append(0.0)
                        b["days"].append(day)
                        continue
                    res = _eval_policy(dd, i, side, size, hold_s, chase_s, chase_every_s, mid0)
                    if res is None:
                        dropped += 1
                        continue
                    b["n"] += 1
                    b["days"].append(day)
                    b["fill_opt"] += 1 if res["filled_opt"] else 0
                    b["fill_pess"] += 1 if res["filled_pess"] else 0
                    b["cost_opt"].append(res["cost_opt"])
                    b["cost_pess"].append(res["cost_pess"])
                    b["diff_opt"].append(res["cost_opt"] - cost_market)
                    b["diff_pess"].append(res["cost_pess"] - cost_market)
                    if res["adverse_opt"] is not None:
                        b["adverse"].append(res["adverse_opt"])

    out_rows = _finalize(buckets, seed, draws)
    return {"rows": out_rows, "n_days": len(days_data), "n_used": len(anchors),
            "dropped": dropped,
            "half_spread_med": statistics.median(half_spreads) if half_spreads else None,
            "notes": []}


def run(arg: dict) -> dict:
    """Задача агента (kind='task', считает только i9): arg = {"symbol_key",
    "book_key" (обязателен), "since", "until", "sizes", "hold_s", "chase_s",
    "chase_every_s", "n_samples", "draws", "seed"}."""
    book_key = arg.get("book_key")
    if not book_key:
        return {"id": "EXEC2", "error": "book_key обязателен"}
    symbol_key = arg.get("symbol_key") or book_key
    since, until = arg.get("since"), arg.get("until")
    rows, dropped_load = _load_full_book(book_key, since, until)
    if not rows:
        return {"id": "EXEC2", "symbol": symbol_key, "window": [since, until],
                "error": "нет снимков стакана в окне"}

    sizes = tuple(int(x) for x in arg.get("sizes", DEFAULT_SIZES))
    hold_s_list = tuple(int(x) for x in arg.get("hold_s", DEFAULT_HOLD_S))
    chase_s_list = tuple(int(x) for x in arg.get("chase_s", DEFAULT_CHASE_S))
    chase_every_s = int(arg.get("chase_every_s", DEFAULT_CHASE_EVERY_S))
    n_samples = int(arg.get("n_samples", DEFAULT_N_SAMPLES))
    draws = int(arg.get("draws", DEFAULT_DRAWS))
    seed = int(arg.get("seed", 0))

    kw = dict(sizes=sizes, hold_s_list=hold_s_list, chase_s_list=chase_s_list,
             chase_every_s=chase_every_s, n_samples=n_samples, draws=draws, seed=seed)
    res = analyze(rows, **kw)
    first_rows, second_rows = _halves_book(rows)
    halves = {
        "first": analyze(first_rows, **kw)["rows"] if len(first_rows) > 1 else [],
        "second": analyze(second_rows, **kw)["rows"] if len(second_rows) > 1 else [],
    }
    hs = res["half_spread_med"]
    notes = [f"снимков отброшено при загрузке: {dropped_load}",
            f"n_samples={n_samples}, реально взято точек отсчёта: {res['n_used']}",
            f"анкеров-политик отброшено (нет снимка для рыночного добора): {res['dropped']}",
            f"полспред медианный по отсчётам: {hs:.2f} пт" if hs is not None else "полспред медианный: нет данных",
            "adverse_60s — только по оптимистичной границе (пессимистичных наливок мало)"]
    last_bid1, last_ask1 = rows[-1][1][0][0], rows[-1][2][0][0]
    last_mid = (last_bid1 + last_ask1) / 2
    return common.report("EXEC2", symbol_key, [since, until], res["rows"], notes,
                         n_days=res["n_days"], halves=halves,
                         cost_pts=common.round_trip_cost_pts(symbol_key, last_mid))
