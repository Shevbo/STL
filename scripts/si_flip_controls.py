"""Si без флипов: три контроля перед тем, как называть это кандидатом.

ЧТО ПРОВЕРЯЕМ. На Si 2022-2026 голая 2EMA/3EMA с заблокированным переворотом
(выход только по тейку 8xATR или стопу 1%) дала +92652 / +84021 пт при дефолтных
параметрах, плюс в каждом из пяти лет. Тупой лонг на тех же контрактах -23125 пт,
разбивка по сторонам: лонг +38867, шорт +80869. Прежде чем вести дальше, нужно
закрыть три дыры, названные проверкой (fable, 18.09.2026).

КРИТЕРИИ ЗАПИСАНЫ ДО ПРОГОНА:

A. ПЛАЦЕБО. Случайные трендовые правила реестра со случайными параметрами, тот же
   голый конфиг, тот же тейк/стоп, тот же блок флипа, те же издержки. Кандидат
   живёт, только если нетто 2EMA выше 95-го перцентиля плацебо. Иначе на Si
   проходит ФАКТОР «медленный тренд без переворотов», а не стратегия.

B. ПРОСАДКА. Тейк 8xATR против стопа 1% — это примерно 1:4, такая асимметрия даёт
   высокую долю выигрышей и левый хвост; «плюс в каждом году» у неё дёшев (память:
   мираж нулевого риска). Считаем максимальную просадку MTM по контракту и худшую
   нереализованную просадку позиции (max_mae). Порог не ставим, но число обязано
   быть на столе рядом с доходом.

C. ОКРЕСТНОСТЬ. Дефолт 10/140 не должен быть счастливой точкой: сетка ema1 x ema2,
   требуем медиану сетки > 0 и >= 60% плюсовых точек.

    PYTHONPATH=. python scripts/si_flip_controls.py --splice Si.txt [--draws 60]
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics as st
import sys
import types
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, "scripts")
from flip_block_matrix import BASE, FLIP_OFF, SLIP, _sig_params  # noqa: E402

from trader.lab.backtest import run_single_backtest  # noqa: E402
from trader.lab.commission import taker_points  # noqa: E402
from trader.lab.runtime import BacktestRuntime  # noqa: E402
from trader.lab.strategies.library import (  # noqa: E402
    AVG_PARAMS, DESKBOT_PARAMS, REGISTRY, make_on_bar)

TREND = ["shectory_2ema", "shectory_3ema", "macd_cross", "macd_shectory1",
         "triple_sma", "roc", "momentum", "ema_atr", "keltner_bo", "bollinger_bo"]
FIXED = {p["key"] for p in AVG_PARAMS + DESKBOT_PARAMS} | {"symbol", "qty",
                                                           "bet_step", "bet_max"}
ORDERED = [("fast", "slow"), ("ema1", "ema2"), ("ema2", "ema3"), ("fast", "mid"),
           ("mid", "slow")]
SYM = "Si"          # переопределяется --sym: тот же контроль на другом инструменте


def score(bars, trades, contract) -> tuple[float, float, float]:
    """(нетто пт, макс. просадка MTM пт, худшая нереализованная просадка пт)."""
    fills = sorted((t for t in trades if t.get("time") is not None),
                   key=lambda t: t["time"])
    cash = pos = cost = 0.0
    peak = dd = 0.0
    entry = 0.0          # средняя цена текущей позиции
    mae = 0.0
    i = 0
    for b in bars:
        while i < len(fills) and fills[i]["time"] <= b.time:
            f = fills[i]
            s = 1 if f["side"] == "buy" else -1
            q = f["qty"]
            new = pos + s * q
            if pos == 0 or (pos > 0) == (s > 0):                 # вход или долив
                entry = (entry * abs(pos) + f["price"] * q) / max(abs(new), 1)
            elif new == 0 or (new > 0) != (pos > 0):             # выход/переворот
                entry = f["price"] if new != 0 else 0.0
            pos = new
            cash -= s * q * f["price"]
            cost += taker_points(contract, f["price"], q) + SLIP[SYM] * q
            i += 1
        if pos != 0 and entry > 0:
            mae = min(mae, (b.low - entry) * pos if pos > 0 else (b.high - entry) * pos)
        eq = cash + pos * b.close - cost
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return cash + pos * bars[-1].close - cost, dd, mae


def _one(item):
    contract, bars, strategy, params = item
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(strategy)
    p = {**params, **BASE, "symbol": contract, "flip_min_pts": FLIP_OFF}
    r = asyncio.run(run_single_backtest(mod, bars, contract, p, point_value=1.0,
                                        runtime_cls=BacktestRuntime))
    return contract, score(bars, r["trades"], contract)


def draw(rng: random.Random) -> tuple[str, dict]:
    rid = rng.choice(TREND)
    p = {s["key"]: rng.randint(int(s["min"]), int(s["max"]))
         for s in REGISTRY[rid]["params_schema"]
         if s["key"] not in FIXED and s.get("type") == "number"}
    for a, b in ORDERED:                 # быстрая короче медленной, иначе вырождение
        if a in p and b in p and p[a] >= p[b]:
            p[a], p[b] = min(p[a], p[b]), max(p[a], p[b]) + 1
    return rid, p


def run_all(ex, by, jobs) -> dict:
    """jobs: список (метка, стратегия, params). Возвращает метка -> список score."""
    tasks, keys = [], []
    for label, strategy, params in jobs:
        for c, b in sorted(by.items()):
            tasks.append((c, b, strategy, params))
            keys.append(label)
    out: dict = {}
    for key, (contract, sc) in zip(keys, ex.map(_one, tasks, chunksize=1)):
        out.setdefault(key, []).append(sc)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splice", required=True)
    ap.add_argument("--draws", type=int, default=60)
    ap.add_argument("--sym", default="Si", help="база инструмента для полспреда")
    ap.add_argument("--only-placebo", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    global SYM
    SYM = a.sym
    from regime_step0 import load_ri
    by = {c: b for c, b in load_ri(a.splice).items() if len(b) > 5000}
    print(f"{a.sym}: контрактов {len(by)}, минуток {sum(len(b) for b in by.values())}",
          flush=True)

    with ProcessPoolExecutor(a.workers) as ex:
        # B. Просадка у самих кандидатов. --only-placebo пропускает B и C:
        # на чужом инструменте проверяется только базовая частота фактора.
        base_jobs = [(s, s, _sig_params(s)) for s in ("shectory_2ema", "shectory_3ema")]
        res = {} if a.only_placebo else run_all(ex, by, base_jobs)
        target = {}
        for label, scs in res.items():
            net = sum(x[0] for x in scs)
            target[label] = net
            print(f"B {label:16} нетто {net:+8.0f} пт | худшая просадка контракта "
                  f"{max(x[1] for x in scs):8.0f} пт | худшая нереализованная "
                  f"{min(x[2] for x in scs):9.0f} пт", flush=True)

        # C. Окрестность периодов 2EMA (дефолт 10/140 — одна из точек сетки).
        grid = [(f"2ema {e1}/{e2}", "shectory_2ema", {"ema1": e1, "ema2": e2})
                for e1 in (5, 8, 10, 13, 20) for e2 in (80, 110, 140, 180, 240)]
        res = {} if a.only_placebo else run_all(ex, by, grid)
        nets = {k: sum(x[0] for x in v) for k, v in res.items()}
        pos = sum(v > 0 for v in nets.values()) if nets else 0
        if nets:
            print(f"\nC окрестность 2EMA: точек {len(nets)}, плюсовых {pos} "
                  f"({100 * pos / len(nets):.0f}%), медиана "
                  f"{st.median(nets.values()):+8.0f} пт, "
                  f"дефолт 10/140 {nets['2ema 10/140']:+8.0f} пт", flush=True)
            for k in sorted(nets, key=lambda k: -nets[k]):
                print(f"   {k:14} {nets[k]:+9.0f}", flush=True)

        # A. Плацебо: случайные трендовые правила с тем же выходом.
        rng = random.Random(20260918)
        jobs, seen = [], []
        while len(jobs) < a.draws:
            rid, p = draw(rng)
            jobs.append((f"pl{len(jobs)}", rid, p))
            seen.append((rid, p))
        res = run_all(ex, by, jobs)
        pl = sorted(sum(x[0] for x in v) for v in res.values())
        q95 = pl[int(0.95 * (len(pl) - 1))]
        print(f"\nA плацебо: розыгрышей {len(pl)}, медиана {st.median(pl):+9.0f} пт, "
              f"95-й перцентиль {q95:+9.0f} пт, лучший {pl[-1]:+9.0f}", flush=True)
        for label, net in target.items():
            better = sum(x >= net for x in pl)
            print(f"   {label:16} {net:+8.0f} пт -> плацебо не хуже у {better} из "
                  f"{len(pl)} (p={better / len(pl):.3f}) "
                  f"{'ПРОШЁЛ' if net > q95 else 'НЕ ПРОШЁЛ'}", flush=True)


if __name__ == "__main__":
    main()
