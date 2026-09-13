"""Слои DeskBot поверх shectory_2ema на M1 (решение оператора 13.09.2026).

ЗАЧЕМ. Голый кросс на M1 гейт не прошёл (e2a/e2b). Оператор велел всё равно проверить
слои DeskBot. Слой поверх сигнала без эджа — кандидат в подгонку, поэтому каждый слой
идёт ОТДЕЛЬНОЙ осью против той же базы (matched pair), и гейт тот же: 6 кварталов.

БАЗА (зафиксирована заранее — лучшие строки e2a/e2b по устойчивости, не по net):
  RI (150, 300) — 5/6 кварталов;  Si (10, 2100) — 5/6 кварталов.
СЛОИ (layer_id — ключ строки для гейта, движок его не читает):
  0         база без слоёв
  1-3       tp_pct 30 / 60 / 120 (0.3 / 0.6 / 1.2%)
  4-6       трейл обе стороны act/back 50/20, 100/40, 200/60
  7-9       тейк по RSI n/lvl 60/70, 120/75, 300/70
  10-11     усреднение avg_max=3, шаг в пунктах (RI 400/800, Si 300/600) обе стороны
  12        «как DeskBot»: tp 1.2%, трейл лонг 3.5/1.1 шорт 1.0/0.6, RSI 120/72, стоп 1%,
            усреднение 3 ступени шаг лонг/шорт RI 400/500, Si 300/375

ГЕЙТ: vs_gate.py --strategy shectory_2ema --keys layer_id,ema1 --tag e2l
Слой — кандидат, только если проходит гейт И лучше базы (layer 0) на >= 4 из 6 кварталов.
Край сетки для layer_id смысла не имеет — смотреть колонку «край» только по параметру слоя.

ЗАПУСК НА ХОСТЕРЕ (после bump update_token и сверки lib_sha):
    PYTHONPATH=. $PY scripts/queue_2ema_layers.py --dry-run
    PYTHONPATH=. $PY scripts/queue_2ema_layers.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from scripts.queue_2ema import API, CODE, CONTRACTS, EMAIL, PIN
from trader.auth.portal import make_session_token

BASE_PAIR = {"RI": (150, 300), "Si": (10, 2100)}
STEPS = {"RI": (400, 800, 400, 500), "Si": (300, 600, 300, 375)}   # слой 10, 11, DeskBot L, S


def layers(inst: str) -> list[dict]:
    s10, s11, dl, ds = STEPS[inst]
    out = [{}]
    out += [{"tp_pct": v} for v in (30, 60, 120)]
    out += [{"trail_act_l": a, "trail_back_l": b, "trail_act_s": a, "trail_back_s": b}
            for a, b in ((50, 20), (100, 40), (200, 60))]
    out += [{"rsi_tp_n": n, "rsi_tp_lvl": lv} for n, lv in ((60, 70), (120, 75), (300, 70))]
    out += [{"avg_max": 3, "avg_step_pts_l": s, "avg_step_pts_s": s} for s in (s10, s11)]
    out += [{"tp_pct": 120, "trail_act_l": 350, "trail_back_l": 110, "trail_act_s": 100,
             "trail_back_s": 60, "rsi_tp_n": 120, "rsi_tp_lvl": 72, "sl_pct": 100,
             "avg_max": 3, "avg_step_pts_l": dl, "avg_step_pts_s": ds}]
    f, s = BASE_PAIR[inst]
    return [{**lay, "layer_id": i, "ema1": f, "ema2": s} for i, lay in enumerate(out)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="e2l")
    ap.add_argument("--priority", type=int, default=20)
    args = ap.parse_args()

    jobs = [{
        "campaign": f"{args.tag}-{sym.lower()}",
        "scriptCode": CODE, "symbol": sym,
        "baseParams": dict(PIN, symbol=sym),
        "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
        "priority": args.priority,
        "paramSets": layers(sym[:2]),
    } for sym, d_from, d_to in CONTRACTS]
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | комбо {total}")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=180) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code >= 300:
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}/{len(jobs)}, комбо {total}")


if __name__ == "__main__":
    main()
