"""Два лидера-«райдера» shectory_2ema на том же падении RIU6 12.08 07:00 -> сейчас.

Окрестность вокруг каждой строки: если оптимум держится только в одной точке и
разваливается у соседей, это подгонка, а не преимущество. Разножку в пунктах
оператор оставил на нуле; контракт один, RIU6, другие не трогаем.
"""
import asyncio, json, types, os, random, sys, time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from trader.lab.iss_loader import load_bars_iss, fetch_contract_spec
from trader.lab.backtest import run_single_backtest, Bar

SYMBOL, SID = "RIU6", "shectory_2ema"
T0 = int(datetime(2026, 8, 12, 7, tzinfo=timezone.utc).timestamp())
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400

A = {"ema1": 49, "ema2": 192, "bet_step": 4, "bet_max": 29, "avg_max": 8, "avg_step_atr": 18,
     "tp_atr": 60, "sl_frac": 199, "sl_pct": 62, "avg_atr_n": 40, "min_gap_atr": 26,
     "cooldown_min": 399, "cooldown_pct": 12, "dv_bars": 233, "dv_range_pts": 372,
     "allow_long": 0, "allow_short": 1, "tod_m1": 630, "tod_m2": 331,
     "tod_s1": 1, "tod_s2": 3, "tod_s3": 3}
B = {**A, "ema1": 19, "ema2": 144, "bet_step": 2, "avg_step_atr": 21, "tp_atr": 44,
     "sl_frac": 200, "sl_pct": 64, "avg_atr_n": 15, "min_gap_atr": 0, "cooldown_min": 423,
     "cooldown_pct": 19, "dv_bars": 70, "dv_range_pts": 302, "allow_long": 1,
     "tod_m1": 957, "tod_m2": 259, "tod_s1": 0, "tod_s2": 3}

NBR = {
    "A": {"ema1": range(35, 66, 3), "ema2": range(150, 241, 10), "tp_atr": [30, 40, 50, 60],
          "sl_frac": [0, 100, 150, 199], "sl_pct": [0, 40, 62, 90], "avg_max": [4, 6, 8, 10],
          "avg_step_atr": [10, 14, 18, 24, 30], "cooldown_min": [0, 200, 399],
          "min_gap_atr": [0, 13, 26], "dv_bars": [0, 120, 233], "dv_range_pts": [0, 372, 700],
          "allow_long": [0, 1], "allow_short": [1]},
    "B": {"ema1": range(10, 31, 2), "ema2": range(110, 191, 10), "tp_atr": [30, 40, 44, 55],
          "sl_frac": [0, 100, 150, 200], "sl_pct": [0, 40, 64, 90], "avg_max": [4, 6, 8, 10],
          "avg_step_atr": [12, 16, 21, 26, 30], "cooldown_min": [0, 200, 423],
          "min_gap_atr": [0, 10, 20], "dv_bars": [0, 70, 140], "dv_range_pts": [0, 302, 600],
          "allow_long": [0, 1], "allow_short": [1]},
}


def sample(tag, base, n):
    rnd = random.Random(hash(tag) % 9973 + 12)
    axes = {k: list(v) for k, v in NBR[tag].items()}
    seen, out = set(), []
    while len(out) < n and len(seen) < n * 40:
        c = {**base, **{k: rnd.choice(v) for k, v in axes.items()}}
        if c["ema1"] >= c["ema2"]:
            continue
        key = tuple(sorted((k, c[k]) for k in axes))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def combo(arg):
    os.nice(5)
    rows, pv, tag, params = arg
    bars = [Bar(**b) for b in rows]
    mod = types.ModuleType("s")
    exec(compile("from trader.lab.strategies.library import make_on_bar\n"
                 f"on_bar = make_on_bar({SID!r})\n", "<s>", "exec"), mod.__dict__)
    p = {**params, "qty": 1, "symbol": SYMBOL, "min_gap_pts": 0}
    try:
        r = asyncio.run(run_single_backtest(mod, bars, SYMBOL, p, point_value=pv))
    except Exception as exc:
        return {"tag": tag, "params": params, "error": str(exc)[:160]}
    eq = r["equity_curve"]
    base = next((q["equity"] for q in reversed(eq) if q["time"] < T0), eq[0]["equity"])
    fills = [t for t in r["trades"] if (t["time"] or 0) >= T0]
    pos, avgq, sp, first_short, max_short = 0, 0.0, 0, None, 0
    for t in r["trades"]:
        q = t["qty"] * (1 if t["side"] == "buy" else -1)
        if pos == 0 or (pos > 0) == (q > 0):
            avgq = (avgq * abs(pos) + t["price"] * abs(q)) / (abs(pos) + abs(q))
        pos += q
        prev, sp = sp, sp + q
        if sp < 0 <= prev and (t["time"] or 0) >= T0 and first_short is None:
            first_short = t["time"]
        max_short = min(max_short, sp)
    net = round(eq[-1]["equity"] - base, 1)
    return {"tag": tag, "params": params, "net": net, "fills": len(fills),
            "first_short": first_short, "max_short": -max_short, "open_pos": pos,
            "per_contract": round(net / max(1, -max_short), 1),
            "open_vm": round((bars[-1].close - avgq) * pos * pv, 1) if pos else 0.0}


async def main():
    pv = (await fetch_contract_spec(SYMBOL) or {}).get("point_value") or 1.0
    bars = await load_bars_iss(SYMBOL, date(2026, 8, 5), date(2026, 8, 14), 1)
    rows = [b.__dict__ for b in bars]
    jobs = ([(rows, pv, "A", c) for c in sample("A", A, N)]
            + [(rows, pv, "B", c) for c in sample("B", B, N)])
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=3) as ex:
        out = list(ex.map(combo, jobs, chunksize=4))
    td = [b for b in bars if b.time >= T0]
    print(json.dumps({"point_value": pv, "n": len(out), "secs": round(time.time() - t0),
                      "p_open": td[0].open, "p_close": td[-1].close, "rows": out},
                     ensure_ascii=False))

asyncio.run(main())
