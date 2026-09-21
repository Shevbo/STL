"""Сводка оси с просадкой ПО MTM, а не по закрытым кругам.

ЗАЧЕМ ИМЕННО MTM. `max_drawdown` в лидерборде считается по ЗАКРЫТЫМ кругам и
делится на пиковое ГО лестницы (trader/lab/backtest.py) — открытого минуса
удерживаемой позиции он не видит вовсе. А механизмы вроде flip_back_pct именно
его и растят: позиция не закрывается, пока цена не отыграет долю хода. Поэтому
просадка здесь берётся из КРИВОЙ СОБСТВЕННОГО КАПИТАЛА прогона (она сохраняется
у одиночных комбинаций) и печатается рядом с реализованной.

    PYTHONPATH=. python scripts/axis_report_mtm.py --base fb2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg


def dd_from_curve(curve) -> float:
    """Максимальная просадка кривой капитала в рублях."""
    if isinstance(curve, str):
        curve = json.loads(curve)
    if not curve:
        return 0.0
    vals = []
    for p in curve:
        if isinstance(p, dict):
            v = p.get("equity", p.get("value"))
        elif isinstance(p, (list, tuple)):
            v = p[-1]
        else:
            v = p
        if v is not None:
            vals.append(float(v))
    peak = dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    rows = await c.fetch(
        "SELECT r.id, l.campaign_run, l.net_profit, l.total_trades, l.max_drawdown, "
        "l.max_mae, l.win_rate, b.equity_curve, b.peak_contracts "
        "FROM backtest_runs r "
        "LEFT JOIN optimization_leaderboard l ON l.campaign_run = "
        "  split_part(r.id, '-', 1) || '-' || split_part(r.id, '-', 2) || '-' "
        "  || split_part(r.id, '-', 3) "
        "LEFT JOIN backtest_results b ON b.run_id = r.id "
        "WHERE r.id LIKE $1 ORDER BY r.id", f"%{a.base}%")
    await c.close()

    print(f"{'прогон':44} {'итог руб':>11} {'кругов':>7} {'реализ.прос':>12} "
          f"{'прос.MTM руб':>13} {'max_mae':>9} {'пик контр':>10}")
    seen: dict[tuple, str] = {}
    twins = 0
    for r in rows:
        if r["net_profit"] is None and r["equity_curve"] is None:
            continue
        print(f"{r['id'][:44]:44} {float(r['net_profit'] or 0):+11.0f} "
              f"{r['total_trades'] or 0:7} "
              f"{100 * float(r['max_drawdown'] or 0):11.0f}% "
              f"{dd_from_curve(r['equity_curve']):13.0f} "
              f"{float(r['max_mae'] or 0):9.0f} {r['peak_contracts'] or 0:10}")
        # ПРОБНИК ЖИВОСТИ ОСИ: совпавшие ИТОГ и число кругов у разных вариантов
        # значат, что на i9 старый движок и параметр молча игнорируется. Этим
        # 21.09.2026 был потрачен целый прогон на 32 задания.
        sig = (round(float(r["net_profit"] or 0), 4), r["total_trades"])
        if sig in seen and seen[sig][:-2] != r["id"][:-2]:
            twins += 1
        seen.setdefault(sig, r["id"])


    if twins:
        print()
        print(f"ВНИМАНИЕ: {twins} прогонов совпали с другими бит-в-бит — похоже, "
              f"ось на i9 не работает (старый движок). Проверь applied_token в "
              f"i9_heartbeat и lib_sha (локальный файл сверять в версии с LF).")


if __name__ == "__main__":
    asyncio.run(main())
