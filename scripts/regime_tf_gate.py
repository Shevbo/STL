"""Реестр на M15/H1: есть ли стратегии с положительным нетто (15.09.2026).

Шаг 0 переключателя закрыт: на M1 нетто в минусе у всех, издержки > валового.
На старших ТФ сделок в 15-60 раз меньше. Вход: CSV regime_step0.py --tf N
(fills = контракты).

КРИТЕРИЙ (записан ДО прогона). Строка стратегия×ТФ — кандидат, если на НЕТТО
(контракт стоит 0.0066% x цена + 5 пт полспреда):
  1. нетто > 0 отдельно на 2022-2024 И на 2025-2026;
  2. нетто > 0 минимум в 3 из 5 календарных лет;
  3. не меньше 200 контрактов оборота.
30 проверок (15 стратегий x 2 ТФ): кандидат = повод для гейта на втором инструменте,
не деньги. Меньше двух кандидатов -> переключатель остаётся закрытым.

    python scripts/regime_tf_gate.py C:/Users/Boris/Downloads/RI.txt m15.csv h1.csv
"""
from __future__ import annotations

import sys
from collections import defaultdict

from regime_step0_test import FEE_RATE, HALF_SPREAD, SKIP, load_daily_ri


def main():
    days = load_daily_ri(sys.argv[1])
    cands = 0
    for path in sys.argv[2:]:
        net = defaultdict(lambda: defaultdict(float))
        fills = defaultdict(int)
        with open(path, encoding="utf-8") as f:
            next(f)
            for ln in f:
                s, _c, d, p, n = ln.rstrip().split(",")
                if s in SKIP:
                    continue
                net[s][d[:4]] += float(p) - int(n) * (FEE_RATE * days[d][3] + HALF_SPREAD)
                fills[s] += int(n)
        print(f"== {path}")
        for s in sorted(net, key=lambda s: -sum(net[s].values())):
            y = net[s]
            a = sum(v for k, v in y.items() if k <= "2024")
            b = sum(v for k, v in y.items() if k >= "2025")
            pos = sum(v > 0 for v in y.values())
            ok = a > 0 and b > 0 and pos >= 3 and fills[s] >= 200
            cands += ok
            yrs = " ".join(f"{k[2:]}:{v / 1000:+.0f}k" for k, v in sorted(y.items()))
            print(f"{'КАНДИДАТ ' if ok else '         '}{s:15} 22-24 {a:+9.0f} 25-26 {b:+9.0f} "
                  f"лет+ {pos}/{len(y)} контр {fills[s]:6} | {yrs}")
    print(f"кандидатов {cands}: переключатель {'можно открыть' if cands >= 2 else 'остаётся закрытым'}")


if __name__ == "__main__":
    main()
