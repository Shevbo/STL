"""Предвестник импульса 8+ ATR (docs/impulse-precursor-2026.md, пререгистрация 02.10.2026).

Вопрос: есть ли устойчивый рисунок N баров (цены и объёма) прямо ПЕРЕД рывком 8+ ATR, который
повторяется и позволяет предсказать рывок и его направление на отложенных днях.

ДАННЫЕ. Бары tf {1,5,15} минут (агрегация закрытых корзин внутри дня), события рывка = детектор
r1 (`r1_retest.find_impulses`, imp_min 80 = 8 ATR, без верхней границы, ATR за 60 баров tf до
старта). Событие = бар старта t_s (i_start) и направление side.

БЕЗ ЗАГЛЯДЫВАНИЯ. Окно рисунка для запроса на баре q = бары [q-N+1 .. q], ВСЕ закрыты к моменту q.
Окно-предвестник события = запрос q = i_start - 1 (кончается строго до старта). Метка запроса q:
первый рывок со стартом в [q+1, q+h] (h баров tf), его направление. Обучение только на первых 2/3
дней контракта; радиус r и порог M2 подбираются на обучении; оценка на последней трети. Запросы
обучения исключают из соседей предвестники в пределах N+h баров (иначе запрос совпал бы сам с собой).

ОЦИФРОВКА (инвариантна к уровню цены и волатильности): приращения close / ATR окна (N-1),
(high-low)/ATR окна (N), объём / медиана объёма окна (N); сводные признаки: наклон, сжатие размаха
(последняя треть к первой), рост объёма, положение close в диапазоне окна, последнее приращение и
последний размах.

M1: ближайшие соседи; расстояние = среднеквадратичное по компонентам (не зависит от N), совпадение =
есть предвестник ближе r; направление = знак суммы направлений совпавших предвестников.
M2: логистическая регрессия (IRLS, без внешних библиотек) отдельно на «рывок вверх» и «вниз» за h
баров; сигнал = max(p) >= порога (квантиль обучения), направление = большая вероятность.
Нуль: метки (пара «рывок, направление») перемешиваются между запросами одного дня, 20 розыгрышей.
Интервалы: бутстрап по дням отложенной трети.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

from trader.lab.footprints import common, r1_retest

DEFAULTS = {"tfs": [1, 5, 15], "Ns": [10, 20, 40], "hs": [3, 10], "atr_n": 60, "imp_bars": 10,
            "imp_min": 80, "imp_max": 100000, "pb": 10, "radii": [0.25, 0.5, 0.75, 1.0, 1.5],
            "draws": 20, "boot": 200, "seed": 0, "min_match": 30, "quantiles": [0.9, 0.95, 0.98, 0.99]}
NFEAT = 6


# ───────────────────────── бары и события ─────────────────────────
def tf_bars(rows: list[list], tf: int) -> list[list]:
    """Минутные бары одного дня -> корзины tf: [ts, o, h, l, c, v], корзина = ts - ts % (tf*60)."""
    if tf == 1:
        return [list(r[:6]) for r in rows]
    size, out = tf * 60, []
    for r in rows:
        b = int(r[0]) - int(r[0]) % size
        if out and out[-1][0] == b:
            o = out[-1]
            o[2] = max(o[2], r[2])
            o[3] = min(o[3], r[3])
            o[4] = r[4]
            o[5] += r[5]
        else:
            out.append([b, r[1], r[2], r[3], r[4], r[5]])
    return out


def events_of_day(bars: list[list], p: dict) -> list[dict]:
    """Рывки дня: [{'s': i_start, 'side': ±1, 'strength': ATR}] по барам tf."""
    ev = r1_retest.find_impulses(bars, {k: p[k] for k in ("atr_n", "imp_bars", "imp_min", "imp_max", "pb")})
    return [{"s": e["i_start"], "side": e["side"], "strength": e["strength"]} for e in ev]


# ───────────────────────── оцифровка ─────────────────────────
def digitize(bars: list[list], q: int, N: int):
    """Окно [q-N+1..q] -> (vec, summary) или None. Использует ТОЛЬКО бары до q включительно."""
    if q < N - 1:
        return None
    w = bars[q - N + 1:q + 1]
    atr = sum(b[2] - b[3] for b in w) / N
    if atr <= 0:
        return None
    incs = [(w[i][4] - w[i - 1][4]) / atr for i in range(1, N)]
    rng_ = [(b[2] - b[3]) / atr for b in w]
    vols = [b[5] for b in w]
    med = statistics.median(vols)
    if med <= 0:
        med = (sum(vols) / N) or 1.0
    vn = [v / med for v in vols]
    t = max(1, N // 3)
    r1, r3 = sum(rng_[:t]) / t, sum(rng_[-t:]) / t
    v1, v3 = sum(vn[:t]) / t, sum(vn[-t:]) / t
    lo, hi = min(b[3] for b in w), max(b[2] for b in w)
    summ = [(w[-1][4] - w[0][4]) / (atr * (N - 1)), math.log((r3 + 0.05) / (r1 + 0.05)),
            math.log((v3 + 0.05) / (v1 + 0.05)), ((w[-1][4] - lo) / (hi - lo)) if hi > lo else 0.5,
            incs[-1], rng_[-1]]
    return incs + rng_ + vn, summ


def rms_dist(a: list[float], b: list[float], cap: float) -> float | None:
    """Среднеквадратичное расстояние; None если оно заведомо больше cap (раннее прекращение)."""
    lim = cap * cap * len(a)
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
        if s > lim:
            return None
    return math.sqrt(s / len(a))


# ───────────────────────── метки ─────────────────────────
def label_at(starts: list[tuple[int, int]], q: int, h: int) -> int:
    """Первый рывок со стартом в [q+1, q+h]: его направление, иначе 0. starts отсортирован."""
    for s, side in starts:
        if s > q + h:
            break
        if s > q:
            return side
    return 0


# ───────────────────────── логистическая регрессия ─────────────────────────
def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        d = M[c][c] or 1e-12
        for j in range(c, n + 1):
            M[c][j] /= d
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c]
                for j in range(c, n + 1):
                    M[r][j] -= f * M[c][j]
    return [M[i][n] for i in range(n)]


def fit_logit(X: list[list[float]], y: list[int], ridge: float = 1e-2, iters: int = 12) -> list[float]:
    """IRLS с L2: веса [b0, b1..bk] по стандартизованным признакам (стандартизация снаружи)."""
    k = len(X[0]) + 1
    w = [0.0] * k
    for _ in range(iters):
        A = [[0.0] * k for _ in range(k)]
        g = [0.0] * k
        for xi, yi in zip(X, y):
            z = w[0] + sum(wj * xj for wj, xj in zip(w[1:], xi))
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            v = p * (1 - p) + 1e-6
            row = [1.0] + xi
            r = (yi - p)
            for a in range(k):
                g[a] += row[a] * r
                for c in range(a, k):
                    A[a][c] += v * row[a] * row[c]
        for a in range(k):
            for c in range(a):
                A[a][c] = A[c][a]
            A[a][a] += ridge
            g[a] -= ridge * w[a]
        step = _solve(A, g)
        w = [wi + si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-5:
            break
    return w


def predict_logit(w: list[float], xi: list[float]) -> float:
    z = w[0] + sum(wj * xj for wj, xj in zip(w[1:], xi))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


# ───────────────────────── статистика на отложенной трети ─────────────────────────
def _agg(items: list[tuple]) -> dict:
    """items: (day, sig, pdir, lab). -> суммы по дням для бутстрапа."""
    d: dict = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for day, sig, pdir, lab in items:
        a = d[day]
        a[0] += 1
        a[3] += 1 if lab else 0
        if sig:
            a[1] += 1
            a[2] += 1 if lab else 0
            if lab and pdir:
                a[4] += 1
                a[5] += 1 if pdir == lab else 0
    return d


def _stat(day_aggs) -> tuple[float | None, float | None, int, int]:
    nq, nm, im, ia, dn, dok = (sum(a[k] for a in day_aggs) for k in range(6))
    lift = ((im / nm) / (ia / nq)) if (nm and ia and nq) else None
    acc = (dok / dn) if dn else None
    return lift, acc, nm, dn


def _pct(xs: list[float], q: float):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))] if xs else None


def evaluate_config(items: list[tuple], test_events: list[tuple], p: dict, rng: random.Random) -> dict:
    """items: (day, sig, pdir, lab, q) отложенной трети; test_events: (day, i_start)."""
    base_items = [(d, s, pd, lab) for d, s, pd, lab, _ in items]
    agg = _agg(base_items)
    days = sorted(agg)
    lift, acc, nm, dn = _stat(list(agg.values()))
    nq = sum(a[0] for a in agg.values())
    base = sum(a[3] for a in agg.values()) / nq if nq else None
    boots_l, boots_a = [], []
    for _ in range(p["boot"]):
        pick = [agg[days[rng.randrange(len(days))]] for _ in days] if days else []
        lf, ac, _, _ = _stat(pick)
        boots_l.append(lf)
        boots_a.append(ac)
    # нуль: пары (рывок, направление) перемешаны между запросами одного дня
    by_day: dict = defaultdict(list)
    for i, (d, s, pd, lab, q) in enumerate(items):
        by_day[d].append(i)
    null_l, null_a = [], []
    for _ in range(p["draws"]):
        labs = [it[3] for it in items]
        for idxs in by_day.values():
            vals = [labs[i] for i in idxs]
            rng.shuffle(vals)
            for i, v in zip(idxs, vals):
                labs[i] = v
        nagg = _agg([(it[0], it[1], it[2], labs[i]) for i, it in enumerate(items)])
        lf, ac, _, _ = _stat(list(nagg.values()))
        null_l.append(lf)
        null_a.append(ac)
    # полнота: доля рывков, перед которыми в [i_start-h, i_start-1] был сигнал
    sigs = defaultdict(set)
    for d, s, pd, lab, q in items:
        if s:
            sigs[d].add(q)
    return {"n_test_queries": nq, "n_test_days": len(days), "base_rate": base, "n_sig": nm,
            "sig_per_day": (nm / len(days)) if days else None, "lift": lift,
            "lift_ci": [_pct(boots_l, 0.025), _pct(boots_l, 0.975)],
            "dir_n": dn, "dir_acc": acc, "dir_ci": [_pct(boots_a, 0.025), _pct(boots_a, 0.975)],
            "null_lift_med": _pct(null_l, 0.5), "null_dir_med": _pct(null_a, 0.5),
            "p3_lift": (sum(1 for x in null_l if lift is not None and x is not None and lift > x), len(null_l)),
            "p3_dir": (sum(1 for x in null_a if acc is not None and x is not None and acc > x), len(null_a)),
            "events_test": len(test_events),
            "completeness": ((sum(1 for d, s0 in test_events
                                  if any((s0 - k) in sigs.get(d, ()) for k in range(1, p["_h"] + 1)))
                              / len(test_events)) if test_events else None)}


# ───────────────────────── конвейер на контракт ─────────────────────────
def analyze(rows: list[list], params: dict | None = None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    rows, notes = common.session_rows(rows)
    day_rows = common.by_day(rows)
    days = sorted(day_rows)
    ntr = max(1, (len(days) * 2) // 3)
    train_days, test_days = set(days[:ntr]), set(days[ntr:])
    rng = random.Random(p["seed"])
    out = {"configs": [], "events": {}, "n_days": len(days), "n_train_days": len(train_days),
           "n_test_days": len(test_days), "notes": notes}
    hmax = max(p["hs"])
    for tf in p["tfs"]:
        bars_d = {d: tf_bars(day_rows[d], tf) for d in days}
        ev_d = {d: events_of_day(bars_d[d], p) for d in days}
        starts_d = {d: sorted((e["s"], e["side"]) for e in ev_d[d]) for d in days}
        n_tr = sum(len(ev_d[d]) for d in train_days)
        n_te = sum(len(ev_d[d]) for d in test_days)
        out["events"][str(tf)] = {"train": n_tr, "test": n_te,
                                  "up": sum(1 for d in days for e in ev_d[d] if e["side"] > 0),
                                  "down": sum(1 for d in days for e in ev_d[d] if e["side"] < 0)}
        for N in p["Ns"]:
            lib = []
            for d in sorted(train_days):
                for e in ev_d[d]:
                    dg = digitize(bars_d[d], e["s"] - 1, N)
                    if dg:
                        lib.append({"d": d, "q": e["s"] - 1, "side": e["side"], "vec": dg[0]})
            rmax = max(p["radii"])
            qrows = []        # по запросу: (day, q, summary, labels{h}, match{r:(flag,signsum)})
            for d in days:
                bars = bars_d[d]
                st = starts_d[d]
                istrain = d in train_days
                for q in range(N - 1, len(bars)):
                    dg = digitize(bars, q, N)
                    if not dg:
                        continue
                    ds = []
                    for it in lib:
                        if istrain and it["d"] == d and abs(it["q"] - q) < N + hmax:
                            continue          # сам предвестник и его окрестность
                        dist = rms_dist(dg[0], it["vec"], rmax)
                        if dist is not None:
                            ds.append((dist, it["side"]))
                    m = {}
                    for r in p["radii"]:
                        ins = [sd for dist, sd in ds if dist <= r]
                        m[r] = (1 if ins else 0, sum(ins))
                    qrows.append((d, q, dg[1], {h: label_at(st, q, h) for h in p["hs"]}, m, istrain))
            tr = [x for x in qrows if x[5]]
            te = [x for x in qrows if not x[5]]
            for h in p["hs"]:
                p["_h"] = h
                test_ev = [(d, e["s"]) for d in test_days for e in ev_d[d]]
                # ── M1: радиус на обучении ──
                base_tr = (sum(1 for x in tr if x[3][h]) / len(tr)) if tr else 0
                best = None
                for r in p["radii"]:
                    nm = sum(1 for x in tr if x[4][r][0])
                    im = sum(1 for x in tr if x[4][r][0] and x[3][h])
                    if nm >= p["min_match"] and base_tr > 0 and im > 0:
                        lf = (im / nm) / base_tr
                        if best is None or lf > best[0]:
                            best = (lf, r)
                if best is not None and te:
                    r = best[1]
                    items = []
                    for d, q, sm, lab, m, _ in te:
                        flag, ssum = m[r]
                        items.append((d, bool(flag), (1 if ssum > 0 else -1 if ssum < 0 else 0), lab[h], q))
                    res = evaluate_config(items, test_ev, p, rng)
                else:
                    res = {"n_sig": 0, "note": "нет радиуса с достаточной поддержкой на обучении" if best is None else "нет отложенных запросов"}
                out["configs"].append({"method": "M1", "tf": tf, "N": N, "h": h, "param": (best[1] if best else None),
                                       "train_lift": (best[0] if best else None), "n_lib": len(lib), **res})
                # ── M2: логрегрессия ──
                if len(tr) > 200:
                    mu = [statistics.fmean(x[2][k] for x in tr) for k in range(NFEAT)]
                    sd = [(statistics.pstdev(x[2][k] for x in tr) or 1.0) for k in range(NFEAT)]
                    Xs = [[(x[2][k] - mu[k]) / sd[k] for k in range(NFEAT)] for x in tr]
                    y_up = [1 if x[3][h] > 0 else 0 for x in tr]
                    y_dn = [1 if x[3][h] < 0 else 0 for x in tr]
                    if sum(y_up) + sum(y_dn) >= 5:
                        w_up = fit_logit(Xs, y_up) if sum(y_up) else [-30.0] + [0.0] * NFEAT
                        w_dn = fit_logit(Xs, y_dn) if sum(y_dn) else [-30.0] + [0.0] * NFEAT
                        sc_tr = [(predict_logit(w_up, xi), predict_logit(w_dn, xi)) for xi in Xs]
                        mx_tr = sorted(max(a, b) for a, b in sc_tr)
                        bestq = None
                        for qu in p["quantiles"]:
                            tau = mx_tr[min(len(mx_tr) - 1, int(qu * len(mx_tr)))]
                            nm = sum(1 for (a, b) in sc_tr if max(a, b) >= tau)
                            im = sum(1 for (a, b), x in zip(sc_tr, tr) if max(a, b) >= tau and x[3][h])
                            if nm >= p["min_match"] and im > 0 and base_tr > 0:
                                lf = (im / nm) / base_tr
                                if bestq is None or lf > bestq[0]:
                                    bestq = (lf, qu, tau)
                        if bestq is not None and te:
                            tau = bestq[2]
                            items = []
                            for d, q, sm, lab, m, _ in te:
                                xi = [(sm[k] - mu[k]) / sd[k] for k in range(NFEAT)]
                                a, b = predict_logit(w_up, xi), predict_logit(w_dn, xi)
                                items.append((d, max(a, b) >= tau, (1 if a > b else -1), lab[h], q))
                            res = evaluate_config(items, test_ev, p, rng)
                        else:
                            res = {"n_sig": 0, "note": "порог не найден на обучении"}
                        out["configs"].append({"method": "M2", "tf": tf, "N": N, "h": h,
                                               "param": (bestq[1] if bestq else None), "train_lift": (bestq[0] if bestq else None), **res})
    return out


def run(arg: dict) -> dict:
    """Задача агента: arg = {symbol_key, since, until, tfs, Ns, hs, draws, boot, seed}."""
    key, since, until = arg["symbol_key"], arg.get("since"), arg.get("until")
    rows = common.load_bars(key, since, until)
    if not rows:
        return {"id": "PRECURSOR", "symbol": key, "window": [since, until], "error": "нет баров в окне"}
    p = {k: arg[k] for k in DEFAULTS if k in arg}
    res = analyze(rows, p)
    return {"id": "PRECURSOR", "symbol": key, "window": [since, until], **res}
