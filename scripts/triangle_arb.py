"""Арбитражный треугольник: MX против синтетики RI × Si (17.09.2026).

СВЯЗЬ. Индекс РТС это индекс Мосбиржи в долларах: IMOEX = RTSI × USDRUB × const.
Фьючерсы: MX ~ IMOEX, RI ~ RTSI, Si ~ USDRUB. Поэтому остаток
    e = ln MX − ln RI − ln Si
держится около медленно плывущей константы (разница базисов: у RI долларовая ставка,
у Si ставочный дифференциал, у MX рублёвая ставка и дивиденды). Отклонение e от
скользящего среднего — кандидат на возврат. Это не статистическая пара, как RI против Si
(там хедж снимал лишь 15% риска), а тождество по построению индекса.

НОГИ. Нейтральность по номиналу N в рублях: короткий MX на N, длинный RI на N и
длинный Si на N (прибыль RI в рублях даёт d ln RI, Si добавляет d ln USDRUB, вместе это
d ln IMOEX). Лоты дробные, чтобы мерить сам арбитраж; ошибку округления до целых лотов
считать отдельно. Цена пункта RI = 0.02 × USDRUB = 0.02 × Si / 1000 рублей.

РЕЖИМЫ СИГНАЛА:
  zrev — классика арбитража: |z| > entry → ставка на возврат, выход при z через 0,
         по стопу/тейку или по времени;
  ema  — правило оператора: две EMA на отклонении e, пересечение быстрой снизу →
         покупаем спред (длинный MX, короткие RI и Si), сверху → продаём.
Стоп = k_sl × SD(e), тейк = 2 × стоп (соотношение 2:1 из задания).

ИСПОЛНЕНИЕ. Сигнал по close бара t, все три ноги по open бара t+1. Издержки на ногу и
сторону: полспреда (измерено: RI 5 пт, Si 3 пт, MX 25 пт) + сбор тейкера MOEX от номинала.
Торговля только в основную сессию (10:00-18:45 МСК): ночь и предоткрытие по замеру
архива стоят 75-180 пт на RI. Позиция закрывается на смене контракта любой ноги: через
шов склейки остаток прыгает на разницу базисов.

ГЛАВНАЯ ЛОВУШКА. На минутках close трёх ног несинхронен: последняя сделка по MX могла
быть на 40 секунд раньше, чем по RI. Это рисует ЛОЖНЫЕ отклонения, которые «возвращаются»,
и бэктест показывает фантомную прибыль. Частичная страховка — исполнение по open
следующего бара; честная проверка — только синхронные котировки (архив, когда в нём
появится MX). Любой плюс на минутках считать верхней границей.

    python scripts/triangle_arb.py --mx MX.txt --ri RI.txt --si Si.txt --out tri.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timezone
from itertools import product

HALF = {"MX": 25.0, "RI": 5.0, "Si": 3.0}                 # пункты цены, измерено 16-17.09
FEE = {"MX": 0.0000660, "RI": 0.0000660, "Si": 0.0000462}  # доля номинала, тейкер MOEX
SESSION = (10 * 60, 18 * 60 + 45)                          # минуты МСК


def load(path: str) -> dict[int, tuple]:
    """time -> (open, close, secname); время бара = МСК-стенка как UTC."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            t = ln.rstrip().split(",")
            if t[4].startswith("<"):
                continue
            ts = int(datetime.strptime(t[2] + t[3], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
            out[ts] = (float(t[4]), float(t[7]), t[9])
    return out


def align(mx, ri, si):
    """Общие минуты, где у всех трёх один и тот же квартальный цикл (MXZ6/RIZ6/SiZ6)."""
    rows = []
    for ts in sorted(set(mx) & set(ri) & set(si)):
        a, b, c = mx[ts], ri[ts], si[ts]
        cyc = a[2][-2:]
        if b[2][-2:] != cyc or c[2][-2:] != cyc:
            continue
        rows.append((ts, a[0], a[1], b[0], b[1], c[0], c[1], cyc))
    return rows


def ema_step(prev, x, n):
    a = 2.0 / (n + 1)
    return x if prev is None else prev + a * (x - prev)


def close_trade(en, mxo, rio, sio, pos, cost_mult):
    usd_x = sio / 1000.0
    gross = (pos * en["n_mx"] * (mxo - en["mx"])
             - pos * en["n_ri"] * (rio - en["ri"]) * 0.02 * (en["usd"] + usd_x) / 2
             - pos * en["n_si"] * (sio - en["si"]))
    cost = 0.0
    for leg, n, p0, p1, pv in (("MX", en["n_mx"], en["mx"], mxo, 1.0),
                               ("RI", en["n_ri"], en["ri"], rio, 0.02 * en["usd"]),
                               ("Si", en["n_si"], en["si"], sio, 1.0)):
        for px in (p0, p1):
            cost += n * (HALF[leg] * pv + FEE[leg] * px * pv)
    year = datetime.fromtimestamp(en["ts"], timezone.utc).year
    return en["ts"], year, gross - cost * cost_mult, gross


def prepare(rows):
    """Общие для всех комбинаций ряды: остаток, минута МСК, шов."""
    e = [math.log(r[2]) - math.log(r[4]) - math.log(r[6]) for r in rows]
    mins = [(r[0] % 86400) // 60 for r in rows]
    seam = [i + 1 >= len(rows) or rows[i + 1][7] != rows[i][7] or rows[i + 1][0] - rows[i][0] > 3600
            for i in range(len(rows))]
    return e, mins, seam


def rolling(e, W):
    """Скользящие среднее и SD по окну W через накопленные суммы: O(n), а не O(n·W)."""
    c1, c2 = [0.0], [0.0]
    for x in e:
        c1.append(c1[-1] + x)
        c2.append(c2[-1] + x * x)
    m, sd = [None] * len(e), [None] * len(e)
    for i in range(W - 1, len(e)):
        s1 = c1[i + 1] - c1[i + 1 - W]
        s2 = c2[i + 1] - c2[i + 1 - W]
        mu = s1 / W
        m[i] = mu
        sd[i] = max(s2 / W - mu * mu, 0.0) ** 0.5 or 1e-9
    return m, sd


def backtest(rows, p, cost_mult=1.0, rand_dir=None, prep=None, roll=None):
    """Сделки: (время входа, год, P&L ₽ нетто, P&L ₽ валовый) на 1 лот MX и хедж."""
    mode = p["mode"]
    e, mins, seam = prep or prepare(rows)
    m, sdv = roll or rolling(e, p["W"])
    trades = []
    pos = 0                           # +1 длинный спред (long MX), −1 короткий
    entry = None
    f_ema = s_ema = None
    prev_diff = None
    for i in range(len(rows) - 1):
        if m[i] is None:
            continue
        in_session = SESSION[0] <= mins[i] < SESSION[1]
        sd = sdv[i]
        dev = e[i] - m[i]
        z = dev / sd
        want = pos
        if pos != 0:
            move = (dev - entry["dev"]) * pos
            stop, take = p["k_sl"] * entry["sd"], 2 * p["k_sl"] * entry["sd"]
            if (move <= -stop or move >= take or seam[i] or not in_session
                    or i - entry["i"] >= p["max_bars"]
                    or (mode == "zrev" and z * pos >= 0)):
                want = 0
        if mode == "ema":
            f_ema = ema_step(f_ema, dev, p["fast"])
            s_ema = ema_step(s_ema, dev, p["slow"])
            diff = f_ema - s_ema
            if pos == 0 and want == 0 and in_session and not seam[i] and prev_diff is not None:
                if prev_diff <= 0 < diff:
                    want = 1
                elif prev_diff >= 0 > diff:
                    want = -1
            prev_diff = diff
        elif pos == 0 and want == 0 and in_session and not seam[i]:
            if z >= p["entry"]:
                want = -1                                  # MX дорог к синтетике: продаём спред
            elif z <= -p["entry"]:
                want = 1

        if want != pos:
            _, nmxo, _, nrio, _, nsio, _, _ = rows[i + 1]
            nts = rows[i + 1][0]
            if pos != 0:
                trades.append(close_trade(entry, nmxo, nrio, nsio, pos, cost_mult))
                pos = 0
            if want != 0:
                usd = nsio / 1000.0
                N = nmxo                                   # номинал 1 лота MX, ₽ (пункт MX = 1 ₽)
                entry = {"i": i, "dev": dev, "sd": sd, "ts": nts, "mx": nmxo, "ri": nrio, "si": nsio,
                         "n_mx": 1.0, "n_ri": N / (nrio * 0.02 * usd), "n_si": N / nsio, "usd": usd}
                pos = want if rand_dir is None else rand_dir.choice((1, -1))
    return trades


def summarize(trades, split_year=2025):
    tr = [t[2] for t in trades if t[1] < split_year]
    te = [t[2] for t in trades if t[1] >= split_year]
    return {"n": len(trades), "net": sum(t[2] for t in trades), "gross": sum(t[3] for t in trades),
            "n_train": len(tr), "net_train": sum(tr), "n_test": len(te), "net_test": sum(te)}


def grid():
    for W, entry, k_sl, mb in product((60, 240, 960), (1.5, 2.0, 2.5, 3.0), (1.0, 2.0, 3.0), (60, 240)):
        yield {"mode": "zrev", "W": W, "entry": entry, "k_sl": k_sl, "max_bars": mb}
    for W, fast, slow, k_sl, mb in product((240, 960), (5, 15, 30), (60, 240), (1.0, 2.0, 3.0), (60, 240)):
        if fast < slow:
            yield {"mode": "ema", "W": W, "fast": fast, "slow": slow, "k_sl": k_sl, "max_bars": mb}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mx", required=True)
    ap.add_argument("--ri", required=True)
    ap.add_argument("--si", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rows = align(load(a.mx), load(a.ri), load(a.si))
    print(f"общих минут одного цикла: {len(rows)}; "
          f"{datetime.fromtimestamp(rows[0][0], timezone.utc):%Y-%m-%d}.."
          f"{datetime.fromtimestamp(rows[-1][0], timezone.utc):%Y-%m-%d}", flush=True)
    rng = random.Random(20260917)
    prep = prepare(rows)
    rolls = {}
    with open(a.out, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["params", "n", "net", "gross", "n_train", "net_train", "n_test", "net_test",
                    "net_test_2x_cost", "net_placebo"])
        for k, p in enumerate(grid(), 1):
            if p["W"] not in rolls:
                rolls[p["W"]] = rolling(prep[0], p["W"])
            kw = {"prep": prep, "roll": rolls[p["W"]]}
            s = summarize(backtest(rows, p, **kw))
            s2 = summarize(backtest(rows, p, cost_mult=2.0, **kw))
            pl = summarize(backtest(rows, p, rand_dir=rng, **kw))
            w.writerow([p, s["n"], round(s["net"]), round(s["gross"]), s["n_train"], round(s["net_train"]),
                        s["n_test"], round(s["net_test"]), round(s2["net_test"]), round(pl["net"])])
            fo.flush()
            if k % 10 == 0:
                print(f"{k} комбинаций", flush=True)


if __name__ == "__main__":
    main()
