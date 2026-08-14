"""Поиск конфига по золоту: не «какой сигнал лучше», а «что торгует РЕЖЕ».

14.08.2026 замер показал, где именно золото проигрывает: macd на GDU6 при qty=1
и avg_max=1 угадывает направление в 63% случаев и всё равно теряет 55 756 ₽ за
три месяца — потому что делает 1315 сделок. Это стена комиссии, а не сигнал, и
чинится она частотой, а не подбором периодов.

Поэтому перебираются ЧЕТЫРЕ регулятора частоты, одинаковые для всех семейств:
остывание после прибыльной сделки, разножка в долях ATR, размер тейка и потолок
доборов. Сигнальные параметры взяты дефолтные — их перебор осмыслен только
после того, как найдена рабочая частота.

Долина смерти считается в ПУНКТАХ, поэтому её коридор пересчитан под цену
золота: 300 пунктов на RI при цене 83 000 это 0.36%, а на золоте при 4 400 —
целых 7%, и робот считает боковиком вообще всё. На этом первый прогон дал ноль
сделок.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_gold_search.py --dry-run
    PYTHONPATH=. $PY scripts/queue_gold_search.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from trader.auth.portal import make_session_token
from trader.lab.strategies.library import REGISTRY

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
DATE_FROM, DATE_TO = "2026-05-04", "2026-08-08"
SYMBOLS = ["GDU6", "GDM6"]          # рабочий контракт и второй, для проверки переноса

# Семейства нарочно разные: тренд, пробой канала, скользящие, осциллятор. Если
# частота вылечит только одно из них — это свойство семейства, а не золота.
STRATEGIES = ["macd_shectory1", "bollinger_bo", "triple_sma", "williams_r"]

COOLDOWNS = [0, 60, 180]        # минут без новых входов после прибыльного выхода
GAPS = [0, 13, 26]              # разножка в долях ATR ×10
TAKES = [40, 80, 120]           # тейк ×ATR/10: дальше тейк — дольше держим
AVG_MAXES = [1, 4]              # без лестницы и с короткой лестницей
DV = [(0, 0), (60, 16)]         # долина выкл / коридор, пересчитанный под золото


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    sets = [{"cooldown_min": cd, "cooldown_pct": 0, "min_gap_atr": g, "tp_atr": tp,
             "avg_max": am, "avg_step_atr": 10 if am > 1 else 0,
             "dv_bars": dvb, "dv_range_pts": dvr}
            for cd in COOLDOWNS for g in GAPS for tp in TAKES
            for am in AVG_MAXES for dvb, dvr in DV]

    jobs = []
    for sid in STRATEGIES:
        spec = REGISTRY.get(sid)
        if spec is None:
            print(f"нет такой стратегии в реестре: {sid}")
            continue
        base = dict(spec["default_params"])
        base["qty"] = 1                      # qty только масштабирует P&L
        for sym in SYMBOLS:
            jobs.append({"campaign": f"gold{sid[:8]}", "scriptCode": script_code(sid),
                         "symbol": sym, "baseParams": dict(base, symbol=sym),
                         "dateFrom": DATE_FROM, "dateTo": DATE_TO,
                         "engine": "remote", "paramSets": sets})

    print(f"стратегий {len(STRATEGIES)} × контрактов {len(SYMBOLS)} × комбо {len(sets)}"
          f" = {len(jobs) * len(sets)} прогонов")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code not in (200, 201, 202):
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok} из {len(jobs)}")


if __name__ == "__main__":
    main()
