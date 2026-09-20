"""Поставить хит-парад лидерборда в очередь i9 — с исполнением ПО СТАКАНУ.

ЗАЧЕМ. Строки лидерборда посчитаны исполнением по открытию следующего бара: ни
спреда, ни глубины. Архив сырого рынка (12.08.2026 и дальше) позволяет исполнить
те же сделки по реальной встречной стороне. Окно архива для всех строк топа —
ЧУЖОЕ: их подбирали по 20.07.2026 и раньше, так что это ещё и выход за окно
подгонки.

ГДЕ СЧИТАЕТСЯ. Только i9: задания кладутся в очередь (engine=remote), агент
забирает их сам. Ни локальная машина, ни хостер под расчёт не используются.

КАК СТАКАН ПОПАДАЕТ НА i9. Выжимка архива (scripts/book_digest.py) лежит на
хостере как `agent_bars/book<КОД>.json` и отдаётся той же ручкой, что бары:
`/api/v1/agent/bars/book<КОД>`. Ключ едет агенту в `base_params["book_key"]` —
ручка /claim отдаёт фиксированный набор полей и своих ключей задания не
пропускает, а base_params пропускает как есть; агент снимает ключ перед
прогоном, в параметры стратегии он не попадает.

ПАРА К СРАВНЕНИЮ. По умолчанию каждая строка ставится ДВАЖДЫ: по барам и по
стакану. Одна строка без пары ничего не говорит — разница и есть цена
исполнения.

    python scripts/queue_book_top.py --rows top_ri.json --symbol RIU6 \
        --book-key bookRIU6 --date-from 2026-08-13 --date-to 2026-09-17
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _token() -> str:
    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        sys.exit("нет OPT_AGENT_TOKEN в окружении")
    return tok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="JSON строк лидерборда (leaderboard_top)")
    ap.add_argument("--symbol", required=True, help="контракт для прогона, напр. RIU6")
    ap.add_argument("--book-key", required=True, help="ключ выжимки, напр. bookRIU6")
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--campaign", default="bookoos")
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    ap.add_argument("--bars-only", action="store_true", help="без пары по стакану")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = json.load(open(a.rows, encoding="utf-8"))
    headers = {"X-Agent-Token": _token(), "Content-Type": "application/json"}

    with httpx.Client(base_url=a.api, headers=headers, timeout=60) as client:
        tpl = {s["id"]: s for s in client.get("/api/v1/strategies").json()}
        missing = {r["strategy"] for r in rows} - set(tpl)
        if missing:
            sys.exit(f"нет шаблонов стратегий: {', '.join(sorted(missing))}")

        jobs = []
        for i, r in enumerate(rows, 1):
            params = {k: v for k, v in r["params"].items() if k != "symbol"}
            params["symbol"] = a.symbol
            for mode in (["бар"] if a.bars_only else ["бар", "стакан"]):
                ps = dict(params)
                if mode == "стакан":
                    ps["book_key"] = a.book_key
                jobs.append({
                    "scriptCode": tpl[r["strategy"]]["script_code"],
                    "baseParams": ps,
                    "paramSets": [{}],          # одна комбинация: сами base_params
                    "symbol": a.symbol,
                    "dateFrom": f"{a.date_from}T00:00:00",
                    "dateTo": f"{a.date_to}T23:59:59",
                    "engine": "remote",
                    # НОМЕР СТРОКИ В ИМЕНИ КАМПАНИИ ОБЯЗАТЕЛЕН: id прогона сервер
                    # собирает из кампании, стратегии и символа, а в топе одна и та
                    # же стратегия стоит десятком строк с разными параметрами —
                    # одинаковые имена дают duplicate key и молча теряют задания.
                    "campaign": f"{a.campaign}{'book' if mode == 'стакан' else 'bar'}{i:02d}",
                    "_meta": {"rank": i, "strategy": r["strategy"], "mode": mode,
                              "fit_net": r.get("net_profit")},
                })

        print(f"строк {len(rows)} -> заданий {len(jobs)} "
              f"({'только бар' if a.bars_only else 'бар + стакан'}), "
              f"окно {a.date_from}..{a.date_to}, символ {a.symbol}")
        if a.dry_run:
            for j in jobs[:6]:
                print("  ", j["_meta"], list(j["baseParams"])[:6])
            print("  … dry-run, ничего не поставлено")
            return

        ok = err = 0
        for j in jobs:
            try:
                resp = client.post("/api/v1/backtest/run", json=j)
                resp.raise_for_status()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                err += 1
                print(f"  ошибка на {j['_meta']}: {exc}")
        print(f"поставлено {ok}, ошибок {err}")


if __name__ == "__main__":
    main()
