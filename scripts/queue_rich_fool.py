"""Перебор rich_fool — лестница ФЕЙДА импульса — на i9.

СПЕЦИФИКАЦИЯ ОПЕРАТОРА (09.09.2026, уточнена 12.09.2026):
  Уровни:  price(0) = вчерашнее закрытие, price(n) = price(n-1) + D/(n+1), D = amp
           (средний дневной размах high-low за n_days). Зазоры убывают: D/2, D/3, D/4…
  Вход:    цена УСКАКАЛА ВВЕРХ -> ШОРТ, далее набор позиции по уровням вверх;
           цена УСКАКАЛА ВНИЗ  -> ЛОНГ, далее набор по уровням вниз.
  Объём:   ступень hit = qty × (vol_mult/10)^hit, потолок max_contracts.
  Стоп:    СТАНДАРТНЫЙ, sl_price_pct % ОТ ЦЕНЫ входа (от средней).
  Тейк:    ТРЕЙЛИНГ, откат на trail_tp_pct % от цены от экстремума в нашу пользу,
           срабатывает только в прибыли.

ВАЖНО ПРО СЕТКУ: стоп в % от цены и шаг лестницы КОНКУРИРУЮТ. Для RI (amp≈2500 на
цене≈80000) зазор до 2-й ступени amp/3 ≈ 1.04% цены: при sl_price_pct ≤ 100 стоп
выбивает позицию РАНЬШЕ второй ступени, и step_count становится инертным. Поэтому
ось sl_price_pct растянута до 6%, а step_count — только до 8: дальние ступени
недостижимы ни при каком стопе за те же hold_min минут.

invert — ЗЕРКАЛЬНАЯ ось контроля (1 = прямой пробой). Фейд обязан бить прямой
пробой, иначе преимущества нет, а есть подгонка.

open_hour=10 закреплён: утренняя сессия 07:00 в данных разрежена (~17 баров/час),
арм-бар 06:50-06:59 есть лишь в 27 днях из 162 — прогон по ней давал 2-5 сделок.

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_rich_fool.py --dry-run
    PYTHONPATH=. $PY scripts/queue_rich_fool.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
D_FROM, D_TO = "2026-03-09", "2026-09-09"
CODE = "from trader.lab.strategies.rich_fool import on_bar, on_start, on_stop"

SYMBOLS = ["RI", "Si"]
AXES = {
    "n_days":       [3, 5, 10, 15, 20],
    "step_count":   [1, 2, 3, 5, 8],
    "vol_mult":     [10, 15, 20, 30],
    # стоп, % от цены ×100: 40 = 0.40%, 600 = 6.00%
    "sl_price_pct": [40, 70, 100, 150, 220, 320, 450, 600],
    # трейлинг-тейк, % от цены ×100: 10 = 0.10%, 260 = 2.60%
    "trail_tp_pct": [10, 20, 35, 50, 80, 120, 180, 260],
    "hold_min":     [30, 60, 120, 240, 480],
    "invert":       [0, 1],
}
PIN = dict(qty=1, open_hour=10, open_min=0, place_lead_min=10,
           max_contracts=120, allow_long=1, allow_short=1, bar_offset_min=0)

# Чанк: символ × invert × hold_min × n_days -> 8·8·5·4 = 1280 paramSets в задании.
CHUNK_KEYS = ("invert", "hold_min", "n_days")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*AXES.values())]
    jobs = []
    for sym in SYMBOLS:
        for inv, hold, nd in itertools.product(AXES["invert"], AXES["hold_min"], AXES["n_days"]):
            sets = [c for c in combos
                    if c["invert"] == inv and c["hold_min"] == hold and c["n_days"] == nd]
            side = "fade" if inv == 0 else "brk"
            jobs.append({
                "campaign": f"rf2{side}{sym}h{hold}n{nd}",
                "scriptCode": CODE, "symbol": sym,
                "baseParams": dict(PIN, symbol=sym, invert=inv, hold_min=hold, n_days=nd),
                "dateFrom": D_FROM, "dateTo": D_TO, "engine": "remote",
                "priority": 40,        # прямой запрос оператора — вперёд фоновых кампаний
                "paramSets": [{k: c[k] for k in keys if k not in CHUNK_KEYS} for c in sets],
            })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"символы {SYMBOLS} | заданий {len(jobs)} | комбо {total}")
    print(f"в задании paramSets: {len(jobs[0]['paramSets'])}")
    for j in jobs[:3]:
        print(f"  {j['campaign']:22s} sets={len(j['paramSets'])}")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=180) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                ok += 1
            else:
                err += 1
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}, ошибок {err}, комбо {total}")


if __name__ == "__main__":
    main()
