"""Run the team-46 container backtest over cached 1m bars.

    PYTHONPATH=. python scripts/run_ai46_bt.py --symbols Si,GD --mode both \
        --step 300 --window-days 7 --model-window 600 --refresh 1800 --days 0

--symbols  comma list of cache keys, or 'all'
--mode     zero | proxy | both
--days     keep only the last N days of bars per symbol (0 = full ~6 months)
Outputs per-run timing + metrics (per symbol and portfolio).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
import time

from trader.lab.ai46.backtest import Ai46Backtester
from trader.lab.runtime import Bar

DATA = os.path.join("data", "ai46_bt")


def _load(key: str) -> tuple[list[Bar], str]:
    with open(os.path.join(DATA, key + ".pkl"), "rb") as f:
        d = pickle.load(f)
    return ([Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
             for r in d["rows"]], d.get("secid") or key)


async def _point_values(secids: dict[str, str]) -> dict[str, float]:
    """₽ за пункт по каждому инструменту из instrument_meta (кэш ISS).

    Без них Ai46Backtester считал комиссию при pointValue=1, то есть от «ноционала»
    = цена в пунктах: у BR (цена ~70) это давало 0.64% за сторону вместо сотых долей
    процента и в одиночку топило результат. Комиссия FORTS берётся от НОМИНАЛА
    контракта, а он равен цена × ₽/пункт.
    """
    from trader.config import Settings
    from trader.db import close_pool, get_pool, init_pool
    from trader.lab.market_store import (
        ensure_instrument_meta_table, get_instrument_meta, refresh_instrument_spec,
    )
    s = Settings()
    if not s.lab_db_url:
        print("WARN: lab_db_url не задан — комиссия будет считаться при ₽/пункт=1")
        return {}
    await init_pool(s.lab_db_url)
    pool = get_pool()
    await ensure_instrument_meta_table(pool)
    out: dict[str, float] = {}
    for key, secid in secids.items():
        m = await get_instrument_meta(pool, secid)
        if not m or m.get("point_value") is None:
            try:
                m = await refresh_instrument_spec(pool, secid)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: {key}/{secid}: {type(exc).__name__} {exc}")
                m = None
        if m and m.get("point_value"):
            out[key] = float(m["point_value"])
        else:
            print(f"WARN: нет ₽/пункт для {key}/{secid} — комиссия по нему занижена")
    await close_pool()
    return out


def _all_keys() -> list[str]:
    return sorted(f[:-4] for f in os.listdir(DATA) if f.endswith(".pkl"))


async def _run_one(bars_by, mode, args, pvs) -> dict:
    bt = Ai46Backtester(
        bars_by, step_secs=args.step, window_secs=args.window_days * 86400,
        ofi_mode=mode, model_refresh_secs=args.refresh, model_window=args.model_window,
        llm_enabled=args.llm, blend_tick=args.blend,
        point_values=pvs, taker=True, progress=True,
    )
    t0 = time.time()
    m = await bt.run()
    m["wall_secs"] = round(time.time() - t0, 1)
    return m


def main() -> None:
    global DATA
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="Si,GD")
    p.add_argument("--mode", default="both", choices=["zero", "proxy", "both"])
    p.add_argument("--step", type=int, default=300)
    p.add_argument("--window-days", type=int, default=7, dest="window_days")
    p.add_argument("--model-window", type=int, default=600, dest="model_window")
    p.add_argument("--refresh", type=float, default=1800.0)
    p.add_argument("--days", type=int, default=0, help="keep last N days only (0=full)")
    p.add_argument("--llm", action="store_true")
    p.add_argument("--blend", action="store_true")
    p.add_argument("--data", default=DATA, help="каталог кэша баров")
    p.add_argument("--json-out", default="", dest="json_out", help="файл для полных метрик")
    args = p.parse_args()
    DATA = args.data
    keys = _all_keys() if args.symbols == "all" else [s.strip() for s in args.symbols.split(",")]
    bars_by, secids = {}, {}
    for k in keys:
        b, secid = _load(k)
        if args.days > 0 and b:
            cutoff = b[-1].time - args.days * 86400
            b = [x for x in b if x.time >= cutoff]
        bars_by[k] = b
        secids[k] = secid
    tot = sum(len(v) for v in bars_by.values())
    print(f"loaded {len(bars_by)} symbols, {tot} bars; "
          f"step={args.step}s window={args.window_days}d model_window={args.model_window} "
          f"refresh={args.refresh}s days={args.days or 'full'}")
    pvs = asyncio.run(_point_values(secids))
    print(f"₽/пункт: {pvs}")

    modes = ["zero", "proxy"] if args.mode == "both" else [args.mode]
    out = {}
    for mode in modes:
        m = asyncio.run(_run_one({k: list(v) for k, v in bars_by.items()}, mode, args, pvs))
        out[mode] = m
        per = m.pop("per_symbol", {})
        print(f"\n=== mode={mode}  wall={m['wall_secs']}s  ticks={m['ticks']} ===")
        print(json.dumps(m, ensure_ascii=False))
        for sym, d in sorted(per.items(), key=lambda kv: -kv[1]["net"]):
            print(f"  {sym:10} open={d['opens']:>4} (L{d['longs']}/S{d['shorts']}) "
                  f"close={d['closes']:>4} win={d['wins']:>4} "
                  f"gross={d['pnl'] * 100:+.2f}% fee={d['fees'] * 100:.2f}% net={d['net'] * 100:+.2f}%")
        m["per_symbol"] = per
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nметрики: {args.json_out}")


if __name__ == "__main__":
    main()
