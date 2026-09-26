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

def script_code(strategy: str, module: str | None) -> str:
    """Тело прогона. Стратегии живут двумя способами, и это надо различать:
    в РЕЕСТРЕ library (macd_shectory1 и прочие, собираются make_on_bar) и
    ОТДЕЛЬНЫМ модулем со своим on_bar (us_open_fvg, rich_fool, impulse_fade).
    Скрипт был прибит к первому способу, и прогон робота открытия США поставить
    было нельзя (упёрся заказ real-trade 25.09.2026)."""
    if module:
        return f"from trader.lab.strategies.{module} import on_bar"
    return ("from trader.lab.strategies.library import make_on_bar" + chr(10) +
            f"on_bar = make_on_bar({strategy!r})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="JSON: {base: {...}, sets: [...]}")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--strategy", default="macd_shectory1",
                    help="id стратегии из реестра library")
    ap.add_argument("--module", help="модуль trader/lab/strategies/<имя>.py со своим "
                                     "on_bar; отменяет --strategy")
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
            body = {"scriptCode": script_code(a.strategy, a.module), "baseParams": base, "paramSets": part,
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
    tag = a.campaign.replace("-", "")
    print(f"СВЕРЬ по окончании: строк в лидерборде должно быть {ok}")
    print(f"  SELECT count(*) FROM optimization_leaderboard "
          f"WHERE campaign_run LIKE 'camp-%{tag}%';")


if __name__ == "__main__":
    main()
