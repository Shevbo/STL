"""R1: ретест пика импульса против зеркального уровня (docs/retest-strategy-2026.md, часть 1).

Вопрос: после импульса в imp_min..imp_max ATR и подтверждения пика откатом на pb ATR
цена возвращается к пику P чаще, чем уходит на то же расстояние d в обратную сторону
(зеркальный уровень M). У блуждания без сноса вероятности равны.

ДЕТЕКТОР (чистый, find_impulses, без заглядывания): внутри дня, бары закрыты.
Кандидат на бар i: окно не длиннее imp_bars баров, оканчивающееся на i, ход от минимума
low окна (бар s) до high[i] составляет imp_min..imp_max ATR, ATR = среднее (high-low) за
atr_n баров, оканчивающихся ПЕРЕД s; bar i обязан быть новым максимумом окна. Пока нет
подтверждения, новый максимум сдвигает P и t_p (импульс заново проверяется; вышел за
imp_max или потерял imp_min = кандидат снят). Подтверждение: первый бар после t_p с
close <= P - pb ATR. После события новое окно начинается не раньше t_p+1.
Импульс вниз = тот же код на зеркальных ценах (o,h,l,c) -> (-o,-l,-h,-c), поэтому исход
сразу «в сторону пика».

ГОНКА (evaluate): с бара после подтверждения до t_p+wait_max. P засчитывается с бара
t_p+wait_min, M = close подтверждения - d. Оба на одном баре = категория «both».
Исход: вход по open бара после подтверждения; выход по open бара после бара, чей CLOSE
за P или за M; иначе по open бара после t_p+wait_max; конец дня = close последнего бара.
"""
from __future__ import annotations

import random
import statistics

from trader.lab.footprints import common

DEFAULTS = {"atr_n": 60, "imp_bars": 10, "imp_min": 30, "imp_max": 100, "pb": 10,
            "wait_min": 3, "wait_max": 120}

STRENGTH = (("3-5", 3.0, 5.0), ("5-7", 5.0, 7.0), ("7-10", 7.0, 10.0001))
LENGTH = (("1-3", 1, 3), ("4-6", 4, 6), ("7-10", 7, 10 ** 6))
HOURS = (("07-10", 7, 10), ("10-14", 10, 14), ("14-19", 14, 19), ("19-24", 19, 24))


def _params(p: dict | None) -> dict:
    return {**DEFAULTS, **(p or {})}


def _tr(blk: list[list], side: int) -> list[list]:
    """side=+1 как есть, -1 зеркало цен (o,h,l,c) -> (-o,-l,-h,-c)."""
    if side == 1:
        return blk
    return [[r[0], -r[1], -r[3], -r[2], -r[4]] + list(r[5:]) for r in blk]


def _find_side(T: list[list], p: dict) -> list[dict]:
    """События импульса вверх на (возможно зеркальных) барах T одного дня."""
    n_atr, w = p["atr_n"], p["imp_bars"]
    lo_k, hi_k, pb = p["imp_min"] / 10, p["imp_max"] / 10, p["pb"] / 10
    rng_ = [r[2] - r[3] for r in T]
    out: list[dict] = []
    lock = 0
    cand = None

    def probe(i):
        a = max(i - w + 1, lock)
        win = T[a:i + 1]
        s = a + min(range(len(win)), key=lambda k: win[k][3])  # первый минимум low
        if s < n_atr or (s < i and T[i][2] < max(r[2] for r in T[s:i])):
            return None
        atr = sum(rng_[s - n_atr:s]) / n_atr
        if atr <= 0:
            return None
        k = (T[i][2] - T[s][3]) / atr
        if not lo_k <= k <= hi_k:
            return None
        return {"s": s, "tp": i, "P": T[i][2], "atr": atr, "strength": k}

    for i in range(len(T)):
        if cand is not None and T[i][2] > cand["P"]:
            cand = probe(i)
            continue
        if cand is None:
            cand = probe(i)
            continue
        if i > cand["tp"] and T[i][4] <= cand["P"] - pb * cand["atr"]:
            out.append({"i_start": cand["s"], "t_p": cand["tp"], "i_conf": i, "P": cand["P"],
                        "d": cand["P"] - T[i][4], "atr": cand["atr"],
                        "strength": cand["strength"], "length": cand["tp"] - cand["s"] + 1})
            lock = cand["tp"] + 1
            cand = None
    return out


