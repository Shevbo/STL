"""Плацебо трендового фактора на RI (15.09.2026, по проверке fable).

Гейт regime_tf_gate.py дал 8 кандидатов из 30 на M15/H1, все трендовые, корреляция
0.4-0.8. Вопрос: сколько проходит ЛЮБОЕ трендовое правило на RI 2022-26? Берём
случайные параметры (равномерно в границах схемы) трендовых стратегий реестра на
случайном ТФ из {15, 60} и гоним через тот же гейт и те же издержки.

КРИТЕРИЙ (записан ДО прогона):
  базовая частота = доля случайных розыгрышей, прошедших гейт (нетто > 0 на 2022-24 и
  2025-26, > 0 в >= 3 из 5 лет, >= 200 контрактов);
  keltner_bo M15 (по умолчанию) — кандидат, только если его нетто БЕЗ RIH2 (+122131)
  выше 95-го перцентиля того же показателя у плацебо. Иначе линия закрыта: проходит
  фактор «тренд на RI», а не стратегия.

    PYTHONPATH=. python scripts/regime_tf_placebo.py --ri C:/Users/Boris/Downloads/RI.txt --draws 200
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, "scripts")
from regime_step0 import aggregate, daily_mtm, load_ri  # noqa: E402
from regime_step0_test import FEE_RATE, HALF_SPREAD  # noqa: E402

from trader.lab.backtest import run_single_backtest  # noqa: E402
from trader.lab.strategies.library import AVG_PARAMS, DESKBOT_PARAMS, REGISTRY, make_on_bar  # noqa: E402

TREND = ["keltner_bo", "bollinger_bo", "shectory_2ema", "macd_cross", "triple_sma", "roc", "momentum", "ema_atr", "fvg"]
FIXED = {p["key"] for p in AVG_PARAMS + DESKBOT_PARAMS} | {"symbol", "qty", "bet_step", "bet_max"}
ORDERED = [("fast", "slow"), ("ema1", "ema2"), ("fast", "mid"), ("mid", "slow")]
KELTNER_NO_RIH2 = 122131


def draw(rng: random.Random) -> tuple[str, int, dict]:
    rid = rng.choice(TREND)
    p = {s["key"]: rng.randint(int(s["min"]), int(s["max"]))
         for s in REGISTRY[rid]["params_schema"] if s["key"] not in FIXED and s.get("type") == "number"}
    for a, b in ORDERED:                     # быстрая короче медленной, иначе вырождение
        if a in p and b in p and p[a] >= p[b]:
            p[a], p[b] = min(p[a], p[b]) - (p[a] == p[b]), max(p[a], p[b])
    return rid, rng.choice((15, 60)), p


def _run(k, rid, contract, bars, p):
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(rid)
    params = {**REGISTRY[rid]["default_params"], "bet_step": 0, **p, "symbol": contract, "flatten_end": 1}
    r = asyncio.run(run_single_backtest(mod, bars, contract, params))
    close = {datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%d"): b.close for b in bars}
    yr, fills = defaultdict(float), 0
    for d, (pnl, n) in daily_mtm(bars, r["trades"]).items():
        yr[d[:4]] += pnl - n * (FEE_RATE * close[d] + HALF_SPREAD)
        fills += n
    return k, contract, dict(yr), fills


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ri", required=True)
    ap.add_argument("--draws", type=int, default=200)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260915)
    a = ap.parse_args()

    m1 = load_ri(a.ri)
    bars = {tf: {c: aggregate(b, tf) for c, b in m1.items()} for tf in (15, 60)}
    del m1
    rng = random.Random(a.seed)
    draws = [draw(rng) for _ in range(a.draws)]
    yr = defaultdict(lambda: defaultdict(float))
    rih2, fills = defaultdict(float), defaultdict(int)
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(_run, k, rid, c, bars[tf][c], p)
                for k, (rid, tf, p) in enumerate(draws) for c in bars[tf]]
        for i, fu in enumerate(as_completed(futs), 1):
            k, c, y, n = fu.result()
            for key, v in y.items():
                yr[k][key] += v
                if c == "RIH2":
                    rih2[k] += v
            fills[k] += n
            if i % 500 == 0:
                print(f"{i}/{len(futs)}", flush=True)

    passed, no_rih2 = 0, []
    for k, (rid, tf, p) in enumerate(draws):
        y = yr[k]
        a1 = sum(v for key, v in y.items() if key <= "2024")
        a2 = sum(v for key, v in y.items() if key >= "2025")
        ok = a1 > 0 and a2 > 0 and sum(v > 0 for v in y.values()) >= 3 and fills[k] >= 200
        passed += ok
        no_rih2.append(sum(y.values()) - rih2[k])
        print(f"{'ПРОШЁЛ ' if ok else '       '}{rid:14} M{tf:<2} {a1:+9.0f} {a2:+9.0f} без RIH2 {no_rih2[-1]:+9.0f} {p}")
    no_rih2.sort()
    p95 = no_rih2[int(0.95 * len(no_rih2))]
    share = sum(v >= KELTNER_NO_RIH2 for v in no_rih2) / len(no_rih2)
    print(f"базовая частота прохождения гейта {passed}/{len(draws)} = {passed / len(draws):.0%}")
    print(f"нетто без RIH2: медиана {no_rih2[len(no_rih2) // 2]:+.0f}, p95 {p95:+.0f}; keltner M15 {KELTNER_NO_RIH2:+} "
          f"(доля плацебо не хуже: {share:.1%}) -> {'КАНДИДАТ' if KELTNER_NO_RIH2 > p95 else 'линия закрыта'}")


if __name__ == "__main__":
    main()
