"""Сколько строк сетки — ДУБЛИ, и какие значения осей ничего не меняют.

ПОЧЕМУ ЭТО ЕСТЬ. 22.09.2026 я отчитался по сетке на 2880 наборов: «медиана такая,
плюсовых 93%». Проверка показала, что треть сетки — побитовые дубли: при qty=1
мартингейл k_avg=1.2 даёт int(1×1.2+0.5)=1, то есть ровно k_avg=1.0. Доли и медианы
были взвешены на дубли, а на RIZ6 из 2880 строк различных исходов оказалось 102.
Дубль не врёт в отдельной строке — он врёт в СТАТИСТИКЕ по сетке.

Проверять ДО любого вывода по переборам: если различных исходов много меньше строк,
считать доли и медианы по сетке нельзя, пока дубли не свёрнуты.

    PYTHONPATH=. python scripts/grid_degeneracy.py --base camp-20260922-l40u
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict

import asyncpg

SERVICE = ("symbol", "book_key")


def dead_values(sets: list[tuple[dict, tuple]]) -> dict[str, list[tuple]]:
    """Для каждой оси: пары значений, НИ РАЗУ не давшие разного исхода.

    Сопоставление строго парное: сравниваются наборы, отличающиеся только этой
    осью. Пара, встретившаяся меньше 5 раз, не выводится — это не вывод, а шум.
    """
    axes = sorted({k for p, _o in sets for k in p})
    out: dict[str, list[tuple]] = {}
    for k in axes:
        groups: dict[tuple, dict] = defaultdict(dict)
        for p, outcome in sets:
            if k not in p:
                continue
            rest = tuple(sorted((a, b) for a, b in p.items() if a != k))
            groups[rest][p[k]] = outcome
        pairs: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
        for byv in groups.values():
            vals = sorted(byv)
            for i, v1 in enumerate(vals):
                for v2 in vals[i + 1:]:
                    c = pairs[(v1, v2)]
                    c[0] += 1
                    c[1] += byv[v1] == byv[v2]
        dead = [(v1, v2, n) for (v1, v2), (n, same) in sorted(pairs.items())
                if n >= 5 and same == n]
        if dead:
            out[k] = dead
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    rows = await c.fetch(
        "SELECT params, net_profit, total_trades FROM optimization_leaderboard "
        "WHERE campaign_run LIKE $1 AND net_profit IS NOT NULL", f"{a.base}%")
    await c.close()

    sets = []
    for r in rows:
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else p
        p = {k: v for k, v in p.items() if k not in SERVICE and isinstance(v, (int, float))}
        sets.append((p, (float(r["net_profit"]), r["total_trades"] or 0)))
    if not sets:
        raise SystemExit(f"нет строк по {a.base}*")

    uniq = {o for _p, o in sets}
    print(f"строк {len(sets)}, различных исходов {len(uniq)} "
          f"({100 * len(uniq) / len(sets):.0f}%), дублей {len(sets) - len(uniq)}")
    if len(uniq) * 2 < len(sets):
        print("  ВНИМАНИЕ: больше половины сетки — дубли; доли и медианы по сетке "
              "взвешены на них и как статистика недействительны")

    dead = dead_values(sets)
    if not dead:
        print("мёртвых значений осей не найдено")
        return
    print("\nЗНАЧЕНИЯ, КОТОРЫЕ НИЧЕГО НЕ МЕНЯЮТ (парное сравнение, n = пар):")
    for k, pairs in dead.items():
        for v1, v2, n in pairs:
            print(f"  {k:16} {v1:>6} == {v2:<6} во всех {n} парах")


if __name__ == "__main__":
    asyncio.run(main())
