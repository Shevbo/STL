"""Калибровка d_coef / step_count / F / hold_min по РЕАЛЬНОЙ достижимости ступеней.

ИСПРАВЛЕНИЕ ОШИБКИ ПЕРВОЙ ВЕРСИИ. Она обещала 65-97% дней со сделками, а живой
прогон дал 10% на RIM6 и 5% на RIU6. Причина: ход цены считался за ВЕСЬ ДЕНЬ, тогда
как первая ступень обязана сработать внутри ОКНА НАБОРА (hold_min минут от первого
бара дня). 71% был недостижимой верхней границей. Оператор усомнился в этих
процентах раньше, чем я нашёл ошибку.

Теперь два разных хода меряются раздельно, как и работает стратегия:
  ВХОД     — ход внутри окна набора: только он может зацепить ПЕРВУЮ ступень;
  НАБОР    — ход за весь день: лестница, уже начавшая набирать, работает до закрытия,
             поэтому недостижимость ПОСЛЕДНЕЙ ступени проверяется по дню целиком.

Смещение ступени: off(k) = D · Σ_{j=1..k} 1/(j+1+F),  D = amp · d_coef
Условие «последняя недостижима ни в одном из n_days»:
    d_coef > max(ход за день по окну n_days) / (amp · S(m, F))

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_calibrate.py [--guard 50]
"""
from __future__ import annotations

import argparse
import datetime
import json

WINDOWS = {
    "RIM6": ("2026-03-20", "2026-06-17"),
    "RIU6": ("2026-06-19", "2026-09-09"),
    "SiM6": ("2026-03-20", "2026-06-17"),
    "SiU6": ("2026-06-19", "2026-09-09"),
}
# РАБОЧАЯ ОБЛАСТЬ по расчёту: F от 3 до 10, много ступеней. F=0 оставлен одной
# строкой как контроль — именно он давал 4-9 сделок за квартал.
GRID = [            # (n_days, step_count, F)
    (15, 20, 0.0),
    (15, 20, 3.0), (15, 20, 5.0), (15, 20, 7.0), (15, 20, 10.0),
    (15, 12, 3.0), (15, 12, 5.0), (15, 12, 7.0), (15, 12, 10.0),
    (5, 20, 3.0), (5, 20, 5.0), (5, 20, 7.0), (5, 20, 10.0),
    (5, 12, 5.0), (5, 12, 10.0),
]
HOLDS = [30, 60, 90, 120, 150]


def s_of(m: int, f: float) -> float:
    return sum(1.0 / (j + 1 + f) for j in range(1, m + 1))


def days_of(secid: str):
    """По каждому дню: размах, ход за день и ход внутри окна от первого бара."""
    a, b = WINDOWS[secid]
    rows = json.load(open(f"agent_bars/{secid}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(a).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(b).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    per: dict[datetime.date, list] = {}
    for ts, _o, h, low, c, _v in rows:
        if lo <= ts <= hi:
            d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            per.setdefault(d.date(), []).append((d.hour * 60 + d.minute, h, low, c))
    out = []
    for d in sorted(per):
        bars = sorted(per[d])
        out.append({"date": d, "bars": bars,
                    "rng": max(x[1] for x in bars) - min(x[2] for x in bars),
                    "close": bars[-1][3], "first": bars[0][0]})
    for i, r in enumerate(out):
        r["exc_day"] = r["exc_win"] = None
        if i:
            pc = out[i - 1]["close"]
            r["pc"] = pc
            r["exc_day"] = max(max(x[1] for x in r["bars"]) - pc,
                               pc - min(x[2] for x in r["bars"]))
    return out


def exc_in_window(r: dict, hold: int) -> float:
    """Ход от вчерашнего закрытия ВНУТРИ окна набора."""
    end = r["first"] + hold
    sel = [x for x in r["bars"] if x[0] <= end]
    if not sel or "pc" not in r:
        return 0.0
    pc = r["pc"]
    return max(max(x[1] for x in sel) - pc, pc - min(x[2] for x in sel))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--guard", type=float, default=50.0)
    args = ap.parse_args()

    for secid in WINDOWS:
        tbl = days_of(secid)
        print(f"\n######## {secid} ({len(tbl)} дней) ########")
        print(f"{'n_d':>4}{'шаг':>4}{'F':>5}{'d_coef':>7}{'1-я ст':>8}"
              f"   {'30м':^10}{'60м':^10}{'90м':^10}{'120м':^10}{'150м':^10}{'конец':>7}")
        for n, m, f in GRID:
            S = s_of(m, f)
            need = []
            for i in range(n, len(tbl)):
                amp = sum(tbl[j]["rng"] for j in range(i - n, i)) / n
                win = [tbl[j]["exc_day"] for j in range(i - n, i) if tbl[j]["exc_day"]]
                if amp > 0 and win:
                    need.append(max(win) / (amp * S))
            if not need:
                continue
            d = max(need)

            cells, firsts, breach, tot = [], [], 0, 0
            for hold in HOLDS:
                hits = steps = cnt = 0
                for i in range(n, len(tbl)):
                    amp = sum(tbl[j]["rng"] for j in range(i - n, i)) / n
                    if amp <= 0 or tbl[i]["exc_day"] is None:
                        continue
                    cnt += 1
                    D = amp * d
                    offs, acc = [], 0.0
                    for k in range(1, m + 1):
                        acc += D / (k + 1 + f)
                        offs.append(acc)
                    if hold == HOLDS[0]:
                        firsts.append(offs[0])
                        if tbl[i]["exc_day"] >= offs[-1]:
                            breach += 1
                    ew = exc_in_window(tbl[i], hold)
                    if ew >= offs[0] + args.guard:
                        hits += 1
                        # набор после входа идёт по ходу за ВЕСЬ день
                        steps += sum(1 for o in offs if tbl[i]["exc_day"] >= o + args.guard)
                tot = cnt
                cells.append(f"{100 * hits / cnt if cnt else 0:>3.0f}%/{steps / hits if hits else 0:>4.1f}")
            print(f"{n:>4}{m:>4}{f:>5.1f}{d:>7.2f}{sum(firsts) / len(firsts):>8,.0f}"
                  f"   {'  '.join(cells)}{100 * breach / tot if tot else 0:>6.0f}%")


if __name__ == "__main__":
    main()
