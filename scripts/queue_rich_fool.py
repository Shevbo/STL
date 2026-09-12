"""Перебор rich_fool — предоткрытийная лестница фейда импульса ОТКРЫТИЯ — на i9.

СПЕЦИФИКАЦИЯ ОПЕРАТОРА (09.09.2026, уточнена 12.09.2026):
  Уровни:  price(0)=вчерашнее закрытие, price(n)=price(n-1)+D/(n+1), D = amp·d_coef,
           amp = средний дневной размах за n_days. Зазоры убывают (D/2, D/3, D/4 …).
           d_coef ∈ [0.05, 1.0] СУЖАЕТ лестницу.
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
сторону, нижняя граница 0.05. step_count выше 5 инертен: дальние ступени недостижимы.
invert — зеркальная ось контроля, фейд обязан бить прямой пробой.

ГЛАВНАЯ ОПАСНОСТЬ ЭТОГО ПЕРЕБОРА. Узкая лестница при ОДНИХ И ТЕХ ЖЕ параметрах даёт
на RIM6 (март-июнь) +13..+26 тыс, а на RIU6 (июнь-сентябрь) −21..−45 тыс. Сделок в
обоих случаях 54-81, то есть это не малая выборка: знак определяется КОНТРАКТОМ, а не
настройкой. Поэтому вершину по net нельзя принимать за кандидата — строка обязана
пройти оба квартала одного инструмента и зеркало invert на ТЕХ ЖЕ параметрах.

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
    # ВОЛНА 2: сетка расширена ЗА КРАЯ, в которые упёрлась волна 1. Все 262 строки,
    # прошедшие гейт по двум кварталам RI, сидели на границах одновременно по
    # пяти-семи осям (hold_min=30 мин, step_count=5 макс, sl=0.50% мин,
    # trail=0.25% мин, vol=1.6 макс, d=0.05 мин) — это не найденный оптимум, а
    # «перебор доехал до стенки». Прежние граничные значения ОСТАВЛЕНЫ в сетке,
    # чтобы новый результат был сравним со старым и было видно, ушёл ли экстремум
    # внутрь расширенного диапазона.
    # d_coef: нижняя граница 0.05 задана оператором, ниже не идём
    "d_coef":       [5, 10, 20],
    # окно набора: вниз от 30 — лидеры выбирали минимум
    "hold_min":     [5, 10, 20, 30],
    # ступеней: вверх от 5 — лидеры выбирали максимум
    "step_count":   [5, 8, 12, 20],
    # множитель объёма к предыдущей заявке: вверх от 1.6
    "vol_mult":     [16, 20, 25, 30],
    # запас стопа за последней ступенью: вниз от 0.50%
    "sl_price_pct": [10, 20, 35, 50],
    # трейлинг-тейк: вниз от 0.25%
    "trail_tp_pct": [10, 15, 25],
    "n_days":       [5, 10],
    "invert":       [0, 1],
}

PIN = dict(qty=1, max_contracts=60, place_lead_min=10,
           ema_fast=9, ema_slow=21, exit_lead_min=120,
           allow_long=1, allow_short=1, bar_offset_min=0)

# Чанк: контракт × invert × hold_min -> 3·4·4·4·3·2 = 1152 paramSets в задании.
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
                "campaign": f"rf4{side}{secid}h{hold}",
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
