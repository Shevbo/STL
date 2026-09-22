"""Зависимости осей: при каком значении одной другие перестают что-либо менять.

ЗАМЕЧАНИЕ ОПЕРАТОРА 21.09.2026: «при определённых значениях одного параметра могут
быть бесполезны переборы множества других осей». Это так, и здесь это сведено в
карту — сначала по КОДУ (что физически отключается), затем проверено по ДАННЫМ
(влияние зависимой оси внутри выключенного состояния должно падать почти в ноль).

Карта нужна не для красоты: случайная сетка по 47 осям дала лишь 16% торгующих
наборов, остальное съели взаимно бессмысленные комбинации. Генератор перебора
обязан её учитывать, иначе i9 считает мусор.

    PYTHONPATH=. python scripts/axis_dependencies.py --base all0921
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics as st
from collections import defaultdict

import asyncpg

# КАРТА ИЗ КОДА (trader/lab/strategies/library.py). Каждая строка: условие на
# ведущей оси -> оси, которые в этом состоянии не читаются вовсе или не могут
# сработать. Проверять при правках движка.
GATES = [
    ("avg_step_atr == 0 и min_gap_atr == 0",
     lambda p: not p.get("avg_step_atr") and not p.get("min_gap_atr"),
     ["avg_max", "k_avg", "avg_from_last", "add_on_profit", "min_gap_pts"],
     "усреднения нет — лестницы и её ручек не существует"),
    ("tp_atr == 0",
     lambda p: not p.get("tp_atr"),
     ["sl_frac"],
     "sl_frac — доля дистанции тейка; без тейка расстояние не от чего считать"),
    ("sl_pct == 0 и sl_frac == 0",
     lambda p: not p.get("sl_pct") and not p.get("sl_frac"),
     ["flip_min_pts", "flip_hold_win", "flip_back_pct", "flip_close_loss"],
     "все удержания флипа армятся ТОЛЬКО со стопом (инвариант «долины»)"),
    ("dv_bars == 0 или dv_range_pts == 0",
     lambda p: not p.get("dv_bars") or not p.get("dv_range_pts"),
     ["dv_bars", "dv_range_pts"],
     "фильтр долины включается только парой"),
    ("cost_atr == 0",
     lambda p: not p.get("cost_atr"),
     ["spread_pts"],
     "полспреда читает только фильтр издержек"),
    ("bet_step == 0",
     lambda p: not p.get("bet_step"),
     ["bet_max"],
     "потолок ставок без самих ставок не нужен"),
    ("reg_n == 0",
     lambda p: not p.get("reg_n"),
     ["reg_band", "reg_mode"],
     "гейт режима выключен — полоса и режим не читаются"),
    ("tod_m1 == 0 и tod_m2 == 0",
     lambda p: not p.get("tod_m1") and not p.get("tod_m2"),
     ["tod_s1", "tod_s2", "tod_s3"],
     "границы окон не заданы — маски сторон не применяются"),
    ("signals2ignor_win == 0 и signals2ignor_lose == 0",
     lambda p: not p.get("signals2ignor_win") and not p.get("signals2ignor_lose"),
     ["signals2ignor_value"],
     "райдер не считает единицы — их цена не нужна"),
    ("gap_auto == 0",
     lambda p: not p.get("gap_auto"),
     ["nd_days"],
     "ND-амплитуда нужна только авто-разножке"),
    ("super_y == 0",
     lambda p: not p.get("super_y"),
     ["super_z"],
     "эскалация выключена"),
    ("int(qty * k_avg) не поднимает ступень",
     lambda p: int(max(1, int(p.get("qty", 1) or 1))
                   * float(p.get("k_avg", 10) or 10) / 10.0 + 0.5)
               == max(1, int(p.get("qty", 1) or 1)),
     ["k_avg"],
     "объём добора целый: при qty=1 любое k_avg < 1.5 = побитовый дубль 1.0"),
    ("flip_min_pts >= 3000",
     lambda p: (p.get("flip_min_pts") or 0) >= 3000,
     ["flip_close_loss", "flip_back_pct", "flip_hold_win"],
     "флип не исполняется никогда — правила его удержания бессмысленны"),
]


def eta(data: list[tuple[dict, float]], key: str) -> tuple[float, int]:
    vals = [(p[key], n) for p, n in data if key in p]
    if len(vals) < 30:
        return 0.0, len(vals)
    groups: dict[float, list[float]] = defaultdict(list)
    for v, n in vals:
        groups[float(v)].append(n)
    if len(groups) < 2:
        return 0.0, len(vals)
    nets = [n for _v, n in vals]
    gm, gv = st.mean(nets), st.pvariance(nets)
    if not gv:
        return 0.0, len(vals)
    between = sum(len(v) * (st.mean(v) - gm) ** 2 for v in groups.values())
    return between / (gv * len(vals)), len(vals)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    rows = await c.fetch(
        "SELECT params, net_profit, total_trades FROM optimization_leaderboard "
        "WHERE campaign_run LIKE $1 AND net_profit IS NOT NULL", f"%{a.base}%")
    await c.close()
    data, traded = [], []
    for r in rows:
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else p
        item = (p, float(r["net_profit"]))
        data.append(item)
        if (r["total_trades"] or 0) > 0:
            traded.append(item)
    print(f"наборов {len(data)}, торгующих {len(traded)} "
          f"({100 * len(traded) / max(len(data), 1):.1f}%)")

    print("\nКАРТА ЗАВИСИМОСТЕЙ (влияние зависимой оси внутри выключенного "
          "состояния / вне него, по торгующим наборам):")
    for name, test, deps, why in GATES:
        inside = [(p, n) for p, n in traded if test(p)]
        outside = [(p, n) for p, n in traded if not test(p)]
        share = 100 * len(inside) / max(len(traded), 1)
        print(f"\n  {name} — {share:.0f}% торгующих наборов; {why}")
        for d in deps:
            e_in, n_in = eta(inside, d)
            e_out, n_out = eta(outside, d)
            verdict = "ось мертва" if e_in * 100 < 0.05 else "ещё влияет"
            print(f"     {d:20} внутри {100 * e_in:5.2f}% (n={n_in:5d}) | "
                  f"вне {100 * e_out:5.2f}% (n={n_out:5d})  -> {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
