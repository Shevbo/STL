"""Сводка оси «разножка × ATR без выходных»: что дал каждый порог.

Кампании называет scripts/queue_gap_axis.py: `<база><bar|book><порог>w<0|1>`.
Здесь они сводятся в таблицу, чтобы видеть обе оси сразу и отдельно по каждому
виду исполнения.

ГЛАВНАЯ ПРОВЕРКА ЖИВОСТИ ОСИ: строки w0 и w1 при одном пороге обязаны РАЗЛИЧАТЬСЯ
по числу сделок или по итогу. Совпали — значит ось на i9 не работает (движок там
старый), и числа читать нельзя (память: пробник живости оси).

    PYTHONPATH=. python scripts/gap_axis_report.py [--base gapv2]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re

import asyncpg


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="gapv2")
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    rows = await c.fetch(
        "SELECT campaign_run, net_profit, total_trades, max_drawdown, max_mae, "
        "win_rate, date_from, date_to FROM optimization_leaderboard "
        "WHERE campaign_run LIKE $1", f"%{a.base}%")
    runs = await c.fetch(
        "SELECT status, count(*) n FROM backtest_runs WHERE id LIKE $1 GROUP BY status",
        f"%{a.base}%")
    await c.close()

    cells: dict[tuple[str, int, int], dict] = {}
    for r in rows:
        m = re.search(rf"{a.base}(bar|book)(\d+)w(\d)", r["campaign_run"] or "")
        if m:
            cells[(m.group(1), int(m.group(2)), int(m.group(3)))] = r

    print("задания:", {r["status"]: r["n"] for r in runs})
    for mode in ("bar", "book"):
        have = sorted({g for (md, g, _w) in cells if md == mode})
        if not have:
            continue
        print(f"\n=== исполнение: {'по барам' if mode == 'bar' else 'ПО СТАКАНУ'} ===")
        print(f"{'разножка':>9} | {'ATR с выходными':>28} | {'ATR без выходных':>28}")
        print(f"{'':>9} | {'итог руб':>12} {'сделок':>7} {'прос':>6} | "
              f"{'итог руб':>12} {'сделок':>7} {'прос':>6}")
        for g in have:
            out = [f"{g:9}"]
            for w in (0, 1):
                r = cells.get((mode, g, w))
                if r is None:
                    out.append(f"{'—':>12} {'—':>7} {'—':>6}")
                else:
                    out.append(f"{float(r['net_profit'] or 0):+12.0f} "
                               f"{r['total_trades']:7d} "
                               f"{100 * float(r['max_drawdown'] or 0):5.0f}%")
            print(" | ".join(out))
        # Живость оси ATR: где-то w0 и w1 обязаны разойтись.
        same = [g for g in have
                if (mode, g, 0) in cells and (mode, g, 1) in cells
                and cells[(mode, g, 0)]["total_trades"] == cells[(mode, g, 1)]["total_trades"]
                and abs(float(cells[(mode, g, 0)]["net_profit"] or 0)
                        - float(cells[(mode, g, 1)]["net_profit"] or 0)) < 1e-6]
        if same and len(same) == len(have):
            print("  ВНИМАНИЕ: w0 и w1 совпали на ВСЕХ порогах — ось ATR на i9 не "
                  "работает, числа читать нельзя")
        elif same:
            print(f"  порогов, где ATR без выходных ничего не изменил: {same}")


if __name__ == "__main__":
    asyncio.run(main())
