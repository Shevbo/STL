"""Какие оси РЕАЛЬНО двигают результат — по случайной выборке перебора.

ЗАЧЕМ. Оператор предложил перебирать умнее: сперва базовые критические параметры,
потом фиксировать их и идти к следующей группе, потом экзотика. Группы надо брать
не на глаз, а из данных: случайная выборка по всем осям уже посчитана, и по ней
видно, какая ось меняет итог, а какая не меняет ничего.

МЕРА ВЛИЯНИЯ — доля объяснённого разброса (eta²): насколько разъезжаются средние
итоги по значениям оси относительно общего разброса. Для осей с порядком (периоды,
пороги) добавляется ранговая корреляция Спирмена: она говорит НЕ только «ось
важна», но и «в какую сторону крутить».

ОГРАНИЧЕНИЕ, которое нельзя забыть: выборка случайная и одна, а взаимодействия
осей она видит только косвенно. Ось, важная лишь в паре с другой, здесь окажется
слабой. Поэтому это инструмент ПЛАНИРОВАНИЯ перебора, а не вывод о стратегии.

    PYTHONPATH=. python scripts/axis_importance.py --base all0921 [--min-rows 2000]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics as st
from collections import defaultdict

import asyncpg


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True,
                    help="ПРЕФИКС кампании (camp-YYYYMMDD-tag): подстрока с ведущим
                          %% заставляла Postgres сканировать 4.8 млн строк лидерборда
                          целиком и грузила хостер, 22.09.2026 LA дошла до 31")
    ap.add_argument("--min-rows", type=int, default=1000)
    ap.add_argument("--top", type=int, default=99)
    # Случайная сетка по 47 осям в большинстве наборов ГЛУШИТ торговлю (долина,
    # остывание, разножка, расписание — любой из фильтров закрывает все входы), и
    # такие наборы дают ровно 0. Мешать их с торгующими нельзя: влияние осей
    # размывается в ноль. По умолчанию считаем только по торгующим.
    ap.add_argument("--all-sets", action="store_true",
                    help="считать и по неторгующим наборам (по умолчанию нет)")
    a = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    total = await c.fetchval(
        "SELECT count(*) FROM optimization_leaderboard WHERE campaign_run LIKE $1",
        f"{a.base}%")
    cond = "" if a.all_sets else " AND total_trades > 0"
    rows = await c.fetch(
        "SELECT params, net_profit, total_trades FROM optimization_leaderboard "
        f"WHERE campaign_run LIKE $1 AND net_profit IS NOT NULL{cond}", f"{a.base}%")
    await c.close()
    print(f"всего наборов {total}, торгующих {len(rows)} "
          f"({100 * len(rows) / max(total, 1):.1f}%)")
    if len(rows) < a.min_rows:
        raise SystemExit(f"строк мало: {len(rows)} < {a.min_rows}")

    data = []
    for r in rows:
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else p
        data.append((p, float(r["net_profit"])))
    nets = [n for _p, n in data]
    gmean, gvar = st.mean(nets), st.pvariance(nets)
    print(f"наборов {len(data)}, средний итог {gmean:+.0f} руб, "
          f"медиана {st.median(nets):+.0f}, разброс SD {gvar ** 0.5:.0f}")

    keys = sorted({k for p, _n in data for k in p
                   if k not in ("symbol", "book_key") and isinstance(p[k], (int, float))})
    out = []
    for k in keys:
        groups: dict[float, list[float]] = defaultdict(list)
        xs, ys = [], []
        for p, n in data:
            if k in p:
                groups[float(p[k])].append(n)
                xs.append(float(p[k]))
                ys.append(n)
        if len(groups) < 2:
            continue
        # eta²: доля разброса, объяснённая различием средних по значениям оси
        between = sum(len(v) * (st.mean(v) - gmean) ** 2 for v in groups.values())
        eta = between / (gvar * len(data)) if gvar else 0.0
        rho = spearman(xs, ys) if len(groups) > 2 else 0.0
        best = max(groups.items(), key=lambda kv: st.median(kv[1]))
        worst = min(groups.items(), key=lambda kv: st.median(kv[1]))
        out.append((eta, k, rho, len(groups), best[0], st.median(best[1]),
                    worst[0], st.median(worst[1])))

    out.sort(reverse=True)
    print(f"\n{'ось':22} {'влияние eta2':>12} {'Спирмен':>8} {'знач':>5} "
          f"{'лучшее':>10} {'медиана':>10} {'худшее':>10} {'медиана':>10}")
    for eta, k, rho, nv, b, bm, w, wm in out[:a.top]:
        print(f"{k:22} {100 * eta:11.2f}% {rho:+8.2f} {nv:5d} {b:10.0f} {bm:+10.0f} "
              f"{w:10.0f} {wm:+10.0f}")
    print("\nГруппы для ступенчатого перебора (по влиянию):")
    for name, lo, hi in (("критические", 0, 5), ("средние", 5, 12), ("экзотика", 12, 99)):
        part = [k for _e, k, *_r in out[lo:hi]]
        if part:
            print(f"  {name}: {', '.join(part)}")


if __name__ == "__main__":
    asyncio.run(main())
