"""«Анти-драма»: фейд бешеного импульса (docs/anti-drama-2026.md). Сделки без движка, один шаг на сделку.

Импульс: retest.find_impulse на барах tf (агрегация flex_range.aggregate_tf), высота H >= Hmin ATR (ATR за 60 баров tf
до старта) за <= imp_bars баров tf, по закрытым барам. Вход ПРОТИВ импульса по open первого M1-бара после закрытия
бара tf (покупка +полспреда / продажа -полспреда, тейкер). Тейк на откате r*H от цены входа, стоп sl*H за экстремум
(пик импульса), время T минут, флэт на последнем баре дня (короткие сессии выходных заканчиваются раньше сами).
Порядок внутри M1-бара: close >= open: open-low-high-close, иначе open-high-low-close; обе цели в одном отрезке = стоп.
Тейк лимитный на уровне с проходом 1 тик (мейкер-граница: брокер), стоп и выходы по времени/концу дня рыночные
(полспреда против, тейкер). Одна позиция за раз: события, пришедшиеся на время сделки, пропускаются.
Контроли: (а) зеркало: вход по продолжению с симметричными уровнями; (б) случайные моменты с той же текущей
волатильностью (дециль ATR_тек за 10 баров tf в тот же час) и направлением «против последнего хода».
"""
from __future__ import annotations

import random
from bisect import bisect_right

from trader.lab.footprints import common
from trader.lab.footprints.flex_range import aggregate_tf

INST = {"RI": {"pv": 1.685138, "tick": 10.0, "half": 5.0, "margin": 21701.8},
        "Si": {"pv": 1.0, "tick": 1.0, "half": 0.5, "margin": 12748.24}}


def _path(o, h, lw, c):
    return [o, lw, h, c] if c >= o else [o, h, lw, c]


def trade(rows: list, j0: int, side: int, ext: float, H: float, r: float, sl: float, T: int, inst: dict,
          mirror: bool = False) -> dict | None:
    """Одна сделка по M1-барам дня rows от бара j0 (вход по его open). side +1 покупка, -1 продажа (против импульса:
    после импульса вверх side = -1); ext = пик импульса, H в пунктах. mirror: продолжение, уровни симметричны.
    -> {pnl_pts (после полспреда на входе и рыночных выходов), kind, exit_ts, entry_ts, entry, fills[(kind, price)]}"""
    if j0 >= len(rows):
        return None
    hs, tick = inst["half"], inst["tick"]
    o0 = rows[j0][1]
    if mirror:
        side = -side
        dist = abs(ext - o0) + sl * H                       # то же расстояние стопа, но в другую сторону
        tp, stop = o0 + side * r * H, o0 - side * dist
    else:
        tp = o0 + side * r * H
        stop = ext - side * sl * H                          # за экстремум: для продажи выше пика
        # side=-1 (шорт после импульса вверх): stop = ext + sl*H
    entry = o0 + side * hs
    t_end = rows[j0][0] + T * 60
    out = {"entry_ts": rows[j0][0], "entry": entry, "side": side}
    for j in range(j0, len(rows)):
        ts, o, h, lw, c = rows[j][:5]
        if j > j0 and ts >= t_end:
            px = o - side * hs
            return {**out, "pnl_pts": side * (px - entry), "kind": "T", "exit_ts": ts, "tp_fill": False}
        a = o
        for b in _path(o, h, lw, c)[1:]:
            lo_, hi_ = min(a, b), max(a, b)
            hit_sl = (hi_ >= stop) if side < 0 else (lo_ <= stop)
            hit_tp = (lo_ <= tp - tick) if side < 0 else (hi_ >= tp + tick)
            if side < 0 and o >= stop or side > 0 and o <= stop:
                hit_sl = True
            if hit_sl:
                px = stop - side * hs
                return {**out, "pnl_pts": side * (px - entry), "kind": "sl", "exit_ts": ts, "tp_fill": False}
            if hit_tp:
                return {**out, "pnl_pts": side * (tp - entry), "kind": "tp", "exit_ts": ts, "tp_fill": True}
            a = b
    px = rows[-1][4] - side * hs
    return {**out, "pnl_pts": side * (px - entry), "kind": "eod", "exit_ts": rows[-1][0], "tp_fill": False}