def find_impulses(rows: list[list], params: dict | None = None) -> list[dict]:
    """События обеих сторон ОДНОГО дня по барам [ts,o,h,l,c,v]; P в реальных ценах.

    Поля: side (+1 импульс вверх), i_start, t_p, i_conf, P, d, atr, strength (ATR),
    length (баров). Событие определяется только барами до i_conf включительно."""
    p = _params(params)
    evs = []
    for side in (1, -1):
        for e in _find_side(_tr(rows, side), p):
            e["P"] = side * e["P"]
            evs.append({"side": side, **e})
    return sorted(evs, key=lambda e: (e["i_conf"], -e["side"]))


def evaluate(blk: list[list], ev: dict, params: dict | None = None) -> dict:
    """Гонка P против M и исход в пунктах (в сторону пика) для события ev дня blk."""
    p = _params(params)
    T = _tr(blk, ev["side"])
    n, c, tp = len(T), ev["i_conf"], ev["t_p"]
    if c + 1 >= n:
        return {"drop": "no_entry"}
    end_w = tp + p["wait_max"]
    if end_w <= c:
        return {"drop": "late_confirm"}
    last = min(end_w, n - 1)
    P = ev["side"] * ev["P"]
    M = T[c][4] - ev["d"]
    kind, btr = "none", None
    for j in range(c + 1, last + 1):
        hit_p = j >= tp + p["wait_min"] and T[j][2] >= P
        hit_m = T[j][3] <= M
        if hit_p and hit_m:
            kind = "both"
            break
        if hit_p:
            kind, btr = "retest", j - tp
            break
        if hit_m:
            kind = "mirror"
            break
    ex = None
    for j in range(c + 1, last + 1):
        if T[j][4] > P or T[j][4] < M:
            ex = T[j + 1][1] if j + 1 < n else T[j][4]
            break
    if ex is None:
        ex = T[last + 1][1] if last + 1 < n else T[last][4]
    return {"kind": kind, "bars_to_retest": btr, "outcome": ex - T[c + 1][1],
            "crossed": end_w > n - 1}


def _bucket(v, table):
    return next((lab for lab, lo, hi in table if lo <= v < hi), None)


def _stat_row(cut: str, bucket: str, evs: list[dict], cost, draws: int, rng: random.Random) -> dict:
    n = len(evs)
    row = {"cut": cut, "bucket": bucket, "n": n}
    if not n:
        return row
    cnt = {k: sum(1 for e in evs if e["kind"] == k) for k in ("retest", "mirror", "both", "none")}
    out = [e["outcome"] for e in evs]
    btr = [e["bars_to_retest"] for e in evs if e["kind"] == "retest"]
    for k, name in (("retest", "p_retest"), ("mirror", "p_mirror"), ("none", "p_none"),
                    ("both", "p_both")):
        row[name] = cnt[k] / n
    score = [(e["day"], (e["kind"] == "retest") - (e["kind"] == "mirror")) for e in evs]
    real = (cnt["retest"] - cnt["mirror"]) / n
    nz = [s for _, s in score if s]
    # нуль: случайный знак каждого события (какая из сторон «пик»), каждое равновероятно
    nulls = [sum(s if rng.getrandbits(1) else -s for s in nz) / n for _ in range(draws)]
    by: dict = {}
    for d, s in score:
        by.setdefault(d, []).append(s)
    boots = common.bootstrap_days(
        by, lambda bl: (sum(map(sum, bl)) / sum(map(len, bl))) if sum(map(len, bl)) else None,
        draws, rng)
    row["diff"] = common.pvalue_and_ci(real, nulls, boots)
    mean = statistics.fmean(out)
    row.update(outcome_mean=mean, outcome_median=statistics.median(out),
               outcome_pos_share=sum(1 for x in out if x > 0) / n,
               outcome_mean_net=None if cost is None else mean - cost,
               bars_to_retest_median=statistics.median(btr) if btr else None)
    return row


