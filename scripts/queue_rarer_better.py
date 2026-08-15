"""«Реже, но эффективнее»: перебор частоты СРАЗУ на двух контрактах.

Заказ оператора 15.08.2026. Первый заход по издержкам провалился ровно потому,
что подбирался на одном контракте: выигрыш сидел в окне подбора, а на соседнем
контракте конфиг терял 44%. Поэтому здесь одна и та же сетка гоняется на RIU6 и
на RIM6, а годным считается только то, что улучшает ОБА.

Оси меняют ЧАСТОТУ, но не сигнал — иначе получится другая стратегия, и право
называться проверенной она потеряет:
  avg_step_atr  шаг лестницы: шире шаг — реже доборы;
  min_gap_atr   разножка от последнего исполнившегося добора;
  cooldown_min  пауза после прибыльного выхода;
  avg_max       потолок доборов: короче лестница — меньше сделок;
  dv_range_pts  ширина коридора боковика.

Тейк зафиксирован на 80: он единственный, что подтвердился в обе стороны в
прошлом заходе, и трогать его снова значит проверять уже проверенное.

ЧИТАТЬ РЕЗУЛЬТАТ по net ПОСЛЕ СПРЕДА и по net НА СДЕЛКУ, а не по net: в
бэктесте спреда нет вовсе, а робот кроссит его на каждой стороне.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_rarer_better.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

# Оба контракта на их собственных живых окнах. RIM6 истекает 18.06, дальше баров
# нет — окно обрезано честно, а не «до сегодня».
TARGETS = [("RIU6", "2026-05-04", "2026-08-08"),
           ("RIM6", "2026-02-01", "2026-06-18")]

# Кандидат как он сейчас стоит на живом роботе.
BASE = {"qty": 1, "fast": 57, "slow": 48, "signal": 10, "sl_pct": 100, "sl_frac": 0,
        "avg_atr_n": 25, "nd_days": 5, "gap_auto": 0, "min_gap_pts": 0,
        "allow_long": 1, "allow_short": 1, "dv_bars": 60, "tp_atr": 80,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1,
        "bet_step": 2, "bet_max": 10, "super_y": 2, "super_z": 2, "k_avg": 20,
        "cooldown_pct": 1}
CODE = ("from trader.lab.strategies.library import make_on_bar\n"
        "on_bar = make_on_bar('macd_shectory1')")

STEPS = [21, 30, 40, 60]        # шаг усреднения ×ATR/10 (21 = как сейчас)
GAPS = [0, 10, 20]              # разножка ×ATR/10
COOLDOWNS = [0, 120, 360]       # минут паузы после прибыльного выхода
AVG_MAXES = [12, 20]            # потолок доборов (20 = как сейчас)
VALLEYS = [300, 500]            # коридор боковика в пунктах (300 = как сейчас)

# Порция ≤32: ручной прогон уходит на РЕЗЕРВНЫЙ воркер i9, у которого потолок
# MANUAL_SIDE_MAX_COMBOS=32, а задание крупнее возвращается помеченным DONE С
# НУЛЁМ строк — снаружи неотличимо от честно посчитанной пустоты (15.08.2026).
CHUNK = 32


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    sets = [{"avg_step_atr": st, "min_gap_atr": g, "cooldown_min": cd,
             "avg_max": am, "dv_range_pts": dv}
            for st in STEPS for g in GAPS for cd in COOLDOWNS
            for am in AVG_MAXES for dv in VALLEYS]
    chunks = [sets[i:i + CHUNK] for i in range(0, len(sets), CHUNK)]
    print(f"комбинаций {len(sets)} × контрактов {len(TARGETS)} = "
          f"{len(sets) * len(TARGETS)} прогонов, заданий {len(chunks) * len(TARGETS)}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for sym, a, b in TARGETS:
            for i, part in enumerate(chunks, 1):
                r = cl.post("/api/v1/backtest/run", json={
                    "campaign": f"rare{sym[:4].lower()}{i}", "scriptCode": CODE,
                    "symbol": sym, "baseParams": dict(BASE, symbol=sym),
                    "dateFrom": a, "dateTo": b, "engine": "remote",
                    "priority": 100, "paramSets": part})
                ok += r.status_code in (200, 201, 202)
    print(f"поставлено {ok} заданий")


if __name__ == "__main__":
    main()
