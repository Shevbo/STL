"""Уровень 2 для keltner_bo с растущего окна: единственный кандидат без плеча.

Найден 17.08 в кампании camp-20260817-risejul (19 231 строка на растущем окне RI
16-30.07). Верх той кампании по деньгам занимают мартингейлы: лидер даёт 313 929 ₽
при 19% побед, наращивая вход на 4 контракта после каждого убытка до 29 — на росте
такое зарабатывает по построению. Этот конфиг другой: ОДИН контракт, 50 сделок,
58% побед, 20 998 ₽ — против 8 397 ₽ у «купил контракт 16-го и держал до 30-го».
Два с половиной эталона без единого лишнего контракта.

Что проверяем и почему именно так:
  РОСТ ДРУГИХ ОКОН   RIH6, RIZ5 — рост RI вне окна отбора. SiU6 (+8.2%) и GZH6
                     (+4.2%) — рост на ДРУГИХ активах: если преимущество от
                     стратегии, а не от подгонки под июль RI, оно обязано
                     проявиться и там.
  ПАДЕНИЕ            RIU6 май-авг, RIM6 фев-июнь. Стратегия двусторонняя, значит
                     на падении она обязана как минимум не терять.
  ВПЕРЁД             RIU6 08-08..08-17 — баров при отборе не существовало.

Эталон «купил и держал» считается на КАЖДОМ окне отдельно, тем же кодом
(scripts/verify_leaderboard_row.py делает это же для строк лидерборда). Сравнивать
net без эталона нельзя: на растущем окне заработает и кирпич.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_keltner_l2.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

# Точные параметры найденной строки. min_gap_atr намеренно 0: при avg_max=1
# усреднения нет вовсе, и разножка ничего не делает — три «разные» верхние
# строки кампании отличались только ею и дали побайтово один результат.
CFG = {"qty": 1, "mult": 30, "ema_period": 23, "atr_period": 5,
       "avg_max": 1, "avg_step_atr": 0, "tp_atr": 0, "sl_frac": 0, "sl_pct": 0,
       "avg_atr_n": 14, "min_gap_atr": 0, "min_gap_pts": 0,
       "cooldown_min": 0, "cooldown_pct": 1,
       "dv_bars": 160, "dv_range_pts": 999,
       "allow_long": 1, "allow_short": 1,
       "tod_m1": 0, "tod_m2": 0, "tod_s1": 3, "tod_s2": 3, "tod_s3": 3,
       "reg_n": 0, "reg_band": 0, "reg_mode": 1}

# (тег, символ, начало, конец, что было с рынком, множитель пунктов к цене RI)
RUNS = [
    ("kelriseh", "RIH6", "2025-11-17", "2026-01-24", "RI рос +19.9%", 1.0),
    ("kelrisez", "RIZ5", "2025-11-17", "2025-12-17", "RI рос +10.2%", 1.0),
    ("kelrisesi", "SiU6", "2026-05-04", "2026-08-07", "Si рос +8.2%", 1.09),
    ("kelrisegz", "GZH6", "2025-11-17", "2026-01-24", "GZ рос +4.2%", 0.115),
    ("kelfallu", "RIU6", "2026-05-04", "2026-08-07", "RI падал −21.8%", 1.0),
    ("kelfallm", "RIM6", "2026-02-01", "2026-06-17", "RI падал −9.7%", 1.0),
    ("kelfwd", "RIU6", "2026-08-08", "2026-08-17", "вперёд, −10.6%", 1.0),
]


def rescale(cfg: dict, k: float) -> dict:
    """Пункты — величина инструмента, а не стратегии.

    dv_range_pts=999 на RI при цене 85 000 это 1.2%, а на газе при 9 000 — все
    11%, то есть «боковик всегда» и ноль сделок. На золоте этот же перенос уже
    съел целый прогон 14.08: шесть конфигов, ноль сделок, читалось как «не
    работает».
    """
    if k == 1.0:
        return dict(cfg)
    out = dict(cfg)
    for key in ("dv_range_pts", "min_gap_pts"):
        if out.get(key):
            out[key] = max(1, round(out[key] * k))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    code = ("from trader.lab.strategies.library import make_on_bar\n"
            "on_bar = make_on_bar('keltner_bo')")
    jobs = []
    for tag, sym, a, b, note, k in RUNS:
        p = rescale(CFG, k)
        jobs.append({"campaign": tag, "scriptCode": code, "symbol": sym,
                     "baseParams": dict(p, symbol=sym), "dateFrom": a, "dateTo": b,
                     "engine": "remote", "priority": 100,
                     "paramSets": [dict(p)]})
        print(f"  {tag:10s} {sym:5s} {a}..{b}  {note}")
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
