"""Гейт кандидата rich_fool: настройка должна жить на ОБОИХ кварталах и бить зеркало.

Вершина лидерборда по net — не кандидат. Проба 12.09 показала, почему: та же
настройка давала на RIM6 +13..+26 тыс, а на RIU6 −21..−45 тыс при 54-81 сделке в
каждой ячейке. Знак определялся контрактом, а не параметрами.

ГЕЙТ (все условия разом):
  1. ВОСПРОИЗВОДИМОСТЬ: вектор прибылен на ДВУХ кварталах одного инструмента.
  2. ЗЕРКАЛО: фейд бьёт прямой пробой (invert=1) на ТЕХ ЖЕ параметрах, на обоих
     кварталах. Не бьёт — направление не доказано.
  3. ПОРОГ СДЕЛОК на каждом контракте, иначе это шум.
  4. РИСК: net/max_mae рядом с net. Итог без просадки ничего не значит.
  5. КРАЙ ДИАПАЗОНА: при случайной выборке «края сетки» нет, но если лучшие векторы
     прижаты к границе диапазона, искать надо за ней. Отмечается по каждой оси.

Сортировка по ХУДШЕМУ из двух кварталов: кандидат силён настолько, насколько слаб
его слабейший период.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_gate.py [--min-trades 30] [--top 15] [--quiet]
  --quiet печатает ТОЛЬКО число прошедших — для наблюдения за прогоном.
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# Диапазоны выборки — для проверки «прижато к границе». Должны совпадать с
# RANGES в queue_rich_fool.py: разойдутся — проверка границ начнёт врать.
RANGES = {
    "f_shift": (0, 100), "n_days": (3, 20), "hold_min": (30, 150),
    "sl_beyond_pts": (5, 300), "tp_arm_pts": (50, 1200), "tp_back_pts": (20, 400),
    "qty_first": (1, 3), "max_contracts": (10, 80),
}
PKEYS = list(RANGES) + ["d_coef"]
PAIRS = [("RIM6", "RIU6"), ("SiM6", "SiU6")]

SQL = """
SELECT symbol, params, (params->>'invert')::int AS invert,
       net_profit, total_trades, win_rate, max_mae
FROM optimization_leaderboard
WHERE strategy = 'rich_fool'
  AND (campaign_run LIKE '%-' || $1 || 'fade%' OR campaign_run LIKE '%-' || $1 || 'brk%')
"""


def near_edge(p: dict) -> list[str]:
    """Оси, где значение в крайних 5% диапазона — признак, что искать надо шире."""
    out = []
    for k, (lo, hi) in RANGES.items():
        v = float(p[k])
        span = hi - lo
        if span <= 0:
            continue
        if v <= lo + 0.05 * span:
            out.append(f"{k}↓")
        elif v >= hi - 0.05 * span:
            out.append(f"{k}↑")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--wave", default="rf7", help="префикс кампании: rf7 случайная, rf8 грубая сетка")
    args = ap.parse_args()

    dsn = os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(SQL, args.wave)
    finally:
        await conn.close()

    tbl: dict[tuple, dict] = {}
    for r in rows:
        p = r["params"]
        if isinstance(p, str):
            import json
            p = json.loads(p)
        try:
            key = tuple(int(p[k]) for k in PKEYS)
        except (KeyError, TypeError, ValueError):
            continue
        tbl[(key, r["symbol"], r["invert"])] = {
            "net": r["net_profit"] or 0.0, "tr": r["total_trades"] or 0,
            "win": r["win_rate"] or 0.0, "mae": r["max_mae"] or 0.0, "p": p,
        }

    cands = []
    for a, b in PAIRS:
        for (key, sym, inv) in list(tbl):
            if sym != a or inv != 0:
                continue
            fa = tbl[(key, a, 0)]
            fb = tbl.get((key, b, 0))
            ba, bb = tbl.get((key, a, 1)), tbl.get((key, b, 1))
            if not (fb and ba and bb):
                continue
            if fa["net"] <= 0 or fb["net"] <= 0:
                continue
            if fa["tr"] < args.min_trades or fb["tr"] < args.min_trades:
                continue
            if not (fa["net"] > ba["net"] and fb["net"] > bb["net"]):
                continue
            rm = min(fa["net"] / fa["mae"] if fa["mae"] else 0.0,
                     fb["net"] / fb["mae"] if fb["mae"] else 0.0)
            cands.append({"pair": f"{a}/{b}", "p": fa["p"], "edge": near_edge(fa["p"]),
                          "worst": min(fa["net"], fb["net"]),
                          "na": fa["net"], "nb": fb["net"], "ta": fa["tr"], "tb": fb["tr"],
                          "wa": fa["win"], "wb": fb["win"], "rmae": rm,
                          "ba": ba["net"], "bb": bb["net"]})

    if args.quiet:
        print(f"строк={len(rows)} прошли={len(cands)}")
        return

    print(f"строк в выборке: {len(rows)}")
    print(f"\nпрошли гейт (прибыль на ДВУХ кварталах + зеркало + >={args.min_trades} сделок): "
          f"{len(cands)}")
    if not cands:
        print("КАНДИДАТОВ НЕТ.")
        return
    cands.sort(key=lambda c: -c["worst"])
    print(f"\n{'пара':<12}{'худший':>9}{'net M6':>9}{'net U6':>9}{'сд':>5}{'сд':>5}"
          f"{'win':>5}{'n/MAE':>7}{'зерк M6':>9}{'зерк U6':>9}  параметры")
    for c in cands[:args.top]:
        p = c["p"]
        ps = (f"F={int(p['f_shift']) / 10:.1f} n={p['n_days']} hold={p['hold_min']} "
              f"d={int(p['d_coef']) / 100:.2f} sl={p['sl_beyond_pts']} "
              f"tp={p['tp_arm_pts']}/{p['tp_back_pts']} q1={p['qty_first']} "
              f"bud={p['max_contracts']}")
        if c["edge"]:
            ps += "  ГРАНИЦА: " + ",".join(c["edge"])
        print(f"{c['pair']:<12}{c['worst']:>9,.0f}{c['na']:>9,.0f}{c['nb']:>9,.0f}"
              f"{c['ta']:>5}{c['tb']:>5}{min(c['wa'], c['wb']):>5.2f}{c['rmae']:>7.1f}"
              f"{c['ba']:>9,.0f}{c['bb']:>9,.0f}  {ps}")
    clean = [c for c in cands if not c["edge"]]
    print(f"\nиз них НЕ у границы диапазона: {len(clean)}")


if __name__ == "__main__":
    asyncio.run(main())
