"""Перебор rich_fool — предоткрытийная лестница фейда импульса ОТКРЫТИЯ — на i9.

СПЕЦИФИКАЦИЯ ОПЕРАТОРА (09.09.2026, уточнена 12.09.2026):
  Уровни:  price(0)=вчерашнее закрытие, price(n)=price(n-1)+D/(n+1), D = amp·d_coef,
           amp = средний дневной размах за n_days. Зазоры убывают (D/2, D/3, D/4 …).
           d_coef ∈ [0.2, 1.0] СУЖАЕТ лестницу.
  Вход:    цена ускакала ВВЕРХ -> ШОРТ с добором по ступеням вверх; ВНИЗ -> ЛОНГ.
  Объём:   один дробный множитель к предыдущей заявке (vol_mult), потолок max_contracts.
  Стоп:    от средней и ЗА последней ступенью лестницы (запас sl_price_pct % от цены).
  Тейк:    трейлинг trail_tp_pct % от экстремума, только в прибыли.
  Заявки:  снимаются через hold_min ТОЛЬКО если не было ни одной сделки.
  Овернайт ЗАПРЕЩЁН: за exit_lead_min до закрытия выход по двум EMA, на закрытии —
           принудительно, плюс страховка на первом баре нового дня.
  Сессия:  открытие = первый бар дня, закрытие = последний бар предыдущего дня того
           же типа. Час нигде не прописан: расписание FORTS менялось внутри периода.

ПОКОНТРАКТНО, НЕ ПО СКЛЕЙКЕ. Непрерывная серия RI сшита через перекат без выравнивания
базиса: шов 01.07.2026 составил −13.01%, и «лидер» прошлого прогона сделал на нём 96%
итога — чистый фантом. Внутри контракта швов нет (максимальный скачок 1.72%). Каждый
контракт гоняется на своём ФРОНТ-окне: от экспирации предыдущего до своей.

ПРО СЕТКУ. Проба показала: при d_coef=1.0 лестница стоит слишком далеко от цены и даёт
1-7 сделок за три месяца, при d_coef=0.2 — 54-66. Поэтому ось d_coef смещена в узкую
сторону. step_count выше 5 инертен: дальние ступени недостижимы. invert — зеркальная
ось контроля, фейд обязан бить прямой пробой.

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
CODE = "from trader.lab.strategies.rich_fool import on_bar, on_start, on_stop"

# Фронт-контракт и его ликвидное окно.
CONTRACTS = [
    ("RIM6", "2026-03-20", "2026-06-17"),
    ("RIU6", "2026-06-19", "2026-09-09"),
    ("SiM6", "2026-03-20", "2026-06-17"),
    ("SiU6", "2026-06-19", "2026-09-09"),
]

AXES = {
    # коэффициент к D: 20 = 0.20×amp (узкая лестница), 100 = 1.00×amp
    "d_coef":       [20, 30, 45, 70, 100],
    "hold_min":     [30, 60, 120, 240],
    "n_days":       [3, 5, 10],
    "step_count":   [1, 2, 3, 5],
    # множитель объёма к ПРЕДЫДУЩЕЙ заявке: 10 = ровно, 13 = ×1.3, 16 = ×1.6
    "vol_mult":     [10, 13, 16],
    # запас стопа ЗА последней ступенью, % от цены ×100
    "sl_price_pct": [50, 100, 200],
    # трейлинг-тейк, % от цены ×100
    "trail_tp_pct": [20, 35, 50, 80],
    "invert":       [0, 1],
}
PIN = dict(qty=1, max_contracts=60, place_lead_min=10,
           ema_fast=9, ema_slow=21, exit_lead_min=120,
           allow_long=1, allow_short=1, bar_offset_min=0)

# Чанк: контракт × invert × hold_min -> 5·3·4·3·3·4 = 2160 paramSets в задании.
CHUNK_KEYS = ("invert", "hold_min")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*AXES.values())]
    jobs = []
    for secid, d_from, d_to in CONTRACTS:
        for inv, hold in itertools.product(AXES["invert"], AXES["hold_min"]):
            sets = [c for c in combos if c["invert"] == inv and c["hold_min"] == hold]
            side = "fade" if inv == 0 else "brk"
            jobs.append({
                "campaign": f"rf3{side}{secid}h{hold}",
                "scriptCode": CODE, "symbol": secid,
                "baseParams": dict(PIN, symbol=secid, invert=inv, hold_min=hold),
                "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                "priority": 40,        # прямой запрос оператора — вперёд фоновых кампаний
                "paramSets": [{k: c[k] for k in keys if k not in CHUNK_KEYS} for c in sets],
            })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"контрактов {len(CONTRACTS)} | заданий {len(jobs)} | комбо {total}")
    print(f"в задании paramSets: {len(jobs[0]['paramSets'])}")
    for j in jobs[:4]:
        print(f"  {j['campaign']:20s} {j['symbol']:5s} {j['dateFrom']}..{j['dateTo']} "
              f"sets={len(j['paramSets'])}")
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
