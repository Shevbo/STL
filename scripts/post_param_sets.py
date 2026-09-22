"""Отправить готовые наборы параметров в очередь i9 (кусками).

ЗАЧЕМ ОТДЕЛЬНЫЙ ШАГ. Наборы строятся из СХЕМЫ стратегии, а схема живёт в коде. На
хостере чекаут репозитория отстаёт (21.09.2026 — на 36 коммитов), и сетка,
построенная там, молча теряла новые оси: четыре сегодняшних параметра в переборе
вообще не участвовали. Пулить прод ради перебора нельзя — чекаут общий, и рестарт
подхватил бы чужие 36 коммитов. Поэтому наборы генерируются ТАМ, ГДЕ СВЕЖИЙ КОД
(окно разработчика), кладутся в файл, а этот скрипт только отправляет их.

    # там, где свежий код:
    python scripts/queue_today_sweep.py ... --dump sets.json
    # затем на хостере (у него есть токен агента):
    PYTHONPATH=. python scripts/post_param_sets.py --file sets.json \
        --symbol spRIZ6d0921 --book-key bookRIZ6d0921 --campaign st1 \
        --date-from 2026-09-19 --date-to 2026-09-21
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BOOK_CHUNK = 150      # см. --chunk: крупное задание по стакану теряется молча

CODE = ("from trader.lab.strategies.library import make_on_bar\n"
        "on_bar = make_on_bar('macd_shectory1')")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="JSON: {base: {...}, sets: [...]}")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--book-key")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    # ПОТОЛОК ПРИ ИСПОЛНЕНИИ ПО СТАКАНУ. 22.09.2026 одно задание на 542 набора по
    # 40-дневной выжимке (29 110 минут стакана) вернулось с i9 ПУСТЫМ СПИСКОМ
    # результатов: ни одной упавшей комбинации, ни ошибки в логе хостера, задание
    # помечено done, строк ноль. Те же 542 набора четырьмя заданиями по 140 легли
    # полностью. Стакан держит каждый воркер, их 14, поэтому крупное задание
    # выносит память молча. Никогда не полагаться на видимый статус задания:
    # сверять число строк в лидерборде с числом отправленных наборов.
    ap.add_argument("--chunk", type=int, default=5000)
    ap.add_argument("--api", default=os.environ.get("STL_API", "http://localhost:8000"))
    a = ap.parse_args()

    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        sys.exit("нет OPT_AGENT_TOKEN")
    payload = json.load(open(a.file, encoding="utf-8"))
    sets, base = payload["sets"], dict(payload.get("base") or {})
    base["symbol"] = a.symbol
    if a.book_key:
        base["book_key"] = a.book_key

    h = {"X-Agent-Token": tok, "Content-Type": "application/json"}
    chunk = a.chunk
    if a.book_key and chunk > BOOK_CHUNK:
        chunk = BOOK_CHUNK
        print(f"исполнение по стакану: размер задания срезан до {chunk} наборов")
    chunks = [sets[i:i + chunk] for i in range(0, len(sets), chunk)]
    ok = 0
    with httpx.Client(base_url=a.api, headers=h, timeout=300) as c:
        for i, part in enumerate(chunks):
            body = {"scriptCode": CODE, "baseParams": base, "paramSets": part,
                    "symbol": a.symbol, "dateFrom": f"{a.date_from}T00:00:00",
                    "dateTo": f"{a.date_to}T23:59:59", "engine": "remote",
                    "campaign": f"{a.campaign}{i:03d}"}
            try:
                c.post("/api/v1/backtest/run", json=body).raise_for_status()
                ok += len(part)
            except Exception as exc:  # noqa: BLE001
                print(f"  кусок {i} не встал: {exc}")
    print(f"поставлено {ok} наборов в {len(chunks)} заданиях, кампания {a.campaign}*")
    # Задание может вернуться пустым при статусе done (см. --chunk). Считать прогон
    # состоявшимся только после сверки числа строк с числом отправленных наборов.
    print(f"СВЕРЬ по окончании: строк в лидерборде должно быть {ok}
"
          f"  SELECT count(*) FROM optimization_leaderboard
"
          f"  WHERE campaign_run LIKE 'camp-%{a.campaign.replace('-', '')}%';")


if __name__ == "__main__":
    main()
