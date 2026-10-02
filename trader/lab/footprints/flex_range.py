"""Длина боковика после импульса: зависимость L от высоты импульса H (docs/flex-radiation-2026.md, часть 1).

ИМПУЛЬС: детектор r1_retest.find_impulses (high-water, подтверждение откатом на 1 ATR, бары закрыты, событие
определяется барами <= i_conf). H = strength (ATR), t0 = бар пика t_p (закрытие этого бара).
БОКОВИК: от t0, длина L = число баров до первого закрытия вне полосы; не вышло до конца дня = цензура.
Полосы: P1 [P - w*H, P + w*H] вокруг цены пика (H в пунктах = strength*ATR), w {0.25, 0.5};
P2 [c0 - k*ATR, c0 + k*ATR] вокруг close бара t0, k {3, 6}.
Оценка: медиана Каплана-Мейера по корзинам H (3-5, 5-8, 8-12, 12-25 ATR), ранговая корреляция и наклон
log L на log H по нецензурированным. Контроль: те же полосы от случайных моментов того же дня с
псевдо-высотой из распределения импульсов того же часа (20 розыгрышей).

Бары разрежены (торговые минуты), поэтому L считается в барах (как в документе), минуты даются справочно.
"""
from __future__ import annotations

import math
import random
import statistics

from trader.lab.footprints import common
from trader.lab.footprints.r1_retest import find_impulses

PARAMS = {"atr_n": 60, "imp_bars": 20, "imp_min": 30, "imp_max": 250, "pb": 10}
BUCKETS = (("3-5", 3.0, 5.0), ("5-8", 5.0, 8.0), ("8-12", 8.0, 12.0), ("12-25", 12.0, 25.0001))
BANDS = (("P1w0.25", "P1", 0.25), ("P1w0.5", "P1", 0.5), ("P2k3", "P2", 3.0), ("P2k6", "P2", 6.0))


def atr_before(rows: list, i: int, n: int = 60) -> float | None:
    """Средний размах n баров, оканчивающихся ПЕРЕД баром i (по закрытым барам)."""
    if i < n:
        return None
    return sum(r[2] - r[3] for r in rows[i - n:i]) / n


def band(kind: str, par: float, price_peak: float, c0: float, h_pts: float, atr: float) -> tuple[float, float]:
    """Границы полосы по данным до t0: P1 вокруг пика (par = w), P2 вокруг close (par = k)."""
    if kind == "P1":
        return price_peak - par * h_pts, price_peak + par * h_pts
    return c0 - par * atr, c0 + par * atr


def length_after(rows: list, t0: int, lo: float, hi: float, last: int | None = None) -> tuple[int, bool, float]:
    """(L в барах, наблюдалось ли выходом, минуты). Цензура: до последнего бара дня (last) закрытий вне полосы нет."""
    n = len(rows) if last is None else last + 1
    for j in range(t0 + 1, n):
        c = rows[j][4]
        if c < lo or c > hi:
            return j - t0, True, (rows[j][0] - rows[t0][0]) / 60
    return n - 1 - t0, False, (rows[n - 1][0] - rows[t0][0]) / 60


def aggregate_tf(rows: list, tf: int) -> list:
    """M1 -> корзины tf минут (bucket = ts - ts % (tf*60), как retro_reverse.aggregate): open первого, high/low
    экстремумы, close последнего. Строка [ts корзины, o, h, l, c, v, ts последней M1-минуты корзины]. Бар корзины
    известен только когда она закрыта: решения принимаются на ts_last и позже (по M1 внутри корзины ничего не заглядывает)."""
    size, out, cur = tf * 60, [], None
    for r in rows:
        b = r[0] - r[0] % size
        if cur is not None and cur[0] == b:
            cur[2], cur[3], cur[4], cur[5], cur[6] = max(cur[2], r[2]), min(cur[3], r[3]), r[4], cur[5] + (r[5] if len(r) > 5 else 0), r[0]
        else:
            if cur is not None:
                out.append(cur)
            cur = [b, r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else 0, r[0]]
    if cur is not None:
        out.append(cur)
    return out


def km_median(pairs: list) -> float | None:
    """Медиана Каплана-Мейера; pairs = [(время, наблюдалось)]. None: кривая не опускается до 0.5."""
    if not pairs:
        return None
    ts = sorted({t for t, _ in pairs})
    s, at_risk = 1.0, len(pairs)
    for t in ts:
        d = sum(1 for x, o in pairs if x == t and o)
        c = sum(1 for x, o in pairs if x == t and not o)
        if d:
            s *= 1 - d / at_risk
            if s <= 0.5:
                return t
        at_risk -= d + c
    return None


