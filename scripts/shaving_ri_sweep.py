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
import statistics as st
import sys
import time
from datetime import date, datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.lab.backtest import run_backtest_grid  # noqa: E402
from trader.lab.iss_loader import fetch_contract_spec, load_bars_iss  # noqa: E402
from trader.lab.strategies.shaving_ri import STRATEGY_META  # noqa: E402

SCRIPT_CODE = "from trader.lab.strategies.shaving_ri import on_bar, on_start, on_stop"
ISS_HIST = "https://iss.moex.com/iss/history/engines/futures/markets/forts/securities"
# ГО ближнего RI как доля номинала (31.07.2026: 22 361.73 / (88 140 × 1.597146)).
MARGIN_FRAC = 0.159

GRIDS = {
    # Разведка: 100 комбинаций, широко и грубо.
    "base": {
        "ema_slow":    [60, 120, 300, 600, 900],   # EMA2 — «ось X»
        "ema_fast":    [5, 30],                    # EMA1 — сглаживание отклонения
        "entry_z_x10": [10, 15, 20, 25, 30],       # вход 1.0 / 1.5 / 2.0 / 2.5 / 3.0 сигмы
        "exit_z_x10":  [0, 5],                     # 0 = ждать оси, 5 = полоса ±0.5 сигмы
    },
    # Уточнение вокруг лидера трёх месяцев (120/30/2.5σ/0). Освобождены две оси,
    # которые бьют по главному риску одной ноги: денежный стоп и время удержания.
    "refine-a": {
        "ema_slow":     [60, 90, 120, 160, 210, 280],
        "ema_fast":     [10, 15, 20, 30, 45],
        "entry_z_x10":  [18, 20, 22, 25, 28],
        "exit_z_x10":   [0, 3, 6, 10],
        "sl_pts":       [0, 150, 300, 600],
        "max_hold_min": [60, 120, 240, 480],
    },
    # Уточнение вокруг лидера июля (60/5/1.0σ/0) — другой режим: быстрая ось,
    # низкий порог, много сделок.
    "refine-b": {
        "ema_slow":     [30, 45, 60, 90, 120],
        "ema_fast":     [3, 5, 8, 12, 20],
        "entry_z_x10":  [8, 10, 12, 14, 17],
        "exit_z_x10":   [0, 3, 6, 10],
        "sl_pts":       [0, 150, 300, 600],
        "max_hold_min": [60, 120, 240, 480],
    },
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


async def _spec_for(symbol: str, d0: date, d1: date) -> tuple[float, float]:
    """(₽/пункт, ГО) для контракта — включая ИСТЁКШИЙ.

    fetch_contract_spec ходит в текущий листинг, а истёкший контракт из него
    выпадает и возвращает пусто. Раньше это молча давало point_value=1.0: P&L
    печатался в пунктах под видом рублей, а биржевой сбор (он берётся с номинала
    price×point_value) занижался в 1.6 раза — то есть холдаут получал скидку на
    комиссию ровно там, где проверяется, окупается ли стратегия. Поэтому для
    истёкших выводим шаг из дневной истории самой биржи:

        ₽/пункт = OPENPOSITIONVALUE / (OPENPOSITION × SETTLEPRICE)

    (рублёвая стоимость открытых позиций, делённая на их же объём в пунктах).
    ГО оценивается по той же доле от номинала, что у текущего ближнего контракта.
    """
    spec = await fetch_contract_spec(symbol) or {}
    if spec.get("point_value"):
        return float(spec["point_value"]), float(spec.get("initial_margin") or 0.0)

    url = (f"{ISS_HIST}/{symbol}.json?iss.meta=off&iss.only=history"
           f"&from={d0}&till={d1}")
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "STL/1.0"}) as c:
        h = (await c.get(url)).json().get("history", {})
    rows = [dict(zip(h.get("columns", []), r)) for r in h.get("data", [])]
    pvs = [r["OPENPOSITIONVALUE"] / (r["OPENPOSITION"] * r["SETTLEPRICE"])
           for r in rows
           if (r.get("OPENPOSITIONVALUE") and r.get("OPENPOSITION") and r.get("SETTLEPRICE"))]
    if not pvs:
        raise SystemExit(
            f"не удалось определить ₽/пункт для {symbol} за {d0}..{d1}. "
            f"Прогон с point_value=1.0 занизил бы комиссию и выдал пункты за рубли — "
            f"задайте --point-value явно.")
    pv = st.median(pvs)
    px = st.median([r["SETTLEPRICE"] for r in rows if r.get("SETTLEPRICE")])
    margin = MARGIN_FRAC * px * pv
    print(f"      спека истёкшего контракта выведена из истории: ₽/пункт={pv:.4f} "
          f"(по {len(pvs)} дням), ГО≈{margin:.0f} (оценка {MARGIN_FRAC:.0%} от номинала)",
          flush=True)
    return pv, margin


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="RIU6")
    ap.add_argument("--basket", default="RTSI")
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--out", default="data/shaving_ri_sweep.json")
    ap.add_argument("--chart-step", type=int, default=5, help="прореживание рядов графика, минут")
    ap.add_argument("--label", default="", help="подпись прогона на стенде")
    ap.add_argument("--grid", default="base", choices=sorted(GRIDS), help="какую сетку гнать")
    ap.add_argument("--from-artifact", default="",
                    help="взять готовые комбинации из артефакта вместо сетки — так конфиг, "
                         "отобранный на одном контракте, проверяется на другом (холдаут)")
    ap.add_argument("--top", type=int, default=30,
                    help="сколько лучших по ПЛАТО взять из артефакта")
    ap.add_argument("--point-value", type=float, default=0.0, help="перебить ₽/пункт вручную")
    ap.add_argument("--initial-margin", type=float, default=0.0, help="перебить ГО вручную")
    ap.add_argument("--chunk", type=int, default=400,
                    help="комбинаций на подпроцесс: артефакт пишется после каждого куска, "
                         "поэтому падение на четвёртом часу не стирает всё сделанное")
    args = ap.parse_args()
    GRID = GRIDS[args.grid]

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

    point_value, initial_margin = await _spec_for(args.symbol, d0, d1)
    if args.point_value:
        point_value = args.point_value
    if args.initial_margin:
        initial_margin = args.initial_margin
    print(f"      ₽/пункт={point_value:.4f} ГО={initial_margin:.0f}", flush=True)

    if args.from_artifact:
        with open(args.from_artifact, encoding="utf-8") as f:
            src = json.load(f)
        GRID = src["grid"]
        keys = list(GRID)
        picked = sorted((r for r in src["results"] if (r["trades"] or 0) > 0),
                        key=lambda r: -(r.get("plateau") or r["net"] or 0))[:args.top]
        param_sets = [{**base, **p["params"]} for p in picked]
        print(f"[2/4] холдаут: {len(param_sets)} комбинаций из {args.from_artifact}", flush=True)
    else:
        keys = list(GRID)
        param_sets = [{**base, **dict(zip(keys, c))} for c in itertools.product(*GRID.values())]
        print(f"[2/4] сетка «{args.grid}»: {len(param_sets)} комбинаций", flush=True)

    def _row(e: dict) -> dict:
        r = e["result"]
        return {
            "params": {k: e["params"][k] for k in keys},
            "trades": r.get("total_trades"), "net": r.get("net_profit"),
            "win_rate": r.get("win_rate"), "dd": r.get("max_drawdown"),
            "dd_mtm": r.get("max_drawdown_mtm"), "rf": r.get("recovery_factor"),
            "rf_mtm": r.get("recovery_factor_mtm"), "sharpe": r.get("sharpe"),
            "ret": r.get("total_return"), "ann_go": r.get("ann_return_go"),
            "peak": r.get("peak_contracts"), "margin": r.get("margin_used"),
            "net_oos": r.get("net_oos"), "wins_win": r.get("windows_profitable"),
            "wins_tot": r.get("windows_total"), "degrade": r.get("degrade"),
        }

    def _write(art: dict) -> None:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(art, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, args.out)          # атомарно: стенд не поймает полуфайл

    meta = {
        "label": args.label, "grid_name": args.grid,
        "symbol": args.symbol, "basket": args.basket,
        "date_from": str(d0), "date_to": str(d1),
        "point_value": point_value, "initial_margin": initial_margin,
        "bars_ri": len(ri), "bars_basket": len(bk), "bars_common": common,
        "base_params": base, "grid": GRID, "params_schema": STRATEGY_META["params_schema"],
    }

    # Кусками: артефакт пишется после каждого, поэтому падение (earlyoom на общем
    # хостере вполне реален) не стирает уже посчитанное. metrics_only — иначе
    # тысячи комбинаций × ~40k точек эквити это гигабайты.
    rows: list[dict] = []
    t0 = time.monotonic()
    for i in range(0, len(param_sets), args.chunk):
        part = param_sets[i:i + args.chunk]
        graded = await run_backtest_grid(
            SCRIPT_CODE, ri, args.symbol, part,
            timeout=max(900, 60 * len(part)),
            point_value=point_value, initial_margin=initial_margin,
            extra={args.basket: bk}, metrics_only=True,
        )
        for e in graded:
            if e.get("ok"):
                rows.append(_row(e))
            else:
                print("      combo failed:", e.get("error"), flush=True)
        done = i + len(part)
        el = time.monotonic() - t0
        print(f"      {done}/{len(param_sets)}  прошло {el / 60:.1f} мин, "
              f"осталось ~{el / done * (len(param_sets) - done) / 60:.1f} мин "
              f"({done / el:.1f} комб/с)", flush=True)
        _write({**meta, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "partial": done < len(param_sets), "done": done, "total": len(param_sets),
                "results": rows, "best": {}, "chart": [], "equity": [], "best_trades": []})

    # Плато вместо пика: одиночный максимум в убыточной окрестности это подгонка.
    # Для каждой точки берём её соседей на один шаг по каждой оси и считаем
    # МЕДИАНУ по точке вместе с соседями — устойчивый счёт, который нельзя выиграть
    # одной удачной комбинацией.
    # На разреженном наборе (холдаут) соседей нет по построению — плато не считаем,
    # иначе оно было бы просто копией net и выдавало бы себя за подтверждение.
    idx = {tuple(r["params"][k] for k in keys): r for r in rows}
    posn = {k: {v: j for j, v in enumerate(GRID[k])} for k in keys}
    for r in rows if not args.from_artifact else []:
        cur = tuple(r["params"][k] for k in keys)
        nb = []
        for ax, k in enumerate(keys):
            j = posn[k][cur[ax]]
            for jj in (j - 1, j + 1):
                if 0 <= jj < len(GRID[k]):
                    t = list(cur)
                    t[ax] = GRID[k][jj]
                    n = idx.get(tuple(t))
                    if n is not None:
                        nb.append(n["net"] or 0.0)
        r["nb_n"] = len(nb)
        r["nb_pos"] = sum(1 for x in nb if x > 0)
        r["plateau"] = st.median(nb + [r["net"] or 0.0])
    rows.sort(key=lambda x: (x["net"] is None, -(x["net"] or 0)))
    print(f"[3/4] готово комбинаций: {len(rows)}", flush=True)

    # График рисуем по лучшему ПЛАТО, а не по пику: именно эту точку имеет смысл
    # смотреть глазами. Пик остаётся в таблице.
    traded = [r for r in rows if (r["trades"] or 0) > 0]
    top = (max(traded, key=lambda r: r.get("plateau", r["net"] or 0))["params"] if traded
           else (rows[0]["params"] if rows else {k: GRID[k][0] for k in keys}))
    meta["top_by_net"] = rows[0]["params"] if rows else {}
    meta["top_by_plateau"] = top
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

    _write({**meta, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "partial": False, "done": len(rows), "total": len(param_sets),
            "results": rows, "best": top, "chart": chart,
            "equity": [[p["time"], round(p["equity"], 1)] for p in eq[::stride]],
            "best_trades": (best or {}).get("result", {}).get("trades") or []})
    print(f"записано {args.out} ({os.path.getsize(args.out) // 1024} КБ)", flush=True)
    pos = sum(1 for r in traded if (r["net"] or 0) > 0)
    print(f"\nсо сделками {len(traded)}, прибыльных {pos} "
          f"({100 * pos // max(1, len(traded))}%), медиана "
          f"{st.median([r['net'] or 0 for r in traded]):.0f} ₽" if traded else "\nсделок нет")
    print("пик по итогу:")
    for r in rows[:3]:
        pl = f" плато={r['plateau']:.0f}₽ соседей+={r['nb_pos']}/{r['nb_n']}" if "plateau" in r else ""
        print(f"  {r['params']} net={r['net']:.0f}₽ сделок={r['trades']} "
              f"вне_выборки={r['net_oos'] or 0:.0f}₽{pl}", flush=True)
    if traded and "plateau" in traded[0]:
        print("лучшее плато:")
        for r in sorted(traded, key=lambda x: -x["plateau"])[:3]:
            print(f"  {r['params']} плато={r['plateau']:.0f}₽ net={r['net']:.0f}₽ "
                  f"сделок={r['trades']} соседей+={r['nb_pos']}/{r['nb_n']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
