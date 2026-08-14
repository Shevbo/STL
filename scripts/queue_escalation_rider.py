"""Райдер «после убытка — крупнее и агрессивнее»: перебор ТРЁХ существующих осей.

Заказ оператора 14.08.2026: после стоп-лосса увеличивать позицию и агрессию
(qty++, k_avg++). Две трети этого в `make_on_bar` уже есть, и обе взводятся
именно убытком, стоп в том числе (`bet_step > 0` в ветке стопа):

  bet_step / bet_max   после каждой убыточной ЗАКРЫТОЙ сделки следующий вход
                       больше на bet_step контрактов (1->2->3...), после
                       прибыльной сброс к базовому qty, потолок добавки bet_max.
  super_y / super_z    после убыточного выхода растут СРАЗУ qty И avg_max на
                       super_y, до super_z эскалаций, сброс после прибыли.
                       Складывается с bet_step, они независимы.

Чего в коде НЕТ: эскалации САМОГО k_avg (коэффициента роста доборов). Поэтому
третья ось здесь СТАТИЧЕСКАЯ — k_avg перебирается как настройка, а не как
реакция на убыток. Это честная замена: она отвечает на вопрос «нужна ли вообще
более агрессивная лестница», не выдавая себя за заказанный райдер.

Базы берутся ЖИВЫЕ, а не лидеры лидерборда: смысл прогона — узнать, что райдер
сделал бы с тем, что реально торгуется или только что поставлено.

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_escalation_rider.py --dry-run
    PYTHONPATH=. $PY scripts/queue_escalation_rider.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
DATE_FROM, DATE_TO = "2026-05-04", "2026-08-08"

# (bet_step, bet_max): 0/0 — выключено, дальше шаг ставки и её потолок.
BETS = [(0, 0), (1, 2), (1, 5), (1, 10), (2, 5), (2, 10)]
# (super_y, super_z): прибавка к qty И avg_max за эскалацию и число эскалаций.
SUPERS = [(0, 0), (1, 1), (1, 2), (1, 3), (2, 2), (2, 3)]
# k_avg хранится ×10: 10 = все доборы равны, 20 = каждый следующий вдвое больше.
K_AVGS = [10, 15, 20]

# Живой реальный робот, только что поставленный шортовый двойник и единственное
# семейство, прошедшее гейт уровня 1 (bollinger_bo на Brent, оба контракта).
BASES = [
    ("lxk22-real", "macd_shectory1", "RIU6", {
        "qty": 1, "avg_max": 20, "fast": 57, "slow": 48, "signal": 10, "tp_atr": 40,
        "avg_atr_n": 25, "avg_step_atr": 21, "min_gap_pts": 0, "cooldown_min": 0,
        "cooldown_pct": 1, "nd_days": 5, "gap_auto": 0, "sl_frac": 0, "sl_pct": 100,
        "allow_long": 1, "allow_short": 1, "dv_bars": 60, "dv_range_pts": 300}),
    ("macdshort", "macd_shectory1", "RIU6", {
        "qty": 1, "avg_max": 10, "fast": 57, "slow": 48, "signal": 10, "tp_atr": 40,
        "avg_atr_n": 25, "avg_step_atr": 21, "min_gap_pts": 0, "cooldown_min": 0,
        "cooldown_pct": 1, "nd_days": 5, "gap_auto": 0, "sl_frac": 0, "sl_pct": 100,
        "allow_long": 0, "allow_short": 1, "dv_bars": 60, "dv_range_pts": 300}),
    ("bollbo-q", "bollinger_bo", "BRQ6", {
        "qty": 1, "period": 23, "mult": 30, "tp_atr": 60, "avg_max": 7,
        "avg_step_atr": 10, "avg_atr_n": 14, "min_gap_atr": 13, "min_gap_pts": 0,
        "sl_pct": 67, "sl_frac": 0, "cooldown_min": 0, "cooldown_pct": 1,
        "allow_long": 1, "allow_short": 1, "dv_bars": 0, "dv_range_pts": 0}),
    ("bollbo-u", "bollinger_bo", "BRU6", {
        "qty": 1, "period": 23, "mult": 30, "tp_atr": 60, "avg_max": 7,
        "avg_step_atr": 10, "avg_atr_n": 14, "min_gap_atr": 13, "min_gap_pts": 0,
        "sl_pct": 67, "sl_frac": 0, "cooldown_min": 0, "cooldown_pct": 1,
        "allow_long": 1, "allow_short": 1, "dv_bars": 0, "dv_range_pts": 0}),
]


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    sets = [{"bet_step": bs, "bet_max": bm, "super_y": sy, "super_z": sz, "k_avg": k}
            for bs, bm in BETS for sy, sz in SUPERS for k in K_AVGS]
    jobs = [{"campaign": f"rider{tag.replace('-', '')}", "scriptCode": script_code(sid),
             "symbol": sym, "baseParams": dict(p, symbol=sym),
             "dateFrom": DATE_FROM, "dateTo": DATE_TO, "engine": "remote",
             "paramSets": sets}
            for tag, sid, sym, p in BASES]

    print(f"баз {len(jobs)} × комбо {len(sets)} = {len(jobs) * len(sets)} прогонов "
          f"на окне {DATE_FROM}..{DATE_TO}")
    for tag, sid, sym, _ in BASES:
        print(f"  {tag:12s} {sid:16s} {sym}")
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