def _rows(evs: list[dict], cost, draws: int, seed: int) -> list[dict]:
    cuts = [("all", "all", evs),
            ("not_crossed", "all", [e for e in evs if not e["crossed"]])]
    cuts += [("strength", lab, [e for e in evs if _bucket(e["strength"], STRENGTH) == lab])
             for lab, _, _ in STRENGTH]
    cuts += [("side", nm, [e for e in evs if e["side"] == sd])
             for nm, sd in (("up", 1), ("down", -1))]
    cuts += [("hour", lab, [e for e in evs if _bucket(e["hour"], HOURS) == lab])
             for lab, _, _ in HOURS]
    cuts += [("length", lab, [e for e in evs if _bucket(e["length"], LENGTH) == lab])
             for lab, _, _ in LENGTH]
    return [_stat_row(c, b, es, cost, draws, random.Random(seed * 1_000_003 + i))
            for i, (c, b, es) in enumerate(cuts)]


def analyze(rows: list[list], params: dict | None = None, draws: int = 200, seed: int = 0,
            cost_pts: float | None = None) -> dict:
    p = _params(params)
    rows, sess_notes = common.session_rows(rows)
    days = common.by_day(rows)
    evs, found, drops = [], 0, {"late_confirm": 0, "no_entry": 0}
    for d, blk in days.items():
        for e in find_impulses(blk, p):
            found += 1
            r = evaluate(blk, e, p)
            if "drop" in r:
                drops[r["drop"]] += 1
                continue
            evs.append({"day": d, "side": e["side"], "strength": e["strength"],
                        "length": e["length"], "hour": common.minute_of_day(blk[e["i_conf"]][0]) // 60,
                        "kind": r["kind"], "bars_to_retest": r["bars_to_retest"],
                        "outcome": r["outcome"], "crossed": r["crossed"]})
    crossed = sum(1 for e in evs if e["crossed"])
    nohist = sum(min(p["atr_n"], len(b)) for b in days.values())
    notes = sess_notes + [
        f"событий найдено {found}, в счёт {len(evs)}; отброшено: подтверждение позже wait_max "
        f"{drops['late_confirm']}, нет бара входа в дне {drops['no_entry']}",
        f"crossed_session: {crossed} из {len(evs)} ({(crossed / len(evs) if evs else 0):.1%}), "
        "окно усечено концом дня, выход по close последнего бара; строка not_crossed без них",
        f"ATR только внутри дня: первые atr_n баров каждого дня не начинают импульс ({nohist} баров)",
        "diff = p_retest - p_mirror; нуль = случайный знак каждого события, ci95 бутстрап по дням",
        f"параметры: {p}",
    ]
    first, second = common.halves(rows)
    half = {}
    for name, part in (("first", first), ("second", second)):
        ds = set(common.by_day(part)) if part else set()
        half[name] = _rows([e for e in evs if e["day"] in ds], cost_pts, draws, seed + 7) if ds else []
    return {"rows": _rows(evs, cost_pts, draws, seed), "halves": half,
            "n_days": len(days), "notes": notes}


def run(arg: dict) -> dict:
    """Задача агента: arg = {symbol_key, since, until, atr_n, imp_bars, imp_min, imp_max, pb,
    wait_min, wait_max, draws, seed}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "R1", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    cost = common.round_trip_cost_pts(key, statistics.median(r[4] for r in rows))
    res = analyze(rows, {k: int(arg[k]) for k in DEFAULTS if k in arg},
                  int(arg.get("draws", 200)), int(arg.get("seed", 0)), cost)
    return common.report("R1", key, [since, until], res["rows"], res["notes"],
                         n_days=res["n_days"], halves=res["halves"], cost_pts=cost,
                         atr_min_pts=common.atr_minute(rows))