def spearman(a: list, b: list) -> float | None:
    if len(a) < 3:
        return None

    def rank(xs):
        o = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and xs[o[j + 1]] == xs[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / den if den else None


def slope(h: list, l_: list) -> float | None:
    """Наклон log L на log H (OLS) по парам с L >= 1."""
    xs = [(math.log(a), math.log(b)) for a, b in zip(h, l_) if a > 0 and b >= 1]
    if len(xs) < 5:
        return None
    mx, my = statistics.mean(x for x, _ in xs), statistics.mean(y for _, y in xs)
    den = sum((x - mx) ** 2 for x, _ in xs)
    return sum((x - mx) * (y - my) for x, y in xs) / den if den else None


def events_of_day(rows: list) -> list:
    """Импульсы дня (обе стороны) с t0 = бар пика; только события, где есть бар после t0 и ATR."""
    out = []
    for e in find_impulses(rows, PARAMS):
        t0 = e["t_p"]
        if t0 + 1 >= len(rows):
            continue
        out.append({**e, "t0": t0, "H": e["strength"], "hour": (rows[t0][0] % 86400) // 3600})
    return out


def event_L(rows: list, e: dict) -> dict:
    """L по всем четырём полосам для события e."""
    t0, atr = e["t0"], e["atr"]
    h_pts = e["H"] * atr
    c0 = rows[t0][4]
    out = {}
    for name, kind, par in BANDS:
        lo, hi = band(kind, par, e["P"], c0, h_pts, atr)
        out[name] = length_after(rows, t0, lo, hi)
    return out


def control_events(rows: list, evs: list, pool: dict, rng: random.Random) -> list:
    """Случайные моменты того же дня: на каждое реальное событие один момент (бар >= 60 и не последний), псевдо-высота
    из распределения импульсов того же часа (иначе любого), P = high бара, c0 = close."""
    out = []
    n = len(rows)
    for _e in evs:
        if n < 62:
            continue
        t0 = rng.randrange(60, n - 1)
        hour = (rows[t0][0] % 86400) // 3600
        hs = pool.get(hour) or pool["all"]
        atr = atr_before(rows, t0)
        if not atr:
            continue
        H = rng.choice(hs)
        out.append({"t0": t0, "H": H, "atr": atr, "P": rows[t0][2], "hour": hour})
    return out


def bucket_of(h: float) -> int | None:
    for k, (_n, lo, hi) in enumerate(BUCKETS):
        if lo <= h < hi:
            return k
    return None


def summarize(items: list, band_name: str) -> dict:
    """items = [{H, L:{band:(L, obs, min)}}]: корзины, корреляция, наклон."""
    out = {"buckets": [], "n": len(items)}
    for k, (nm, _lo, _hi) in enumerate(BUCKETS):
        sub = [x for x in items if bucket_of(x["H"]) == k]
        pairs = [(x["L"][band_name][0], x["L"][band_name][1]) for x in sub]
        out["buckets"].append({"b": nm, "n": len(sub), "cens": sum(1 for _, o in pairs if not o),
                               "km_med": km_median(pairs),
                               "med_obs": statistics.median([t for t, o in pairs if o]) if any(o for _, o in pairs) else None})
    obs = [x for x in items if x["L"][band_name][1]]
    out["rho"] = spearman([x["H"] for x in obs], [x["L"][band_name][0] for x in obs])
    out["slope"] = slope([x["H"] for x in obs], [x["L"][band_name][0] for x in obs])
    out["n_obs"] = len(obs)
    return out


def analyze(rows: list, draws: int = 20, seed: int = 0) -> dict:
    """Один контракт: все события, полосы, корзины, половины, контроль."""
    days = common.by_day(rows)
    keys = sorted(days)
    half_cut = keys[len(keys) // 2] if keys else None
    day_ev, items = {}, []
    for d in keys:
        r = days[d]
        evs = events_of_day(r)
        day_ev[d] = evs
        for e in evs:
            items.append({"day": str(d), "half": 1 if d >= half_cut else 0, "H": e["H"], "side": e["side"], "hour": e["hour"],
                          "L": event_L(r, e), "ts": r[e["t0"]][0]})
    pool: dict = {"all": [x["H"] for x in items]}
    for x in items:
        pool.setdefault(x["hour"], []).append(x["H"])
    res = {"n_days": len(keys), "n_events": len(items), "variants": {}}
    for name, _k, _p in BANDS:
        v = {"all": summarize(items, name), "h0": summarize([x for x in items if x["half"] == 0], name),
             "h1": summarize([x for x in items if x["half"] == 1], name), "ctrl": []}
        for dd in range(draws):
            rng = random.Random(seed + 100 + dd)
            citems = []
            for d in keys:
                r = days[d]
                for ce in control_events(r, day_ev[d], pool, rng):
                    citems.append({"H": ce["H"], "L": {nm: _ctrl_L(r, ce, nm, kk, pp) for nm, kk, pp in BANDS if nm == name}})
            s = summarize(citems, name)
            v["ctrl"].append({"slope": s["slope"], "rho": s["rho"], "km": [b["km_med"] for b in s["buckets"]], "n": s["n"]})
        res["variants"][name] = v
    res["events"] = [{"day": x["day"], "ts": x["ts"], "H": round(x["H"], 3), "side": x["side"], "half": x["half"],
                      "L": {k: [a, b, round(c, 1)] for k, (a, b, c) in x["L"].items()}} for x in items]
    return res


def _ctrl_L(rows, ce, name, kind, par):
    h_pts = ce["H"] * ce["atr"]
    lo, hi = band(kind, par, ce["P"], rows[ce["t0"]][4], h_pts, ce["atr"])
    return length_after(rows, ce["t0"], lo, hi)


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "since", "until", "draws"}."""
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "FLEX1", "symbol": key, "error": "нет баров в окне"}
    if arg.get("tf", 1) != 1:
        return {"id": "FLEX1", "symbol": key, **analyze_tf(rows, int(arg["tf"]), int(arg.get("draws", 20)), int(arg.get("seed", 0)))}
    return {"id": "FLEX1", "symbol": key, **analyze(rows, int(arg.get("draws", 20)), int(arg.get("seed", 0)))}


def analyze_tf(rows: list, tf: int, draws: int = 20, seed: int = 0) -> dict:
    """Часть 1 на оси tf: бары tf непрерывным рядом (ATR за 60 баров tf переходит через ночь), импульс и подтверждение
    в одном дне, L в барах tf до конца дня (цензура), контроль: случайные tf-бары того же дня с псевдо-высотой того же часа."""
    agg = aggregate_tf(rows, tf)
    day = [a[6] // 86400 for a in agg]
    last_of, first_of = {}, {}
    for i, d in enumerate(day):
        last_of[d] = i
        first_of.setdefault(d, i)
    items = []
    for e in find_impulses(agg, PARAMS):
        t0 = e["t_p"]
        if day[e["i_start"]] != day[e["i_conf"]] or t0 + 1 >= len(agg) or day[t0] != day[e["i_conf"]]:
            continue
        L = {}
        h_pts = e["strength"] * e["atr"]
        for name, kind, par in BANDS:
            lo, hi = band(kind, par, e["P"], agg[t0][4], h_pts, e["atr"])
            L[name] = length_after(agg, t0, lo, hi, last_of[day[t0]])
        items.append({"day": day[t0], "H": e["strength"], "hour": (agg[t0][6] % 86400) // 3600, "L": L, "t0": t0})
    if not items:
        return {"tf": tf, "n_events": 0, "variants": {}}
    dmin = min(x["day"] for x in items)
    dmax = max(x["day"] for x in items)
    cut = dmin + (dmax - dmin) // 2
    for x in items:
        x["half"] = 1 if x["day"] >= cut else 0
    pool: dict = {"all": [x["H"] for x in items]}
    for x in items:
        pool.setdefault(x["hour"], []).append(x["H"])
    res = {"tf": tf, "n_events": len(items), "variants": {}}
    for name, _k, _p in BANDS:
        v = {"all": summarize(items, name), "h0": summarize([x for x in items if x["half"] == 0], name),
             "h1": summarize([x for x in items if x["half"] == 1], name), "ctrl": []}
        for dd in range(draws):
            rng = random.Random(seed + 100 + dd)
            citems = []
            for x in items:
                lo_i = max(60, first_of[x["day"]])
                hi_i = last_of[x["day"]] - 1
                if hi_i <= lo_i:
                    continue
                t0 = rng.randrange(lo_i, hi_i + 1)
                atr = atr_before(agg, t0)
                if not atr:
                    continue
                hs = pool.get((agg[t0][6] % 86400) // 3600) or pool["all"]
                H = rng.choice(hs)
                kk, pp = next((k_, p_) for nm, k_, p_ in BANDS if nm == name)
                lo, hi = band(kk, pp, agg[t0][2], agg[t0][4], H * atr, atr)
                citems.append({"H": H, "L": {name: length_after(agg, t0, lo, hi, last_of[x["day"]])}})
            sm = summarize(citems, name)
            v["ctrl"].append({"slope": sm["slope"], "rho": sm["rho"], "km": [b["km_med"] for b in sm["buckets"]], "n": sm["n"]})
        res["variants"][name] = v
    return res
