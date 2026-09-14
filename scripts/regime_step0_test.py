"""Шаг 0, проверка «убийца»: предсказуем ли месячный результат стратегий по прошлому.

Вход: CSV из regime_step0.py (дневной валовый P&L в пунктах) и RI.txt (цена дня и
признаки режима).

v1 (34f6cb5, валовый): A/B/C прошли, но критерий был пустой — все три проходят и
тогда, когда стратегии просто различаются средним, а месяцы независимы. C выбирал
stochastic почти в каждой терцили = статичный выбор. Валовый плюс осцилляторов
~0.7 пт/филл при тике 10 = bid-ask bounce, не эдж (проверка fable 14.09.2026).

КРИТЕРИЙ v2 (записан ДО прогона, 14.09.2026). Всё на НЕТТО: филл стоит
0.0066% x цена (биржевой сбор тейкера) + 5 пт (полспреда RI). williams_r исключён
(тождественен stochastic). Проект идёт дальше, если выполнено ХОТЯ БЫ ОДНО:
  D. «вчерашний победитель» (1 или 3 мес) обгоняет ЛУЧШУЮ ЗА ВСЁ ПРОШЛОЕ (растущее
     окно, с 13-го месяца), t > 2;
  E. выбор по терцилям режима (пороги и выбор на 2022-2024) обгоняет на 2025-2026
     ЛУЧШУЮ ОДИНОЧНУЮ за 2022-2024, t > 2.
A/B/C печатаются для справки вместе с долей плацебо (месяцы перемешаны внутри
каждой стратегии, 200 раз), где они тоже проходят t > 2. Ни D, ни E -> проект закрыт.

    python scripts/regime_step0_test.py step0.csv C:/Users/Boris/Downloads/RI.txt [--gross]
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from statistics import mean, stdev

FEE_RATE = 0.000066     # trader/lab/commission.py, группа index, тейкер
HALF_SPREAD = 5.0       # пунктов RI, полтика
SKIP = {"williams_r"}


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


def load_pnl(path, days=None):
    """month -> strat -> пункты; days задан = нетто за вычетом издержек филлов."""
    m = defaultdict(lambda: defaultdict(float))
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            s, _c, d, p, n = ln.rstrip().split(",")
            cost = int(n) * (FEE_RATE * days[d][3] + HALF_SPREAD) if days else 0.0
            m[d[:7]][s] += float(p) - cost
    return m


def features(days, month):
    """Признаки на конец месяца ПЕРЕД `month`, только по прошлому."""
    ds = [d for d in sorted(days) if d < month + "-01"]
    if len(ds) < 61:
        return None
    last20 = [days[d] for d in ds[-20:]]
    rng = mean((h - lo) / c for _, h, lo, c in last20)
    move = abs(last20[-1][3] / days[ds[-21]][3] - 1)
    rets = [days[ds[i]][3] / days[ds[i - 1]][3] - 1 for i in range(len(ds) - 60, len(ds))]
    return {"vol20": rng, "trend20": move / rng / 20, "ac60": spearman(rets[:-1], rets[1:])}


def winner(mat, months, i, lb):
    past = [sum(mat[months[j]][k] for j in range(i - lb, i)) for k in range(len(mat[months[0]]))]
    return max(range(len(past)), key=lambda k: past[k])


def ab_t(mat, months):
    """t для A и B3 (против равных долей) — ради плацебо."""
    ic = [spearman(mat[a], mat[b]) for a, b in zip(months, months[1:])]
    b = [mat[months[i]][winner(mat, months, i, 3)] - mean(mat[months[i]]) for i in range(3, len(months))]
    return tstat(ic), tstat(b)


def main():
    gross = "--gross" in sys.argv
    days = load_daily_ri(sys.argv[2])
    pnl = load_pnl(sys.argv[1], None if gross else days)
    months = sorted(pnl)
    strats = sorted({s for m in months for s in pnl[m]} - SKIP)
    K = range(len(strats))
    mat = {m: [pnl[m].get(s, 0.0) for s in strats] for m in months}
    print(f"{'ВАЛОВЫЙ' if gross else 'НЕТТО'}: стратегий {len(strats)}, месяцев {len(months)}")
    tot = sorted(((sum(mat[m][k] for m in months), strats[k]) for k in K), reverse=True)
    print("итог, пт: " + ", ".join(f"{s} {v:+.0f}" for v, s in tot))

    tA, tB = ab_t(mat, months)
    rng = random.Random(1)
    cols = {k: [mat[m][k] for m in months] for k in K}
    plac = []
    for _ in range(200):
        for k in K:
            rng.shuffle(cols[k])
        pm = {m: [cols[k][i] for k in K] for i, m in enumerate(months)}
        plac.append(ab_t(pm, months))
    print(f"справка A t={tA:.2f} (плацебо t>2: {sum(a > 2 for a, _ in plac) / 2:.0f}%), "
          f"B3 vs равные доли t={tB:.2f} (плацебо {sum(b > 2 for _, b in plac) / 2:.0f}%)")

    ok = False
    for lb in (1, 3):
        d = []
        for i in range(12, len(months)):
            best = max(K, key=lambda k: sum(mat[months[j]][k] for j in range(i)))
            d.append(mat[months[i]][winner(mat, months, i, lb)] - mat[months[i]][best])
        ok |= tstat(d) > 2
        print(f"D{lb}. победитель - лучшая за прошлое: {mean(d):+.0f} пт/мес, t={tstat(d):.2f}, n={len(d)}")

    feats = {m: features(days, m) for m in months}
    train = [m for m in months if feats[m] and m[:4] <= "2024"]
    test = [m for m in months if feats[m] and m[:4] >= "2025"]
    single = max(K, key=lambda k: sum(mat[m][k] for m in train))
    for key in ("vol20", "trend20", "ac60"):
        vals = sorted(feats[m][key] for m in train)
        q1, q2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]

        def lvl(m, key=key, q1=q1, q2=q2):
            return 0 if feats[m][key] < q1 else (1 if feats[m][key] < q2 else 2)
        best = {L: max(K, key=lambda k, L=L: sum(mat[m][k] for m in train if lvl(m) == L)) for L in range(3)}
        d = [mat[m][best[lvl(m)]] - mat[m][single] for m in test]
        ok |= tstat(d) > 2
        picks = ", ".join(strats[best[L]] for L in range(3))
        print(f"E {key}: против {strats[single]} {sum(d):+.0f} пт, t={tstat(d):.2f}, n={len(d)} | {picks}")
    print("КРИТЕРИЙ v2:", "ПРОЙДЕН" if ok else "НЕ ПРОЙДЕН -> проект закрыт")


if __name__ == "__main__":
    main()