def net(tr: dict, key: str, inst: dict, boundary: str) -> float:
    """Чистый результат сделки в рублях: комиссия входа тейкер; выход тейка мейкер на мейкер-границе."""
    from trader.lab.commission import commission_for
    ts, px = tr["entry_ts"], tr["entry"]
    fee = commission_for(key, px, 1, inst["pv"], taker=True, ts=ts)
    exit_taker = not (boundary == "maker" and tr["tp_fill"])
    fee += commission_for(key, px, 1, inst["pv"], taker=exit_taker, ts=tr["exit_ts"])
    return tr["pnl_pts"] * inst["pv"] - fee


def events_tf(rows: list, tf: int, imp_bars: int, imp_min: float = 150.0) -> tuple[list, list, dict]:
    """События на барах tf: [{'sgn','i','P','atr','move'}], tf-бары, и словарь day->(индексы). imp_min в 0.1 ATR."""
    from trader.lab.runtime import Bar
    from trader.lab.strategies.retest import find_impulse
    agg = aggregate_tf(rows, tf)
    bars = [Bar(time=a[0], open=a[1], high=a[2], low=a[3], close=a[4], volume=a[5]) for a in agg]
    prm = {"imp_min": imp_min, "imp_max": 100000, "imp_bars": imp_bars, "atr_n": 60}
    evs = []
    for i in range(len(bars)):
        for sgn in (1, -1):
            e = find_impulse(bars, i, prm, sgn)
            if e:
                evs.append({"sgn": sgn, **e})
    return evs, agg, {}


def day_index(rows: list) -> dict:
    """ts M1-бара -> (день, индекс в его дне) и дни как списки баров."""
    days = common.by_day(rows)
    keys = sorted(days)
    where = {}
    for k, d in enumerate(keys):
        for i, r in enumerate(days[d]):
            where[r[0]] = (k, i)
    return {"keys": keys, "days": [days[d] for d in keys], "where": where}


def real_trades(di: dict, evs: list, agg: list, hmin: float, p: dict, inst: dict, mirror: bool = False) -> list:
    """Сделки по событиям с H >= hmin ATR: последовательно, одна позиция за раз."""
    out = []
    busy_until: dict = {}
    for e in sorted(evs, key=lambda x: (agg[x["i"]][6], x["i"])):
        if e["move"] / e["atr"] < hmin:
            continue
        w = di["where"].get(agg[e["i"]][6])
        if not w:
            continue
        k, i = w
        rows = di["days"][k]
        j0 = i + 1
        if j0 >= len(rows) or rows[j0][0] < busy_until.get(k, 0):
            continue
        t = trade(rows, j0, -e["sgn"], e["P"], e["move"], p["r"], p["sl"], p["T"], inst, mirror)
        if t:
            busy_until[k] = t["exit_ts"] + 60
            out.append({**t, "day": k, "H": e["move"], "atr": e["atr"], "sgn": e["sgn"], "i_tf": e["i"]})
    return out


