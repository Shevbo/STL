"""Хит-парад лидерборда: топ-N строк перебора с колонками, по которым его судят.

ЧЕСТНОЕ ПРЕДУПРЕЖДЕНИЕ, без которого таблицу читать нельзя. Строки лидерборда —
это ЛУЧШИЕ точки подгонки на своём окне, а не кандидаты: отбор шёл по тому же
окну, на котором считался результат. «Окна» в БД (windows_profitable /
windows_total) — нарезка ОДНОГО прогона, а не независимые выборки. Поэтому
верхняя строка таблицы означает «здесь подгонка удалась лучше всего», и ничего
больше; проверка кандидата живёт отдельно (scripts/candidate_gate.py и
пререгистрированные прогоны).

ann_return_go — ДОЛЯ, а не проценты: 4.179 = 418% годовых на ГО.

    ssh hoster ... python scripts/leaderboard_top.py [--top 30] [--min-trades 100]
                                                     [--strategy X] [--symbol Y]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

COLS = ("strategy", "symbol", "campaign_run", "params", "ann_return_go",
        "net_profit", "total_trades", "max_drawdown", "max_mae", "win_rate",
        "windows_profitable", "windows_total", "date_from", "date_to",
        "candidate", "verified_at")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--order", default="ann_return_go",
                    choices=("ann_return_go", "net_profit", "score"))
    ap.add_argument("--strategy")
    ap.add_argument("--symbol")
    ap.add_argument("--all-windows", action="store_true",
                    help="только строки, плюсовые во ВСЕХ своих окнах")
    # Пороги против вырождения. Без них верх таблицы занимают строки с просадкой
    # в ОДИН пункт: ann_return_go = доход/просадку, и деление на ноль даёт
    # «1 000 000 000% годовых» при win_rate 0.10 (мираж нулевого риска,
    # 05.08.2026). Короткое окно — та же болезнь: месяц подгонки не выборка.
    # max_drawdown в этой таблице — ДОЛЯ (медиана 0.44 = 44%), не пункты.
    ap.add_argument("--min-dd", type=float, default=0.05,
                    help="минимальная просадка долей: строка без риска = ошибка учёта")
    # Потолок риска. Без него верх таблицы — мартингейл (bet_step 5, bet_max 29)
    # с просадкой 200-400% от счёта: такую строку нельзя торговать в принципе,
    # счёт кончается раньше, чем строка доходит до своего итога.
    ap.add_argument("--max-dd", type=float, default=0.5,
                    help="потолок просадки долей от счёта")
    ap.add_argument("--min-days", type=int, default=180,
                    help="минимальная длина окна прогона в днях")
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    where, args = ["total_trades >= $1"], [a.min_trades]
    if a.strategy:
        args.append(a.strategy)
        where.append(f"strategy = ${len(args)}")
    if a.symbol:
        args.append(a.symbol)
        where.append(f"symbol = ${len(args)}")
    if a.all_windows:
        where.append("windows_total > 0 AND windows_profitable = windows_total")
    args.append(a.min_dd)
    where.append(f"max_drawdown >= ${len(args)}")
    args.append(a.max_dd)
    where.append(f"max_drawdown <= ${len(args)}")
    args.append(a.min_days)
    where.append(f"date_to - date_from >= ${len(args)}")   # обе колонки date: разность в днях
    # Одинаковый результат повторяется десятками строк: параметр, который ничего
    # не меняет, порождает клонов. Берём по одному представителю на результат.
    args.append(a.top)
    sql = (f"SELECT DISTINCT ON (strategy, symbol, net_profit, total_trades) "
           f"{', '.join(COLS)} FROM optimization_leaderboard "
           f"WHERE {' AND '.join(where)} "
           f"ORDER BY strategy, symbol, net_profit, total_trades, {a.order} DESC")
    rows = await c.fetch(sql, *args[:-1])
    rows = sorted(rows, key=lambda r: -(r[a.order] or 0))[:a.top]

    print(f"топ {len(rows)} по {a.order}, сделок >= {a.min_trades}"
          + (" , плюс во всех окнах" if a.all_windows else ""))
    print(f"{'#':>3} {'стратегия':18} {'симв':7} {'год.ГО':>8} {'нетто':>12} "
          f"{'сделок':>7} {'просад':>9} {'max_mae':>10} {'win':>5} {'окна':>7} "
          f"{'период':>19}")
    for i, r in enumerate(rows, 1):
        per = f"{str(r['date_from'])[:10]}..{str(r['date_to'])[:10]}" if r["date_from"] else "-"
        w = (f"{r['windows_profitable']}/{r['windows_total']}"
             if r["windows_total"] else "-")
        print(f"{i:3d} {(r['strategy'] or '-'):18} {(r['symbol'] or '-'):7} "
              f"{(r['ann_return_go'] or 0):8.2f} {(r['net_profit'] or 0):12.0f} "
              f"{r['total_trades']:7d} {100 * (r['max_drawdown'] or 0):8.0f}% "
              f"{(r['max_mae'] or 0):10.0f} {(r['win_rate'] or 0):5.2f} {w:>7} {per:>19}")
    print("\nпараметры лидеров:")
    for i, r in enumerate(rows[:5], 1):
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else p
        keep = {k: v for k, v in (p or {}).items() if v not in (0, None, "", False)}
        print(f"  {i}. {r['strategy']} {r['symbol']}: {keep}")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
