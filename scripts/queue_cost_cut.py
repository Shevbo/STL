"""Срезать ЧИСЛО СДЕЛОК кандидату, не потеряв P&L. Перебор только регуляторов частоты.

Вопрос оператора 15.08.2026: кандидат делает 1083 сделки за три месяца, издержки
съедают заметную часть. Сигнальные параметры при этом не трогаем — меняя их, мы
получим ДРУГУЮ стратегию и заново потеряем право говорить, что она прошла второй
контракт и период вне отбора.

Регуляторов частоты в слое управления позицией ровно пять, и каждый режет своё:
  cooldown_min/pct  пауза после прибыльного выхода — режет серии перезаходов;
  min_gap_atr       разножка от последнего ИСПОЛНИВШЕГОСЯ добора — режет лестницу,
                    схлопывающуюся в одну цену;
  tp_atr            дальше тейк — дольше держим, реже оборачиваемся;
  avg_step_atr      шире шаг усреднения — меньше доборов;
  dv_range_pts      шире коридор боковика — больше времени робот стоит вне рынка.

ЧИТАТЬ РЕЗУЛЬТАТ НАДО ПО NET ПОСЛЕ СПРЕДА, а не по net. В бэктесте спреда нет
вовсе, а заявки робота маркетабельны по построению: на каждой стороне теряется
минимум шаг цены. Поэтому конфиг, срезавший сделки вдвое при том же net, в бою
ВЫИГРЫВАЕТ, хотя в таблице бэктеста выглядит равным. Оценка спреда та же, что в
гейте кандидата: сделки × 2 × qty × рублёвая стоимость шага.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_cost_cut.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
DATE_FROM, DATE_TO = "2026-05-04", "2026-08-08"

# Кандидат как он есть: конфиг живого lxk22 + расписание сторон + эскалация.
BASE = {"qty": 1, "fast": 57, "slow": 48, "signal": 10, "symbol": "RIU6",
        "sl_pct": 100, "sl_frac": 0, "avg_max": 20, "avg_atr_n": 25,
        "nd_days": 5, "gap_auto": 0, "min_gap_pts": 0, "allow_long": 1,
        "allow_short": 1, "dv_bars": 60,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1,
        "bet_step": 2, "bet_max": 10, "super_y": 2, "super_z": 2, "k_avg": 20}

COOLDOWNS = [0, 30, 60, 120, 240]     # минут паузы после прибыльного выхода
COOL_PCT = [0, 1]                     # 0 = после ЛЮБОГО неубыточного выхода
GAPS = [0, 10, 20, 30]                # разножка ×ATR/10
TAKES = [40, 80, 120]                 # тейк ×ATR/10 (40 = как у кандидата)
STEPS = [21, 30, 40]                  # шаг усреднения ×ATR/10 (21 = как у кандидата)
VALLEYS = [300, 500, 800]             # коридор боковика в пунктах (300 = как сейчас)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    sets = [{"cooldown_min": cd, "cooldown_pct": cp, "min_gap_atr": g,
             "tp_atr": tp, "avg_step_atr": st, "dv_range_pts": dv}
            for cd in COOLDOWNS for cp in COOL_PCT for g in GAPS
            for tp in TAKES for st in STEPS for dv in VALLEYS]
    # ПОРЦИЯМИ ПО 32. Ручной прогон (priority 100) идёт на РЕЗЕРВНЫЙ воркер i9, а
    # у того есть потолок MANUAL_SIDE_MAX_COMBOS=32: задание крупнее агент
    # возвращает — и, как выяснилось 15.08.2026, помечает его DONE С НУЛЁМ строк.
    # Со стороны это неотличимо от честно посчитанной пустоты. Поэтому режем сами:
    # 1080 комбинаций одним заданием молча пропали за 30 секунд.
    chunks = [sets[i:i + 32] for i in range(0, len(sets), 32)]
    print(f"комбинаций: {len(sets)} -> заданий по 32: {len(chunks)}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return
    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for i, part in enumerate(chunks, 1):
            job = {"campaign": f"costcut{i}", "symbol": "RIU6", "baseParams": BASE,
                   "scriptCode": ("from trader.lab.strategies.library import make_on_bar\n"
                                  "on_bar = make_on_bar('macd_shectory1')"),
                   "dateFrom": DATE_FROM, "dateTo": DATE_TO, "engine": "remote",
                   "priority": 100, "paramSets": part}
            r = cl.post("/api/v1/backtest/run", json=job)
            ok += r.status_code in (200, 201, 202)
    print(f"поставлено {ok} из {len(chunks)}")


if __name__ == "__main__":
    main()
