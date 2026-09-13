"""Перебор valley_spike: «другое дальнее» × «другая долина» (заказ оператора 13.09.2026).

ЗАЧЕМ. Проба 9309e75 упёрлась в стену частоты: при разрыве средней и долины ≥50%
дневной свечи — 0-5 сделок за квартал, при ≥25% — 16-27, знак 2 из 4. Оператор
велел взять перебором ровно два параметра:
  min_gap_amp  «дальнее»: насколько далеко медленная средняя ДО долины должна стоять
               от середины долины, % дневной свечи. Ниже порог — больше долин
               вооружается. Уровень лестницы от этого не меняется: это всё та же
               замороженная средняя.
  dv_bars      «долина»: окно, за которое размах закрытий обязан уложиться в коридор.
               Короче окно — долин больше, и они короче.
Остальное закреплено по последней пробе. ttl_bars=240: без срока жизни лестницы
наливов нет вовсе (выброс на полсвечи одним баром не делается), а дальше 240 баров
результат не меняется.

КВАРТАЛЫ. По контрактам, без сшивки. Выборка M6/U6 и вне выборки M5/U5/Z5/H6 по RI и Si.
Порог частоты — 100 сделок за квартал; знак обязан держаться на обоих кварталах выборки.
P&L истёкших RI — в ПУНКТАХ (point_value=1.0, в instrument_meta их нет), живых — в
рублях: сравнивать по знаку и числу сделок, а величину RI переводить руками.

ПЕРЕД ЗАПУСКОМ: бампнуть update_token и сверить code_sha в i9_heartbeat с отпечатком
на ХОСТЕРЕ (valley_spike.py в agent/update_manifest.txt).

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_valley_spike.py --dry-run
    PYTHONPATH=. $PY scripts/queue_valley_spike.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
CODE = "from trader.lab.strategies.valley_spike import on_bar, on_start, on_stop"

CONTRACTS = [
    (f"{inst}{q}", a, b) for inst in ("RI", "Si") for q, a, b in (
        ("M6", "2026-03-20", "2026-06-17"),
        ("U6", "2026-06-19", "2026-09-10"),
        ("M5", "2025-03-20", "2025-06-19"),
        ("U5", "2025-06-20", "2025-09-18"),
        ("Z5", "2025-09-19", "2025-12-18"),
        ("H6", "2025-12-19", "2026-03-19"),
    )
]

AXES = {
    "min_gap_amp": [0, 5, 10, 15, 20, 25],
    "dv_bars": [15, 30, 60, 120],
}
PIN = dict(qty=1, step_count=3, d_coef=50, vol_mult=10, max_contracts=10, amp_days=5,
           slow_n=400, dv_amp=30, mirror=0, ret_pct=50, stop_amp=25, slip_amp=3,
           max_hold=240, ttl_bars=240, allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="vs1")
    # priority DESC: больше — раньше. Очередь сейчас пуста, 20 — выше хвоста фона.
    ap.add_argument("--priority", type=int, default=20)
    args = ap.parse_args()

    combos = [dict(zip(AXES, v)) for v in itertools.product(*AXES.values())]
    jobs = [{
        "campaign": f"{args.tag}-{sym.lower()}",
        "scriptCode": CODE, "symbol": sym,
        "baseParams": dict(PIN, symbol=sym),
        "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
        "priority": args.priority,
        "paramSets": combos,
    } for sym, d_from, d_to in CONTRACTS]

    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | комбо {total}")
    for j in jobs:
        print(f"  {j['campaign']:12s} {j['dateFrom']}..{j['dateTo']}  {len(j['paramSets'])} шт")
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
