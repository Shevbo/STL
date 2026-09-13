"""Гейт перебора valley_spike: какая пара «дальнее × долина» живёт на всех кварталах.

Читает optimization_leaderboard кампании --tag (по умолчанию vs1, см. queue_valley_spike.py)
и для каждой пары min_gap_amp × dv_bars печатает net/сделки по шести кварталам RI и Si.

Гейт на инструмент (все условия разом, CLAUDE.local.md):
  выборка   плюс на ОБОИХ M6 и U6;
  вне       сколько из M5/U5/Z5/H6 в плюсе;
  частота   минимум сделок по всем шести кварталам против --min-trades (100).
Край сетки выводится рядом: оптимум на границе оси — не кандидат.

ЕДИНИЦЫ. RI M6/U6 — рубли (point_value из instrument_meta), истёкшие RI — пункты
(записи нет, point_value=1.0). Знак и сделки сравнимы, величины RI между ними — нет.
Si везде pv=1.

ЗАПУСК: PYTHONPATH=. $PY scripts/vs_gate.py [--tag vs1] [--min-trades 100]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re

import asyncpg

IN_Q, OUT_Q = ("M6", "U6"), ("M5", "U5", "Z5", "H6")
KEYS = ("min_gap_amp", "dv_bars")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="vs1")
    ap.add_argument("--min-trades", type=int, default=100)
    a = ap.parse_args()

    conn = await asyncpg.connect(os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"])
    try:
        rows = await conn.fetch(
            "SELECT campaign_run, params, net_profit, total_trades FROM optimization_leaderboard "
            "WHERE strategy = 'valley_spike' AND campaign_run LIKE $1", f"camp-%-{a.tag}%")
    finally:
        await conn.close()

    t: dict = {}                       # (inst, quarter, key) -> (net, trades)
    distinct: dict = {}
    for r in rows:
        m = re.search(rf"-{re.escape(a.tag)}(ri|si)([muzh]\d)$", r["campaign_run"])
        if not m:
            continue
        inst, q = m.group(1).upper(), m.group(2).upper()
        inst = "RI" if inst == "RI" else "Si"
        p = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"])
        key = tuple(int(float(p[k])) for k in KEYS)
        t[(inst, q, key)] = (float(r["net_profit"] or 0), int(r["total_trades"] or 0))
        distinct.setdefault((inst, q), set()).add((round(float(r["net_profit"] or 0), 2),
                                                   int(r["total_trades"] or 0)))
    if not t:
        print(f"строк кампании {a.tag} нет")
        return
    print(f"строк {len(rows)}; живость осей (уникальных результатов из 24): "
          + " ".join(f"{i}{q}={len(v)}" for (i, q), v in sorted(distinct.items())))

    keys = sorted({k for (_, _, k) in t})
    grid = {k: sorted({key[n] for key in keys}) for n, k in enumerate(KEYS)}
    for inst in ("RI", "Si"):
        print(f"\n=== {inst}  (разрыв%, окно) | " + " ".join(f"{q:>12}" for q in IN_Q + OUT_Q)
              + " | выборка вне/4 минсд край")
        for key in keys:
            cells, nets, trs = [], [], []
            for q in IN_Q + OUT_Q:
                v = t.get((inst, q, key))
                cells.append(f"{v[0]:>7.0f}/{v[1]:<4d}" if v else f"{'—':>12}")
                if v:
                    nets.append((q, v[0]))
                    trs.append(v[1])
            ins_ok = all(n > 0 for q, n in nets if q in IN_Q) and len([1 for q, _ in nets if q in IN_Q]) == 2
            oos = sum(1 for q, n in nets if q in OUT_Q and n > 0)
            edge = [k for n, k in enumerate(KEYS) if key[n] in (grid[k][0], grid[k][-1])]
            mark = " <<" if ins_ok and oos >= 3 and trs and min(trs) >= a.min_trades and not edge else ""
            print(f"  ({key[0]:>2}%, {key[1]:>3}) | " + " ".join(cells)
                  + f" | {'да ' if ins_ok else 'нет'}     {oos}/4 {min(trs) if trs else 0:>5} {','.join(edge) or '-'}{mark}")
    print("\n<< = плюс на обоих кварталах выборки, >=3 из 4 вне, сделок не меньше порога, не край сетки")


if __name__ == "__main__":
    asyncio.run(main())
