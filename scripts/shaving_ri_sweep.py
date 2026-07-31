#!/usr/bin/env python3
"""Прогон сетки «Shaving RI 1» и сборка артефакта для стенда.

ЗАПУСКАТЬ НА ХОСТЕРЕ: MOEX ISS с дев-машины недоступен, а очередь i9 держит копию
репозитория без этой стратегии и без опорной серии RTSI.

    ssh hoster 'cd ~/apps/shectory-trader && poetry run nice -n 15 \
        python scripts/shaving_ri_sweep.py --symbol RIU6 \
        --date-from 2026-05-01 --date-to 2026-07-31 --out data/shaving_ri_sweep.json'

Пишет ОДИН json: параметры прогона, таблицу всех комбинаций и ряды для графика
(спред/ось/z + эквити и сделки лучшей комбинации). Стенд читает только его —
в API-процессе ничего не считается (правило изоляции процессов).
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.lab.backtest import run_backtest_grid  # noqa: E402
from trader.lab.iss_loader import fetch_contract_spec, load_bars_iss  # noqa: E402
from trader.lab.strategies.shaving_ri import STRATEGY_META  # noqa: E402

SCRIPT_CODE = "from trader.lab.strategies.shaving_ri import on_bar, on_start, on_stop"

# Сетка ровно на 100 комбинаций: 5 осей × 2 сглаживания × 5 порогов входа × 2 выхода.
GRID = {
    "ema_slow":    [60, 120, 300, 600, 900],   # EMA2 — «ось X»
    "ema_fast":    [5, 30],                    # EMA1 — сглаживание отклонения
    "entry_z_x10": [10, 15, 20, 25, 30],       # вход 1.0 / 1.5 / 2.0 / 2.5 / 3.0 сигмы
    "exit_z_x10":  [0, 5],                     # 0 = ждать оси, 5 = полоса ±0.5 сигмы
}


def _ema_series(xs: list[float], n: int) -> list[float]:
    a = 2.0 / (n + 1)
    e = xs[0]
    out = []
    for x in xs:
        e += a * (x - e)
        out.append(e)
    return out


def _spread_series(ri, bk, ema_slow: int, ema_fast: int, z_win: int, step: int):
    """Тот же расчёт, что в стратегии, но векторно и для картинки: только минуты,
    где есть ОБА бара (иначе отклонение считалось бы по несвежей цене)."""
    bmap = {b.time: b.close for b in bk}
    pts = [(b.time, b.close, bmap[b.time]) for b in ri if b.time in bmap and bmap[b.time] > 0]
    if not pts:
        return []
    ratio = [p[1] / p[2] for p in pts]
    axis = _ema_series(ratio, ema_slow)
    dev = [(ratio[i] - axis[i]) * pts[i][2] for i in range(len(pts))]
    sig = _ema_series(dev, ema_fast)
    out, win, wsum, wsq = [], [], 0.0, 0.0
    for i, d in enumerate(dev):
        win.append(d)
        wsum += d
        wsq += d * d
        if len(win) > z_win:
            old = win.pop(0)
            wsum -= old
            wsq -= old * old
        m = wsum / len(win)
        var = wsq / len(win) - m * m
        sd = var ** 0.5 if var > 0 else 0.0
        if i % step:
            continue
        out.append([pts[i][0], round(pts[i][1], 1), round(pts[i][2], 2),
                    round(sig[i], 1), round(sig[i] / sd, 2) if sd > 0 else 0.0])
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="RIU6")
    ap.add_argument("--basket", default="RTSI")
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--out", default="data/shaving_ri_sweep.json")
    ap.add_argument("--chart-step", type=int, default=5, help="прореживание рядов графика, минут")
    ap.add_argument("--label", default="", help="подпись прогона на стенде")
    args = ap.parse_args()

    d0, d1 = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    base = {p["key"]: p["default"] for p in STRATEGY_META["params_schema"]}
    base["symbol"], base["basket"] = args.symbol, args.basket

    print(f"[1/4] бары {args.symbol} и {args.basket} {d0}..{d1}", flush=True)
    ri = await load_bars_iss(args.symbol, d0, d1, interval=1)
    bk = await load_bars_iss(args.basket, d0, d1, interval=1)
    if not ri or not bk:
        raise SystemExit(f"нет баров: {args.symbol}={len(ri)} {args.basket}={len(bk)}")
    common = len(set(b.time for b in ri) & set(b.time for b in bk))
    print(f"      RI={len(ri)} корзина={len(bk)} общих минут={common}", flush=True)

    spec = await fetch_contract_spec(args.symbol) or {}
    point_value = spec.get("point_value") or 1.0
    initial_margin = spec.get("initial_margin") or 0.0
    print(f"      ₽/пункт={point_value} ГО={initial_margin}", flush=True)

    keys = list(GRID)
    param_sets = [{**base, **dict(zip(keys, c))} for c in itertools.product(*GRID.values())]
    print(f"[2/4] сетка: {len(param_sets)} комбинаций", flush=True)

    # metrics_only: 100 комбинаций × ~40k точек эквити = сотни мегабайт, а на хостере
    # earlyoom убивает по памяти. Сделки и эквити берём вторым проходом, только у
    # победителя.
    graded = await run_backtest_grid(
        SCRIPT_CODE, ri, args.symbol, param_sets,
        timeout=max(900, 60 * len(param_sets)),
        point_value=point_value, initial_margin=initial_margin,
        extra={args.basket: bk}, metrics_only=True,
    )

    rows = []
    for e in graded:
        if not e.get("ok"):
            print("      combo failed:", e.get("error"), flush=True)
            continue
        r = e["result"]
        rows.append({
            "params": {k: e["params"][k] for k in keys},
            "trades": r.get("total_trades"), "net": r.get("net_profit"),
            "win_rate": r.get("win_rate"), "dd": r.get("max_drawdown"),
            "dd_mtm": r.get("max_drawdown_mtm"), "rf": r.get("recovery_factor"),
            "rf_mtm": r.get("recovery_factor_mtm"), "sharpe": r.get("sharpe"),
            "ret": r.get("total_return"), "ann_go": r.get("ann_return_go"),
            "peak": r.get("peak_contracts"), "margin": r.get("margin_used"),
            "net_oos": r.get("net_oos"), "wins_win": r.get("windows_profitable"),
            "wins_tot": r.get("windows_total"), "degrade": r.get("degrade"),
        })
    rows.sort(key=lambda x: (x["net"] is None, -(x["net"] or 0)))
    print(f"[3/4] готово комбинаций: {len(rows)}", flush=True)

    top = rows[0]["params"] if rows else {k: GRID[k][0] for k in keys}
    best_res = (await run_backtest_grid(
        SCRIPT_CODE, ri, args.symbol, [{**base, **top}], timeout=900,
        point_value=point_value, initial_margin=initial_margin,
        extra={args.basket: bk},
    ))[0]
    best = best_res if best_res.get("ok") else None
    eq = (best or {}).get("result", {}).get("equity_curve") or []
    stride = max(1, len(eq) // 2000)
    chart = _spread_series(ri, bk, top["ema_slow"], top["ema_fast"],
                           base["z_win"], max(1, args.chart_step))
    print(f"[4/4] ряды графика: {len(chart)} точек, эквити {len(eq)//stride}", flush=True)

    art = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": args.label, "symbol": args.symbol, "basket": args.basket,
        "date_from": str(d0), "date_to": str(d1),
        "point_value": point_value, "initial_margin": initial_margin,
        "bars_ri": len(ri), "bars_basket": len(bk), "bars_common": common,
        "base_params": base, "grid": GRID, "params_schema": STRATEGY_META["params_schema"],
        "results": rows, "best": top,
        "chart": chart,
        "equity": [[p["time"], round(p["equity"], 1)] for p in eq[::stride]],
        "best_trades": (best or {}).get("result", {}).get("trades") or [],
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(art, f, ensure_ascii=False, separators=(",", ":"))
    print(f"записано {args.out} ({os.path.getsize(args.out) // 1024} КБ)", flush=True)
    for r in rows[:5]:
        print(f"  {r['params']} net={r['net']:.0f}₽ сделок={r['trades']} "
              f"RF={r['rf'] if r['rf'] is None else round(r['rf'], 2)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
