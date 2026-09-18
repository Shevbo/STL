"""«Волшебная десятка» на Si: диверсификация или плечо x10?

ВОПРОС ОПЕРАТОРА (18.09.2026). Раз внутри класса разброс случайных правил шире
разницы между ними, не запустить ли сразу десять роботов с разными параметрами —
в совокупности они дадут стабильный заработок?

ЧТО РЕШАЕТ ОТВЕТ. Только корреляция. Если десять правил ходят вместе, десять
роботов по лоту это ОДНО правило десятью лотами: доход x10, просадка x10, шансы
те же. Диверсификация начинается там, где итог портфеля колеблется слабее суммы
своих частей, и меряется это честным сравнением равной экспозиции (память:
«усреднение = плечо, сравнивать с базой равной экспозиции»).

КАК МЕРЯЕМ. Десять правил — сетка периодов 2EMA, выбранная ДО прогона так, чтобы
покрыть углы и середину (быстрая 5..20, медленная 80..240). Для каждого правила
считаем нетто по КАЛЕНДАРНЫМ МЕСЯЦАМ (56 наблюдений вместо 19 контрактов — на
корреляцию этого хватает), дальше:

  средняя парная корреляция месячных рядов;
  SD месяца у одного правила против SD месяца у портфеля из десяти (равный вес);
  то же в пересчёте на равную экспозицию: 10 правил по лоту против 1 правила
    десятью лотами — у второго доход и SD ровно x10, поэтому сравниваем
    отношение дохода к SD, а не сами уровни;
  доля месяцев, где портфель в минусе, и худший месяц портфеля.

    PYTHONPATH=. python scripts/si_portfolio_ten.py --splice Si.txt
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st
import sys
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, "scripts")
from flip_block_matrix import BASE, FLIP_OFF, SLIP  # noqa: E402

from trader.lab.backtest import run_single_backtest  # noqa: E402
from trader.lab.commission import taker_points  # noqa: E402
from trader.lab.runtime import BacktestRuntime  # noqa: E402
from trader.lab.strategies.library import make_on_bar  # noqa: E402

# Десятка выбрана ДО прогона: углы сетки, середины сторон и центр.
TEN = [(5, 80), (5, 140), (5, 240), (10, 110), (10, 140),
       (10, 240), (13, 80), (13, 180), (20, 110), (20, 240)]
SYM = "Si"


def monthly(bars, trades, contract) -> dict[str, float]:
    """Нетто по календарным месяцам (МСК-стенка): MTM за месяц минус издержки месяца."""
    fills = sorted((t for t in trades if t.get("time") is not None),
                   key=lambda t: t["time"])
    out: dict[str, float] = defaultdict(float)
    cash = pos = 0.0
    prev_eq = 0.0
    cost = 0.0            # НАКОПИТЕЛЬНО: обнуление на баре теряло издержки целиком
    i = 0
    for b in bars:
        while i < len(fills) and fills[i]["time"] <= b.time:
            f = fills[i]
            s = 1 if f["side"] == "buy" else -1
            pos += s * f["qty"]
            cash -= s * f["qty"] * f["price"]
            cost += taker_points(contract, f["price"], f["qty"]) + SLIP[SYM] * f["qty"]
            i += 1
        eq = cash + pos * b.close - cost
        key = datetime.fromtimestamp(b.time, tz=timezone.utc).strftime("%Y-%m")
        out[key] += eq - prev_eq
        prev_eq = eq
    return dict(out)


def _one(item):
    contract, bars, e1, e2 = item
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("shectory_2ema")
    p = {**BASE, "ema1": e1, "ema2": e2, "symbol": contract,
         "flip_min_pts": FLIP_OFF}
    r = asyncio.run(run_single_backtest(mod, bars, contract, p, point_value=1.0,
                                        runtime_cls=BacktestRuntime))
    return (e1, e2), monthly(bars, r["trades"], contract)


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splice", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    from regime_step0 import load_ri
    by = {c: b for c, b in load_ri(a.splice).items() if len(b) > 5000}

    per: dict[tuple, dict[str, float]] = {r: defaultdict(float) for r in TEN}
    tasks = [(c, b, e1, e2) for c, b in sorted(by.items()) for e1, e2 in TEN]
    with ProcessPoolExecutor(a.workers) as ex:
        for rule, months in ex.map(_one, tasks):
            for k, v in months.items():
                per[rule][k] += v

    keys = sorted({k for m in per.values() for k in m})
    series = {r: [per[r].get(k, 0.0) for k in keys] for r in TEN}

    print(f"Si, {len(keys)} месяцев, правил {len(TEN)}\n")
    print(f"{'правило':12} {'итог пт':>9} {'ср.месяц':>9} {'SD месяца':>10} "
          f"{'плюс.мес':>9}")
    for r in TEN:
        v = series[r]
        print(f"2ema {r[0]:2d}/{r[1]:<4d} {sum(v):+9.0f} {st.mean(v):+9.0f} "
              f"{st.stdev(v):10.0f} {sum(x > 0 for x in v):5d}/{len(v):<3d}")

    pairs = [corr(series[TEN[i]], series[TEN[j]])
             for i in range(len(TEN)) for j in range(i + 1, len(TEN))]
    port = [sum(series[r][i] for r in TEN) for i in range(len(keys))]
    single = [st.mean([sum(series[r]) for r in TEN])]           # для справки
    sd_single = st.mean([st.stdev(series[r]) for r in TEN])
    mean_single = st.mean([st.mean(series[r]) for r in TEN])

    print(f"\nсредняя парная корреляция месячных рядов: {st.mean(pairs):.2f} "
          f"(мин {min(pairs):.2f}, макс {max(pairs):.2f})")
    print(f"одно правило, 1 лот:      средний месяц {mean_single:+8.0f} пт, "
          f"SD {sd_single:8.0f}, доход/SD {mean_single / sd_single:+5.2f}")
    print(f"десятка, по лоту каждая:  средний месяц {st.mean(port):+8.0f} пт, "
          f"SD {st.stdev(port):8.0f}, доход/SD {st.mean(port) / st.stdev(port):+5.2f}")
    print(f"одно правило, 10 лотов:   средний месяц {10 * mean_single:+8.0f} пт, "
          f"SD {10 * sd_single:8.0f}, доход/SD {mean_single / sd_single:+5.2f} "
          f"(отношение не меняется — это и есть плечо)")
    gain = (mean_single / sd_single)
    gain_port = st.mean(port) / st.stdev(port)
    print(f"\nвыигрыш десятки против одного правила по доход/SD: "
          f"x{gain_port / gain:.2f} (при независимых правилах было бы x3.16)")
    print(f"минусовых месяцев у портфеля {sum(x < 0 for x in port)}/{len(port)}, "
          f"худший месяц {min(port):+.0f} пт, худший у одного правила "
          f"{min(min(series[r]) for r in TEN):+.0f} пт")
    del single


if __name__ == "__main__":
    main()
