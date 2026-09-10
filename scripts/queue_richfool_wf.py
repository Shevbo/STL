"""Проверка вперёд для rich_fool: те же конфиги на окнах, которых отбор не видел.

ЗАЧЕМ. Ночной широкий перебор 09.09 (33 960 строк на RI, окно 09.03-08.09)
дал верхнюю строку +943 961 ₽ при 55 сделках и пике 120 контрактов. Рынок за
то же окно УПАЛ на 28% (RI 117 010 -> 83 800, купил-и-держал на 120 контрактах
= -6.8 млн), значит это не бета. Но строка выбрана лучшей из 33 960 на ОДНОМ
окне: ровно так выглядели все прошлые миражи (OB BRU6, shectory_2ema, camp-20260731).
Отличить преимущество от подгонки можно только одним способом: прогнать ТЕ ЖЕ
конфиги на окнах, которых отбор не касался.

ЧТО СЧИТАЕТСЯ. Три окна на двух символах:
  pre   2025-09-09..2026-03-09  шесть месяцев ДО окна отбора
  half2 2026-06-09..2026-09-08  вторая половина окна отбора
  post  2026-08-21..2026-09-08  данные, появившиеся ПОСЛЕ сетки (07.09+)
Плюс зеркальный тест: тот же конфиг с перевёрнутым invert, чтобы понять, что
несёт результат — механизм лестницы или конкретная сторона сигнала.

Кандидат выживает, только если плюсовой на pre И post, на обоих символах, и
зеркало при этом НЕ даёт столько же (иначе знак сигнала ни при чём).

ЗАПУСК НА ХОСТЕРЕ (конфиги готовит выгрузка из БД, см. --configs):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_richfool_wf.py --dry-run
    PYTHONPATH=. $PY scripts/queue_richfool_wf.py --submit
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
CODE = "from trader.lab.strategies.rich_fool import on_bar, on_start, on_stop"

WINDOWS = [
    ("pre",   "2025-09-09", "2026-03-09"),   # отбор этих баров не видел
    ("half2", "2026-06-09", "2026-09-08"),   # вторая половина окна отбора
    ("post",  "2026-08-21", "2026-09-08"),   # данные ПОСЛЕ сетки
]
SYMBOLS = ["RI", "Si"]
CAMPAIGN = "camp-20260910-rfwf"
PRIORITY = 40
# 90 = предел счёта оператора (10.09); пик выше физически невозможно поставить.
MAX_CONTRACTS = 90


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="/tmp/rf_wf.json",
                    help="json [{params, net, tr, peak}, ...] — конфиги из отбора")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--max-contracts", type=int, default=90,
                    help="потолок позиции: 90 контрактов — предел счёта оператора")
    ap.add_argument("--tag", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    raw = json.load(open(args.configs, encoding="utf-8"))[:args.limit]
    # symbol принадлежит заданию, не конфигу: один и тот же набор гоняется на RI и Si.
    # qty=1 обязателен: первая ступень лестницы ВСЕГДА 1 контракт (по указанию
    # оператора 10.09), рост объёма задаёт только vol_mult. Потолок 90 — его счёт.
    cfgs = [{**{k: v for k, v in c["params"].items() if k != "symbol"},
             "qty": 1, "max_contracts": args.max_contracts} for c in raw]

    jobs = []
    for sym in SYMBOLS:
        for label, df, dt in WINDOWS:
            for mirror in (False, True):
                sets = []
                for c in cfgs:
                    p = dict(c, symbol=sym)
                    if mirror:
                        p["invert"] = 0 if int(p.get("invert", 0)) else 1
                    sets.append(p)
                tag = f"{sym}-{label}{'-mirror' if mirror else ''}"
                if args.tag:
                    tag = f"{tag}-{args.tag}"
                jobs.append({
                    "campaign": f"{CAMPAIGN}-{tag}",
                    "scriptCode": CODE, "symbol": sym,
                    "baseParams": {"symbol": sym},
                    "dateFrom": df, "dateTo": dt,
                    "engine": "remote", "priority": PRIORITY,
                    "paramSets": sets,
                })

    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"конфигов {len(cfgs)} | окон {len(WINDOWS)} | символов {len(SYMBOLS)} | "
          f"зеркало вкл | заданий {len(jobs)} | прогонов {total}")
    for j in jobs:
        print(f"  {j['baseParams']['symbol']:<3} {j['dateFrom']}..{j['dateTo']} "
              f"{j['campaign'].rsplit('-', 1)[-1]:<8} {len(j['paramSets'])} шт")
    if args.dry_run or not args.submit:
        print("Сухой прогон — ничего не поставлено.")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=180) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                ok += 1
                print(f"  поставлено {j['symbol']} {j['dateFrom']}..{j['dateTo']} "
                      f"-> {r.json().get('runId', '?')}")
            else:
                err += 1
                print(f"  ошибка {r.status_code}: {r.text[:160]}")
    print(f"поставлено {ok}, ошибок {err}, прогонов {total}")


if __name__ == "__main__":
    main()
