"""Переживает ли отбор rich_fool новые кварталы: гейт rf10 (RIM6/RIU6) против rf11 (RIZ5/RIH6).

Один лидер вне выборки ничего не доказывает: RIZ5 он прошёл, RIH6 провалил. Вопрос
другой — отбор несёт информацию или шум. Если среди прошедших гейт на M6/U6 доля
векторов, прибыльных и бьющих зеркало на Z5/H6, не выше, чем среди НЕ прошедших,
то гейт выбирал шум.

Критерий на квартал: фейд > 0 и фейд > зеркала на тех же параметрах.
d_coef не ключ — калибруется под каждый контракт.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_oos_gate.py [--min-trades 30] [--top 15]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

KEYS = ["f_shift", "n_days", "hold_min", "sl_beyond_pts", "time_exit_min", "tp_arm_pts",
        "tp_back_pts", "qty_first", "max_contracts"]
IN, OUT = ("RIM6", "RIU6"), ("RIZ5", "RIH6")

SQL = """
SELECT symbol, params, net_profit, total_trades, max_mae
FROM optimization_leaderboard
WHERE strategy = 'rich_fool'
  AND (campaign_run LIKE '%-rf10fade%' OR campaign_run LIKE '%-rf10brk%'
       OR campaign_run LIKE '%-rf11fade%' OR campaign_run LIKE '%-rf11brk%')
"""


def ok(t: dict, key: tuple, sym: str, min_tr: int) -> bool | None:
    f, m = t.get((key, sym, 0)), t.get((key, sym, 1))
    if not (f and m):
        return None
    return f[0] > 0 and f[0] > m[0] and f[1] >= min_tr


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    conn = await asyncpg.connect(os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"])
    try:
        rows = await conn.fetch(SQL)
    finally:
        await conn.close()
    t: dict = {}
    for r in rows:
        p = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"])
        key = tuple(int(p.get(k, 0)) for k in KEYS)
        t[(key, r["symbol"], int(p["invert"]))] = (r["net_profit"] or 0, r["total_trades"] or 0,
                                                     r["max_mae"] or 0)
    vecs = {k for (k, s, i) in t if s == IN[0]}
    groups = {"прошли гейт M6/U6": [], "НЕ прошли": []}
    # вне выборки сделок меньше (RIZ5 36, RIH6 42 у лидера): порог для OUT вдвое мягче
    oos_min = a.min_trades // 2
    for k in vecs:
        ins = [ok(t, k, s, a.min_trades) for s in IN]
        outs = [ok(t, k, s, oos_min) for s in OUT]
        if None in ins or None in outs:
            continue
        groups["прошли гейт M6/U6" if all(ins) else "НЕ прошли"].append((k, outs))
    print(f"строк {len(rows)}, векторов с полным набором {sum(len(v) for v in groups.values())}")
    print(f"критерий квартала: фейд>0, фейд>зеркала; сделок >= {a.min_trades} в выборке, "
          f">= {oos_min} вне\n")
    print(f"{'группа':<20}{'векторов':>9}{'Z5 ок':>8}{'H6 ок':>8}{'оба ок':>8}")
    for g, v in groups.items():
        n = len(v) or 1
        z = sum(1 for _, o in v if o[0])
        h = sum(1 for _, o in v if o[1])
        b = sum(1 for _, o in v if all(o))
        print(f"{g:<20}{len(v):>9}{100 * z / n:>7.0f}%{100 * h / n:>7.0f}%{100 * b / n:>7.0f}%")

    four = []
    for k, outs in groups["прошли гейт M6/U6"]:
        if all(outs):
            nets = [t[(k, s, 0)][0] for s in IN + OUT]
            mir = [t[(k, s, 1)][0] for s in IN + OUT]
            tr = [t[(k, s, 0)][1] for s in IN + OUT]
            mae = max(t[(k, s, 0)][2] for s in IN + OUT)
            four.append((min(nets), nets, mir, tr, mae, dict(zip(KEYS, k))))
    four.sort(key=lambda x: -x[0])
    print(f"\nживут на ВСЕХ ЧЕТЫРЁХ кварталах: {len(four)}")
    print(f"{'худший':>9}  {'M6':>8}{'U6':>8}{'Z5':>8}{'H6':>8}  сделки  maxMAE  параметры")
    for w, nets, mir, tr, mae, p in four[:a.top]:
        ps = " ".join(f"{k}={v}" for k, v in p.items() if k != "sl_beyond_pts")
        print(f"{w:>9,.0f}  " + "".join(f"{x:>8,.0f}" for x in nets)
              + f"  {'/'.join(map(str, tr))}  {mae:>7,.0f}  {ps}")


if __name__ == "__main__":
    asyncio.run(main())