def atr_pools(di: dict, agg: list, tf: int) -> tuple[dict, dict]:
    """Пулы случайных моментов на закрытиях корзин tf: (час, дециль ATR_тек за 10 баров tf) и по часу."""
    pts = []
    for j in range(9, len(agg)):
        w = di["where"].get(agg[j][6])
        if w:
            pts.append((w[0], w[1], sum(b[2] - b[3] for b in agg[j - 9:j + 1]) / 10, (agg[j][6] % 86400) // 3600))
    byh: dict = {}
    for k, i, a, h in pts:
        byh.setdefault(h, []).append((a, k, i))
    edges = {h: sorted(x[0] for x in v) for h, v in byh.items()}
    pm: dict = {}
    for h, v in byh.items():
        e = edges[h]
        for a, k, i in v:
            pm.setdefault((h, min(9, bisect_right(e, a) * 10 // max(1, len(e)))), []).append((k, i))
    ph = {h: [(k, i) for _a, k, i in v] for h, v in byh.items()}
    return {"edges": edges, "pm": pm}, ph


def random_trades(di: dict, pools: tuple, trades: list, agg: list, p: dict, inst: dict, w_bars: int, rng: random.Random) -> list:
    """Контроль (б): на каждую реальную сделку случайный момент того же часа и дециля ATR_тек, направление против
    последнего хода (за w_bars M1-баров), H и расстояние стопа как у реальной сделки, экстремум окна."""
    meta, ph = pools
    out = []
    for t in trades:
        j = t["i_tf"]
        a = sum(b[2] - b[3] for b in agg[j - 9:j + 1]) / 10 if j >= 9 else None
        h = (agg[j][6] % 86400) // 3600
        cand = None
        if a is not None and h in meta["edges"]:
            e = meta["edges"][h]
            cand = meta["pm"].get((h, min(9, bisect_right(e, a) * 10 // max(1, len(e)))))
        cand = cand or ph.get(h)
        if not cand:
            continue
        k, i = rng.choice(cand)
        rows = di["days"][k]
        if i < w_bars or i + 1 >= len(rows):
            continue
        move = rows[i][4] - rows[i - w_bars][4]
        if move == 0:
            continue
        sgn = 1 if move > 0 else -1
        win = rows[i - w_bars + 1:i + 1]
        ext = max(x[2] for x in win) if sgn > 0 else min(x[3] for x in win)
        tr = trade(rows, i + 1, -sgn, ext, t["H"], p["r"], p["sl"], p["T"], inst)
        if tr:
            out.append({**tr, "day": k})
    return out


def run(arg: dict) -> dict:
    """arg = {symbol_key, since, until, tfs, imp_bars_list, hmins, rs, sls, Ts, draws, chunk [i, n] по парам (tf, imp_bars)}.
    Возвращает по конфигурациям: сделки обучения (суммы), тест (список чистых результатов), зеркало, случайные."""
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "ANTIDRAMA", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    di = day_index(rows)
    nd = len(di["days"])
    nt = nd * 2 // 3
    pairs = [(tf, b) for tf in arg["tfs"] for b in arg["imp_bars_list"]]
    if arg.get("chunk"):
        i, n = arg["chunk"]
        pairs = pairs[i::n]
    out = {"id": "ANTIDRAMA", "symbol": key, "n_days": nd, "n_train": nt, "configs": []}
    for tf, ib in pairs:
        evs, agg, _ = events_tf(rows, tf, ib)
        pools = atr_pools(di, agg, tf)
        for hm in arg["hmins"]:
            for r in arg["rs"]:
                for sl in arg["sls"]:
                    for T in arg["Ts"]:
                        p = {"r": r / 100.0, "sl": sl, "T": T}
                        tr = real_trades(di, evs, agg, hm, p, inst)
                        mi = real_trades(di, evs, agg, hm, p, inst, mirror=True)
                        item = {"tf": tf, "ib": ib, "hm": hm, "r": r, "sl": sl, "T": T}
                        for b in ("maker", "taker"):
                            item["train_" + b] = round(sum(net(x, key, inst, b) for x in tr if x["day"] < nt), 1)
                            item["mirror_train_" + b] = round(sum(net(x, key, inst, b) for x in mi if x["day"] < nt), 1)
                        item["n_train"] = sum(1 for x in tr if x["day"] < nt)
                        test = [x for x in tr if x["day"] >= nt]
                        item["test_t"] = [round(net(x, key, inst, "taker"), 1) for x in test]
                        item["test_m"] = [round(net(x, key, inst, "maker"), 1) for x in test]
                        item["test_kind"] = [x["kind"] for x in test]
                        item["mirror_test_t"] = [round(net(x, key, inst, "taker"), 1) for x in mi if x["day"] >= nt]
                        item["test_days"] = [x["day"] for x in test]
                        ctrl = []
                        for dd in range(arg.get("draws", 0)):
                            rng = random.Random(5000 + dd)
                            rt = random_trades(di, pools, test, agg, p, inst, max(1, ib * tf), rng)
                            ctrl.append([round(sum(net(x, key, inst, "taker") for x in rt), 1), len(rt)])
                        item["rand"] = ctrl
                        out["configs"].append(item)
    return out
