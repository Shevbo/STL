"""Проверка вперёд по времени на СКЛЕЙКЕ RI: отбираем на одном окне, судим на следующем.

ЗАЧЕМ ИМЕННО ЭТО. Два дня перебора закончились одинаково: конфиг, прибыльный на окне
отбора, разваливается на независимом. Ночной этап 2 дал ноль выживших из девяти
стратегий, четыре кандидата подряд рассыпались на втором контракте. Значит следующий
вопрос не «какая строка больше заработала», а «существует ли на RI конфиг, который
переживает СМЕНУ ОКНА» — и отвечать на него надо тем же способом, каким мы будем
торговать: выбрал на прошлом, проверил на следующем, ни разу не заглянув вперёд.

ПОЧЕМУ СКЛЕЙКА, А НЕ КОНТРАКТ. Оператор 20.08 ограничил работу одним RI и склеенными
котировками. Это заодно снимает вопрос «а не подогнались ли мы под один контракт»:
склейка проходит через ролл, и конфиг обязан пережить смену контракта внутри окна.

ЧТО СЧИТАЕТСЯ. Один и тот же набор конфигов гоняется на N последовательных окнах.
Дальше (уже не здесь, а при чтении) конфиг считается выжившим, только если он
плюсовой на КАЖДОМ окне, кроме первого: первое — окно отбора, остальные — экзамен.

Минутная история ISS живёт около четырёх месяцев, поэтому окна короткие и их четыре.
Проверять глубину данных ОБЯЗАТЕЛЬНО: окно без баров считается мгновенно и читается
как результат (на этом уже потеряли кампанию 17.08).

    python scripts/queue_walkforward_ri.py --dry-run
    python scripts/queue_walkforward_ri.py --campaign camp-20260821-wfri
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

# Четыре последовательных окна по три недели: 20.04 -> 21.08. Границы выбраны по
# календарю, а не по режиму рынка: подгонять границы под удобные куски — это тот же
# самый заглядывание вперёд, только на уровне нарезки.
FOLDS = [("2026-04-24", "2026-05-15"),
         ("2026-05-15", "2026-06-05"),
         ("2026-06-05", "2026-07-03"),
         ("2026-07-03", "2026-07-31"),
         ("2026-07-31", "2026-08-21"),
         # ШЕСТОЕ окно добавлено 07.09.2026. Это данные, появившиеся ПОСЛЕ того, как
         # сетка была посчитана: ни отбор, ни настройка их не видели. Пока окно одно,
         # оно и есть единственная честная проверка вперёд — остальные пять конфиг
         # мог подогнать под себя самим фактом отбора по ним.
         ("2026-08-21", "2026-09-07")]

SYMBOL = "RI"          # СКЛЕЙКА, не контракт


def token() -> str:
    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        sys.exit("OPT_AGENT_TOKEN не найден в окружении")
    return tok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="/tmp/wf_cfgs.json",
                    help="json {strategy: [params, ...]} — что проверяем")
    ap.add_argument("--campaign", default="camp-20260821-wfri")
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfgs: dict[str, list[dict]] = json.load(open(args.configs, encoding="utf-8"))
    jobs = []
    for sid, sets in cfgs.items():
        if not sets:
            continue
        code = ("from trader.lab.strategies.library import make_on_bar; "
                f"on_bar = make_on_bar('{sid}')")
        for i, (a, b) in enumerate(FOLDS):
            jobs.append({
                "strategy": sid, "fold": i, "combos": len(sets),
                "payload": {
                    "scriptCode": code,
                    "baseParams": {"symbol": SYMBOL, "qty": 1},
                    "symbol": SYMBOL,
                    "dateFrom": f"{a}T00:00:00",
                    "dateTo": f"{b}T23:59:59",
                    "engine": "remote",
                    "campaign": f"{args.campaign}f{i}",
                    "paramSets": [{**p, "symbol": SYMBOL} for p in sets],
                },
            })

    total = sum(j["combos"] for j in jobs)
    print(f"стратегий {len(cfgs)}, окон {len(FOLDS)}, заданий {len(jobs)}, прогонов {total:,}")
    if args.dry_run:
        for j in jobs[:10]:
            print(f"  {j['strategy']:<16} окно {j['fold']} {j['combos']:>4} комбо")
        print("Сухой прогон — ничего не поставлено.")
        return

    with httpx.Client(base_url=args.api, timeout=60,
                      headers={"X-Agent-Token": token(),
                               "Content-Type": "application/json"}) as c:
        ok = err = 0
        for i, j in enumerate(jobs, 1):
            r = c.post("/api/v1/backtest/run", json=j["payload"])
            if r.status_code in (200, 201, 202):
                ok += 1
                print(f"[{i}/{len(jobs)}] {j['strategy']} окно {j['fold']}: "
                      f"{j['combos']} комбо -> {r.json().get('runId','?')}")
            else:
                err += 1
                print(f"[{i}/{len(jobs)}] ОШИБКА {j['strategy']} окно {j['fold']}: "
                      f"{r.status_code} {r.text[:120]}")
            if i % 10 == 0:
                time.sleep(0.5)
    print(f"поставлено {ok}, ошибок {err}")


if __name__ == "__main__":
    main()
