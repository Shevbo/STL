"""Сводка прогонов «бар против стакана»: что осталось от хит-парада при реальном
исполнении.

Пары ставит scripts/queue_book_top.py: кампания называется
`<база>bar<NN>` и `<база>book<NN>`, где NN — место строки в топе. Здесь они
сводятся обратно: одна строка таблицы = одна строка лидерборда, посчитанная
дважды на ОДНОМ окне и ОДНИХ параметрах, с разницей только в исполнении.

    PYTHONPATH=. python scripts/book_pairs_report.py [--base bookoos]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re

import asyncpg


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="bookoos")
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    rows = await c.fetch(
        "SELECT campaign_run, strategy, symbol, net_profit, total_trades, "
        "max_drawdown, win_rate, date_from, date_to "
        "FROM optimization_leaderboard WHERE campaign_run LIKE $1", f"%{a.base}%")
    runs = await c.fetch(
        "SELECT status, count(*) n FROM backtest_runs WHERE id LIKE $1 GROUP BY status",
        f"%{a.base}%")
    await c.close()

    pairs: dict[int, dict] = {}
    for r in rows:
        m = re.search(rf"{a.base}(bar|book)(\d+)", r["campaign_run"] or "")
        if not m:
            continue                       # прогоны без номера — первая попытка, дубли
        rank = int(m.group(2))
        pairs.setdefault(rank, {})[m.group(1)] = r

    print("задания:", {r["status"]: r["n"] for r in runs})
    done = [k for k, v in sorted(pairs.items()) if "bar" in v and "book" in v]
    print(f"пар готово: {len(done)} из {len(pairs)}\n")
    print(f"{'#':>3} {'стратегия':17} {'бар руб':>11} {'стакан руб':>11} "
          f"{'цена испол.':>12} {'сделок':>7} {'просад':>8}")
    tot_bar = tot_book = 0.0
    for rank in done:
        b, k = pairs[rank]["bar"], pairs[rank]["book"]
        nb, nk = float(b["net_profit"] or 0), float(k["net_profit"] or 0)
        tot_bar += nb
        tot_book += nk
        print(f"{rank:3d} {b['strategy']:17} {nb:+11.0f} {nk:+11.0f} "
              f"{nk - nb:+12.0f} {b['total_trades']:7d} "
              f"{100 * float(k['max_drawdown'] or 0):7.0f}%")
    if done:
        n = len(done)
        print(f"\nитого по {n} парам: бар {tot_bar:+.0f} руб, стакан {tot_book:+.0f} руб, "
              f"исполнение съело {tot_bar - tot_book:+.0f}")
        print(f"плюсовых по бару {sum(1 for r in done if (pairs[r]['bar']['net_profit'] or 0) > 0)}, "
              f"по стакану {sum(1 for r in done if (pairs[r]['book']['net_profit'] or 0) > 0)}")


if __name__ == "__main__":
    asyncio.run(main())
