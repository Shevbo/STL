"""Все 18 стратегий реестра на ПАДЕНИИ 12.08 07:00 -> сейчас, RIU6 M1.

Случайная выборка по СОБСТВЕННОЙ схеме каждой стратегии (min..max из params_schema),
qty=1, min_gap_pts=0 (закреплено оператором). Вырожденные пары периодов отброшены
тем же гейтом, что и в кампаниях: fast>=slow у MACD, ema1==ema2, и порядок периодов
у triple_sma/ema_atr; oversold<overbought у осцилляторов.

Финрез считается ТОЛЬКО на срезе периода: equity на конце минус equity на последнем
баре до 12.08 07:00. Бары грузятся с 05.08 — прогрев должен лежать ДО среза.
"""
import asyncio, json, types, os, random, sys, time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from trader.lab.iss_loader import load_bars_iss, fetch_contract_spec
from trader.lab.backtest import run_single_backtest, Bar
from trader.lab.strategies.library import REGISTRY

# Отрезок задаётся аргументами: окно рынка — это ДАННЫЕ задачи, а не константа кода.
# argv: N [strategy_ids|all] [SYMBOL] [T0=YYYY-MM-DD] [LOAD_FROM=YYYY-MM-DD] [TO=YYYY-MM-DD]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "RIU6"
_t0 = sys.argv[4] if len(sys.argv) > 4 else "2026-08-12"
_from = sys.argv[5] if len(sys.argv) > 5 else "2026-08-05"
_to = sys.argv[6] if len(sys.argv) > 6 else "2026-08-14"
# Бары ISS проштампованы МОСКОВСКОЙ стенкой в UTC, поэтому граница отрезка
# записывается как есть, без перевода зон.
T0 = int(datetime(*[int(x) for x in _t0.split("-")], tzinfo=timezone.utc).timestamp())
LOAD_FROM = date(*[int(x) for x in _from.split("-")])
LOAD_TO = date(*[int(x) for x in _to.split("-")])
PINNED = {"symbol", "qty", "min_gap_pts"}


def ok(sid, c):
    f, s = c.get("fast"), c.get("slow")
    if f is not None and s is not None and f >= s:
        return False
    if c.get("ema1") is not None and c["ema1"] == c.get("ema2"):
        return False
    if sid == "triple_sma" and not (c["fast"] < c["mid"] < c["slow"]):
        return False
    # williams_r хранит оба порога как ПОЛОЖИТЕЛЬНЫЕ величины (%R отрицателен),
    # поэтому у него oversold > overbought — общий фильтр вырезал бы всю схему.
    if sid != "williams_r" and c.get("oversold") is not None             and c["oversold"] >= c.get("overbought", 1e9):
        return False
    if not (c.get("allow_long", 1) or c.get("allow_short", 1)):
        return False
    return True


def sample(sid, n, seed=20260812):
    schema = REGISTRY[sid]["params_schema"]
    axes = [(p["key"], int(p["min"]), int(p["max"])) for p in schema
            if p["key"] not in PINNED and isinstance(p.get("min"), (int, float))]
    rnd = random.Random(seed + hash(sid) % 9973)
    seen, out, guard = set(), [], 0
    while len(out) < n and guard < n * 200:
        guard += 1
        c = {k: rnd.randint(lo, hi) for k, lo, hi in axes}
        if not ok(sid, c):
            continue
        key = tuple(sorted(c.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def combo(arg):
    os.nice(5)
    rows, pv, sid, params = arg
    bars = [Bar(**b) for b in rows]
    code = ("from trader.lab.strategies.library import make_on_bar\n"
            f"on_bar = make_on_bar({sid!r})\n")
    mod = types.ModuleType("s")
    exec(compile(code, "<s>", "exec"), mod.__dict__)
    p = {**params, "qty": 1, "symbol": SYMBOL, "min_gap_pts": 0}
    try:
        r = asyncio.run(run_single_backtest(mod, bars, SYMBOL, p, point_value=pv))
    except Exception as exc:
        return {"sid": sid, "params": params, "error": str(exc)[:160]}
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
    return {"sid": sid, "params": params, "net": round(eq[-1]["equity"] - base, 1),
            "fills": len(fills), "first_short": first_short, "max_short": -max_short,
            "open_pos": pos,
            "open_vm": round((bars[-1].close - avgq) * pos * pv, 1) if pos else 0.0}


async def main():
    pv = (await fetch_contract_spec(SYMBOL) or {}).get("point_value") or 1.0
    bars = await load_bars_iss(SYMBOL, LOAD_FROM, LOAD_TO, 1)
    rows = [b.__dict__ for b in bars]
    only = (list(REGISTRY) if len(sys.argv) <= 2 or sys.argv[2] == "all"
            else sys.argv[2].split(","))
    jobs = [(rows, pv, sid, c) for sid in only for c in sample(sid, N)]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=3) as ex:
        out = list(ex.map(combo, jobs, chunksize=4))
    td = [b for b in bars if b.time >= T0]
    hold = (td[-1].close - td[0].open) * pv          # купил на открытии, держал до конца
    print(json.dumps({"point_value": pv, "symbol": SYMBOL, "n": len(out),
                      "secs": round(time.time() - t0), "bars_period": len(td),
                      "buy_and_hold_rub": round(hold, 1),
                      "p_open": td[0].open, "p_close": td[-1].close,
                      "rows": out}, ensure_ascii=False))

asyncio.run(main())
