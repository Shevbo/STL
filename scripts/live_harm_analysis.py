"""Какие РЕАЛЬНЫЕ сделки тянут робота вниз — и какая формула их отсекает.

ЗАДАЧА ОПЕРАТОРА (20.09.2026): разобрать историю живой торговли, увидеть вредные
сделки, вывести логику фильтра формулой и применить.

МЕТОД. Журнал `algo_trades` склеивается в КРУГИ (от нуля позиции до нуля), у
каждого круга берётся итог в рублях и признаки, известные НА МОМЕНТ ВХОДА:

  hour      — час МСК входа;
  atr       — ATR(25) по барам ДО входа, в пунктах;
  rng60     — размах последних 60 баров (мера боковика, как dv_range_pts);
  trend     — цена минус EMA(200) в пунктах со знаком позиции: вход ПО тренду > 0;
  since     — минут с прошлого выхода (мера «влез сразу обратно»);
  dir       — сторона.

Признаки из БУДУЩЕГО (длительность круга, максимальная просадка, число доборов)
здесь запрещены: по ним фильтр не построить, они известны только после выхода.

ЧЕСТНОСТЬ. Порог подбирается на ПЕРВОЙ половине истории и проверяется на второй.
Обязательный контроль — «оставленные против убранных» в рублях НА ФИЛЛ: без него
«нетто выросло» значит лишь «сделок стало меньше».

    python scripts/live_harm_analysis.py real_fills.csv --bars RIU6.json RIZ6.json \
        --robot lxk22tsffsxiiotb8kmpsato
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from bisect import bisect_right
from datetime import timedelta, timezone

from live_cycles import cycles, read_fills

MSK = timezone(timedelta(hours=3))


def load_bars(paths: list[str]) -> tuple[list[int], list[list]]:
    """Бары агента: метки — московская стенка, проставленная как UTC (см. book_digest)."""
    rows: list[list] = []
    for p in paths:
        rows.extend(json.load(open(p, encoding="utf-8"))["rows"])
    rows.sort(key=lambda r: r[0])
    dedup: list[list] = []
    for r in rows:
        if dedup and dedup[-1][0] == r[0]:
            continue
        dedup.append(r)
    return [r[0] for r in dedup], dedup


def ema_last(vals: list[float], n: int) -> float:
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def atr_pts(bars: list[list], n: int = 25) -> float:
    """Уайлдеровский ATR по закрытым барам (high=2, low=3, close=4)."""
    if len(bars) < n + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, lo, pc = bars[i][2], bars[i][3], bars[i - 1][4]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def features(times: list[int], bars: list[list], c: dict, prev_exit) -> dict | None:
    """Признаки на момент входа. Бар входа НЕ включаем: робот решает по закрытым."""
    ts = int(c["t0"].replace(tzinfo=MSK).timestamp())
    # Метки баров — МСК-стенка как UTC, поэтому ищем по «стенке», а не по epoch.
    wall = int(c["t0"].replace(tzinfo=timezone.utc).timestamp())
    i = bisect_right(times, wall) - 1
    if i < 250:
        return None
    hist = bars[max(0, i - 260):i + 1]
    closes = [b[4] for b in hist]
    price = closes[-1]
    sgn = 1 if c["dir"] == "long" else -1
    e200 = ema_last(closes[-200:], 200) if len(closes) >= 200 else closes[0]
    last60 = hist[-60:]
    return {
        "hour": c["t0"].hour,
        "atr": atr_pts(hist[-120:], 25),
        "rng60": max(b[2] for b in last60) - min(b[3] for b in last60),
        "trend": (price - e200) * sgn,
        "since": ((c["t0"] - prev_exit).total_seconds() / 60.0) if prev_exit else 1e9,
        "dir": c["dir"],
        "net": c["net"],
        "fills": c["fills"],
        "ts": ts,
    }


def bucket_table(rows: list[dict], key: str, edges: list[float]) -> None:
    print(f"\n  {key}:")
    lo = -1e18
    for e in edges + [1e18]:
        sub = [r for r in rows if lo <= r[key] < e]
        if len(sub) >= 15:
            net = sum(r["net"] for r in sub)
            fl = sum(r["fills"] for r in sub)
            print(f"    [{lo if lo > -1e17 else '-inf':>8} .. {e if e < 1e17 else 'inf':>8}) "
                  f"кругов {len(sub):4d}  итог {net:+10.0f}  "
                  f"{net / len(sub):+7.0f} ₽/круг  {net / fl:+7.0f} ₽/филл")
        lo = e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--bars", nargs="+", required=True)
    ap.add_argument("--robot", required=True)
    a = ap.parse_args()

    fills = read_fills(a.csv, a.robot)
    cs = cycles(fills)
    times, bars = load_bars(a.bars)
    rows: list[dict] = []
    kept_cs: list[dict] = []          # круги, у которых признаки посчитались
    prev_exit = None
    for c in cs:
        f = features(times, bars, c, prev_exit)
        prev_exit = c["t1"]
        if f:
            rows.append(f)
            kept_cs.append(c)
    print(f"{a.robot}: кругов {len(cs)}, с признаками {len(rows)}, "
          f"итог {sum(r['net'] for r in rows):+.0f} ₽")

    for key, edges in (("hour", [10, 12, 14, 16, 18, 20, 22]),
                       ("atr", [40, 60, 80, 110]),
                       ("rng60", [200, 350, 500, 800]),
                       ("trend", [-600, -200, 0, 200, 600]),
                       ("since", [2, 10, 30, 120])):
        bucket_table(rows, key, edges)
    for d in ("long", "short"):
        sub = [r for r in rows if r["dir"] == d]
        if sub:
            print(f"\n  {d}: кругов {len(sub)}, итог {sum(r['net'] for r in sub):+.0f} ₽, "
                  f"{sum(r['net'] for r in sub) / sum(r['fills'] for r in sub):+.0f} ₽/филл")

    # Половины истории: порог подбирается на первой, проверяется на второй.
    order = sorted(range(len(rows)), key=lambda i: rows[i]["ts"])
    rows = [rows[i] for i in order]
    kept_cs = [kept_cs[i] for i in order]
    half = len(rows) // 2
    print(f"\nпервая половина: кругов {half}, итог {sum(r['net'] for r in rows[:half]):+.0f} ₽ | "
          f"вторая: {len(rows) - half}, {sum(r['net'] for r in rows[half:]):+.0f} ₽")
    print(f"медиана atr {st.median([r['atr'] for r in rows]):.0f} пт, "
          f"rng60 {st.median([r['rng60'] for r in rows]):.0f} пт, "
          f"trend {st.median([r['trend'] for r in rows]):+.0f} пт")
    gate_grid(rows, times, bars, kept_cs)



def gate_grid(rows: list[dict], times: list[int], bars: list[list],
              cs: list[dict]) -> None:
    """Что отсёк бы ГЕЙТ РЕЖИМА (reg_n/reg_band) на живой истории.

    Правило движка: цена выше SMA(reg_n) + полоса -> шорт запрещён, ниже - полосы ->
    лонг запрещён. Здесь оно применяется к журналу: у каждого круга берётся цена
    входа и SMA на том же баре.

    ОГРАНИЧЕНИЕ, которое нельзя забыть: убранный вход меняет дальнейший путь робота
    (эскалация ставок, потолок позиции), поэтому журнальная оценка - прикидка.
    Приговор выносит парный прогон на i9 с гейтом и без.
    """
    closes = [b[4] for b in bars]
    pref = [0.0]
    for c in closes:
        pref.append(pref[-1] + c)

    def sma(i: int, n: int) -> float:
        return (pref[i + 1] - pref[i + 1 - n]) / n if i + 1 >= n else 0.0

    idx = []
    for c in cs:
        wall = int(c["t0"].replace(tzinfo=timezone.utc).timestamp())
        idx.append(bisect_right(times, wall) - 1)

    half = len(rows) // 2
    t_split = rows[half]["ts"] if rows else 0
    print("\n=== ГЕЙТ РЕЖИМА по журналу (подбор на 1-й половине, проверка на 2-й) ===")
    print(f"{'reg_n':>6} {'полоса':>7} {'убрано':>7} "
          f"{'1-я: оставл.':>13} {'убранные':>10} {'2-я: оставл.':>13} {'убранные':>10}")
    best = None
    for n in (120, 200, 300, 500, 800):
        for band in (0, 10, 20, 40):
            keep1 = drop1 = keep2 = drop2 = 0.0
            kf1 = df1 = kf2 = df2 = 0
            nk = nd = 0
            for r, c, i in zip(rows, cs, idx):
                if i + 1 < n:
                    continue
                ref = sma(i, n)
                px = closes[i]
                b = px * band / 10000.0
                blocked = (px > ref + b and c["dir"] == "short") or \
                          (px < ref - b and c["dir"] == "long")
                first = r["ts"] < t_split
                if blocked:
                    nd += 1
                    if first:
                        drop1 += r["net"]
                        df1 += r["fills"]
                    else:
                        drop2 += r["net"]
                        df2 += r["fills"]
                else:
                    nk += 1
                    if first:
                        keep1 += r["net"]
                        kf1 += r["fills"]
                    else:
                        keep2 += r["net"]
                        kf2 += r["fills"]
            if nk < 50 or nd < 20:
                continue
            row = (n, band, nd, keep1, drop1, keep2, drop2, kf1, df1, kf2, df2)
            print(f"{n:6d} {band:7d} {nd:7d} {keep1:+13.0f} {drop1:+10.0f} "
                  f"{keep2:+13.0f} {drop2:+10.0f}")
            if best is None or keep1 > best[3]:
                best = row
    if best:
        n, band, nd, k1, d1, k2, d2, kf1, df1, kf2, df2 = best
        print(f"\nлучший по 1-й половине: reg_n={n}, полоса={band} "
              f"({band / 100:.2f}% цены), убрано кругов {nd}")
        print(f"  1-я половина: оставленные {k1:+.0f} ₽ ({k1 / max(kf1, 1):+.0f} ₽/филл), "
              f"убранные {d1:+.0f} ₽ ({d1 / max(df1, 1):+.0f} ₽/филл)")
        print(f"  2-я половина: оставленные {k2:+.0f} ₽ ({k2 / max(kf2, 1):+.0f} ₽/филл), "
              f"убранные {d2:+.0f} ₽ ({d2 / max(df2, 1):+.0f} ₽/филл)")
        print("  КОНТРОЛЬ: фильтр годен, только если ₽/филл оставленных ВЫШЕ, чем у "
              "убранных, на ОБЕИХ половинах — иначе он просто сократил торговлю.")

if __name__ == "__main__":
    main()
