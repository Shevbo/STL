"""Шаг 0, проверка «убийца»: предсказуем ли месячный результат стратегий по прошлому.

Вход: CSV из regime_step0.py (дневной валовый P&L в пунктах) и RI.txt (для признаков режима).

КРИТЕРИЙ (записан ДО просмотра результатов, 14.09.2026). Проект идёт дальше, если
выполнено ХОТЯ БЫ ОДНО, иначе закрывается без новых осей:
  A. Персистентность рангов: средний Спирмен рангов стратегий месяц m -> m+1 > 0.10,
     t > 2 по месяцам.
  B. «Вчерашний победитель» (лучшая по прошлым 1 или 3 месяцам) обгоняет равные доли
     в следующем месяце: t > 2 по всей выборке И разница > 0 отдельно на 2022-2024 и
     на 2025-2026.
  C. Режимы: терцили признака (размах 20д, |ход 20д|/размах, автокорр. дневных
     доходностей 60д) с порогами по 2022-2024; стратегия, лучшая в каждой терцили на
     2022-2024, на 2025-2026 обгоняет равные доли (сумма разниц > 0, t > 2).
Проверок немного (2 окна B, 3 признака C), но t > 2 при ~6 попытках — слабый порог;
прошедшее идёт в Шаг 1, а не в деньги.

    python scripts/regime_step0_test.py step0.csv C:/Users/Boris/Downloads/RI.txt
"""
from __future__ import annotations

import sys
from collections import defaultdict
from statistics import mean, stdev


def tstat(xs):
    return mean(xs) / (stdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and stdev(xs) > 0 else 0.0


def ranks(v):
    o = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for k, i in enumerate(o):
        r[i] = k
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def load_pnl(path):
    m = defaultdict(lambda: defaultdict(float))       # month -> strat -> pts
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            s, _c, d, p, _n = ln.rstrip().split(",")
            m[d[:7]][s] += float(p)
    return m


def load_daily_ri(path):
    """дата -> (open, high, low, close) по склейке."""
    days = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            t = ln.split(",")
            d = f"{t[2][:4]}-{t[2][4:6]}-{t[2][6:8]}"
            o, h, lo, c = map(float, t[4:8])
            if d in days:
                a = days[d]
                days[d] = (a[0], max(a[1], h), min(a[2], lo), c)
            else:
                days[d] = (o, h, lo, c)
    return days


def features(days, month):
    """Признаки на конец месяца ПЕРЕД `month`, только по прошлому."""
    ds = [d for d in sorted(days) if d < month + "-01"]
    if len(ds) < 61:
        return None
    last20 = [days[d] for d in ds[-20:]]
    rng = mean((h - lo) / c for _, h, lo, c in last20)
    move = abs(last20[-1][3] / days[ds[-21]][3] - 1)
    rets = [days[ds[i]][3] / days[ds[i - 1]][3] - 1 for i in range(len(ds) - 60, len(ds))]
    ac = spearman(rets[:-1], rets[1:])
    return {"vol20": rng, "trend20": move / rng / 20, "ac60": ac}


def main():
    pnl = load_pnl(sys.argv[1])
    days = load_daily_ri(sys.argv[2])
    months = sorted(pnl)
    strats = sorted({s for m in months for s in pnl[m]})
    mat = {m: [pnl[m].get(s, 0.0) for s in strats] for m in months}
    print(f"стратегий {len(strats)}, месяцев {len(months)} ({months[0]}..{months[-1]})")

    # A
    ic = [spearman(mat[a], mat[b]) for a, b in zip(months, months[1:])]
    print(f"A. Спирмен m->m+1: среднее {mean(ic):+.3f}, t={tstat(ic):.2f}, n={len(ic)}")

    # B
    for lb in (1, 3):
        diff, yrs = [], []
        for i in range(lb, len(months)):
            past = [sum(mat[months[j]][k] for j in range(i - lb, i)) for k in range(len(strats))]
            win = max(range(len(strats)), key=lambda k: past[k])
            nxt = mat[months[i]]
            diff.append(nxt[win] - mean(nxt))
            yrs.append(months[i][:4])
        d1 = [x for x, y in zip(diff, yrs) if y <= "2024"]
        d2 = [x for x, y in zip(diff, yrs) if y >= "2025"]
        print(f"B{lb}. победитель - равные доли: среднее {mean(diff):+.0f} пт/мес, t={tstat(diff):.2f}; "
              f"2022-24 {sum(d1):+.0f}, 2025-26 {sum(d2):+.0f}")

    # C
    feats = {m: features(days, m) for m in months}
    for key in ("vol20", "trend20", "ac60"):
        train = [m for m in months if feats[m] and m[:4] <= "2024"]
        test = [m for m in months if feats[m] and m[:4] >= "2025"]
        vals = sorted(feats[m][key] for m in train)
        q1, q2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        def lvl(m, key=key, q1=q1, q2=q2):
            return 0 if feats[m][key] < q1 else (1 if feats[m][key] < q2 else 2)
        best = {}
        for L in range(3):
            ms = [m for m in train if lvl(m) == L]
            tot = [sum(mat[m][k] for m in ms) for k in range(len(strats))]
            best[L] = max(range(len(strats)), key=lambda k: tot[k]) if ms else None
        diff = [mat[m][best[lvl(m)]] - mean(mat[m]) for m in test if best[lvl(m)] is not None]
        picks = ", ".join(f"{L}:{strats[b]}" for L, b in best.items() if b is not None)
        print(f"C {key}: вне выборки {sum(diff):+.0f} пт, t={tstat(diff):.2f}, n={len(diff)} | {picks}")


if __name__ == "__main__":
    main()
