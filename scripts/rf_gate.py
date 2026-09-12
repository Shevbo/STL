"""Гейт кандидата rich_fool: ищем настройку, которая живёт на ОБОИХ кварталах.

Зачем отдельный скрипт. Вершина лидерборда по net — это не кандидат. Проба 12.09
показала, почему: узкая лестница при одних и тех же параметрах даёт на RIM6
+13..+26 тыс, а на RIU6 −21..−45 тыс при 54-81 сделке в каждой ячейке. Знак
определяется контрактом, а не настройкой, поэтому «лучшая строка» — это просто тот
квартал, которому повезло.

ГЕЙТ (все условия разом, по требованию оператора из CLAUDE.local.md):
  1. ВОСПРОИЗВОДИМОСТЬ: строка с ТЕМИ ЖЕ параметрами прибыльна на ДВУХ кварталах
     одного инструмента (M6 и U6). Одна пара = один кандидат.
  2. ЗЕРКАЛО: фейд обязан бить прямой пробой (invert=1) на ТЕХ ЖЕ параметрах.
     Если пробой не хуже — направление не доказано.
  3. ПОРОГ СДЕЛОК: минимум min_trades на каждом контракте, иначе это шум.
  4. НЕ КРАЙ СЕТКИ: оптимум не должен сидеть на границе ни одной оси.
  5. РИСК: net/max_mae выводится рядом с net — итог без просадки ничего не значит.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_gate.py [--min-trades 30] [--top 15]
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# Оси ВОЛНЫ 2 (расширены за края, в которые упёрлась волна 1). Проверка «край
# сетки» сверяется именно с ними, поэтому при смене сетки этот словарь обязан
# меняться вместе с queue_rich_fool.py — иначе гейт объявит внутренним то, что
# на самом деле стоит на границе.
AXES = {
    "d_coef": [5, 10, 20],
    "hold_min": [5, 10, 20, 30],
    "step_count": [5, 8, 12, 20],
    "vol_mult": [16, 20, 25, 30],
    "sl_price_pct": [10, 20, 35, 50],
    "trail_tp_pct": [10, 15, 25],
    "n_days": [5, 10],
}
PAIRS = [("RIM6", "RIU6"), ("SiM6", "SiU6")]
PKEYS = list(AXES)

SQL = """
SELECT symbol,
       params->>'d_coef'       AS d_coef,
       params->>'hold_min'     AS hold_min,
       params->>'n_days'       AS n_days,
       params->>'step_count'   AS step_count,
       params->>'vol_mult'     AS vol_mult,
       params->>'sl_price_pct' AS sl_price_pct,
       params->>'trail_tp_pct' AS trail_tp_pct,
       (params->>'invert')::int AS invert,
       net_profit, total_trades, win_rate, max_mae
FROM optimization_leaderboard
WHERE strategy = 'rich_fool' AND campaign_run LIKE '%rf4%'
"""


def on_edge(p: dict) -> list[str]:
    """Оси, у которых значение стоит на границе сетки."""
    out = []
    for k, vals in AXES.items():
        v = int(p[k])
        if v == min(vals) or v == max(vals):
            out.append(k)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    dsn = os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(SQL)
    finally:
        await conn.close()

    # (params, symbol, invert) -> метрики
    tbl: dict[tuple, dict] = {}
    for r in rows:
        key = tuple(r[k] for k in PKEYS)
        tbl[(key, r["symbol"], r["invert"])] = {
            "net": r["net_profit"] or 0.0, "tr": r["total_trades"] or 0,
            "win": r["win_rate"] or 0.0, "mae": r["max_mae"] or 0.0,
        }
    print(f"строк в выборке: {len(rows)}")
    if not rows:
        return

    cands = []
    for a, b in PAIRS:
        keys = {k for (k, sym, inv) in tbl if sym == a and inv == 0}
        for k in keys:
            fa, fb = tbl.get((k, a, 0)), tbl.get((k, b, 0))
            if not fa or not fb:
                continue
            # 1. прибыльна на ОБОИХ кварталах
            if fa["net"] <= 0 or fb["net"] <= 0:
                continue
            # 3. порог сделок на каждом
            if fa["tr"] < args.min_trades or fb["tr"] < args.min_trades:
                continue
            # 2. зеркало: фейд обязан бить пробой на обоих
            ba, bb = tbl.get((k, a, 1)), tbl.get((k, b, 1))
            if not ba or not bb:
                continue
            if not (fa["net"] > ba["net"] and fb["net"] > bb["net"]):
                continue
            p = dict(zip(PKEYS, k))
            edges = on_edge(p)
            worst = min(fa["net"], fb["net"])
            rmae_a = fa["net"] / fa["mae"] if fa["mae"] else 0.0
            rmae_b = fb["net"] / fb["mae"] if fb["mae"] else 0.0
            cands.append({
                "pair": f"{a}/{b}", "p": p, "edges": edges,
                "net_a": fa["net"], "net_b": fb["net"], "worst": worst,
                "tr_a": fa["tr"], "tr_b": fb["tr"],
                "win_a": fa["win"], "win_b": fb["win"],
                "mae_a": fa["mae"], "mae_b": fb["mae"],
                "rmae": min(rmae_a, rmae_b),
                "brk_a": ba["net"], "brk_b": bb["net"],
            })

    print(f"\nпрошли гейт (прибыль на ДВУХ кварталах + зеркало + >={args.min_trades} сделок): "
          f"{len(cands)}")
    if not cands:
        print("КАНДИДАТОВ НЕТ. Ни одна настройка не прибыльна на обоих кварталах одного\n"
              "инструмента одновременно с тем, чтобы обыгрывать прямой пробой.")
        return

    # сортируем по ХУДШЕМУ из двух кварталов: кандидат силён настолько, насколько
    # слаб его слабейший период
    cands.sort(key=lambda c: -c["worst"])
    print(f"\n{'пара':<12}{'худший':>10}{'net M6':>10}{'net U6':>10}{'сд.M6':>7}{'сд.U6':>7}"
          f"{'net/MAE':>9}{'пробой M6':>11}{'пробой U6':>11}  параметры / край сетки")
    for c in cands[:args.top]:
        p = c["p"]
        ps = (f"d={int(p['d_coef']) / 100:.2f} hold={p['hold_min']} n={p['n_days']} "
              f"st={p['step_count']} vol={int(p['vol_mult']) / 10:.1f} "
              f"sl={int(p['sl_price_pct']) / 100:.2f}% tr={int(p['trail_tp_pct']) / 100:.2f}%")
        edge = f"  КРАЙ: {','.join(c['edges'])}" if c["edges"] else "  внутри сетки"
        print(f"{c['pair']:<12}{c['worst']:>10,.0f}{c['net_a']:>10,.0f}{c['net_b']:>10,.0f}"
              f"{c['tr_a']:>7}{c['tr_b']:>7}{c['rmae']:>9.1f}"
              f"{c['brk_a']:>11,.0f}{c['brk_b']:>11,.0f}  {ps}{edge}")

    clean = [c for c in cands if not c["edges"]]
    print(f"\nиз них НЕ на краю сетки: {len(clean)}")
    if not clean:
        print("Все прошедшие сидят на границе хотя бы одной оси — это признак подгонки,\n"
              "а не найденного оптимума. Сетку надо расширять в сторону края.")


if __name__ == "__main__":
    asyncio.run(main())
