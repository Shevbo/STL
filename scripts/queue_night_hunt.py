"""Ночная охота: 5 стратегий, зарабатывающих на РОСТЕ или во всех трёх режимах.

Заказ оператора 17.08.2026: «гоняй всю ночь, найди 5 лучших на росте или
универсальных солдат». Здесь этап 1 — широкое сито; этап 2 (проверка выживших на
остальных окнах) ставится отдельным скриптом по результатам этого.

ПОЧЕМУ ВОРОНКА, А НЕ ОДИН ОГРОМНЫЙ ПЕРЕБОР. За день 17.08 четыре независимых
разбора дали один и тот же итог: на окне отбора эталон бьют тысячи конфигов, на
любом независимом окне преимущество исчезает. Перебирать сразу по восьми окнам
дорого и бессмысленно — почти всё умрёт на втором. Поэтому этап 1 гоняет ДВА
окна, растущее и падающее, и оставляет только тех, кто плюсов на ОБОИХ; всё
остальное отсеивается там, где это дёшево.

ЧТО ИМЕННО ПЕРЕБИРАЕТСЯ. Сигнальные параметры каждой стратегии берутся из её
собственной схемы (по три значения на ось: низ, середина, верх диапазона), а
сверху накладываются ТРИ райдера, которых у большинства стратегий ещё не было:
  райдер 1  эскалация после убытка: bet_step/bet_max и super_y/super_z;
  райдер 2  расписание сторон внутри дня: tod_m1/tod_m2 + маски;
  райдер 3  пропуск сигналов после крупной сделки: signals2ignor_*.

ПЛАНКА РАЙДЕРА 3 В ПУНКТАХ и потому инструментальная: 300 пунктов на RI при цене
78 000 это 0.4%, а на золоте при 4 400 — 7%. Здесь оба окна этапа 1 на RI, так
что пересчёт не нужен; на этапе 2 он обязателен (см. rescale в queue_keltner_l2).

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_night_hunt.py --dry-run
    PYTHONPATH=. $PY scripts/queue_night_hunt.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token
from trader.lab.strategies.library import REGISTRY

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

# Этап 1: одно растущее окно и одно падающее, оба на RI и оба с проверенными
# данными (415-675 баров в день). Прочие окна — на этапе 2.
RISE = ("RIH6", "2025-11-17", "2026-01-24")      # RI рос +19.9%
FALL = ("RIU6", "2026-05-04", "2026-08-07")      # RI падал −21.8%

# Оси райдеров общие для всех стратегий: они живут в AVG_PARAMS.
# (bet_step, bet_max, super_y, super_z)
ESCALATION = [(0, 0, 0, 0), (1, 5, 0, 0), (0, 0, 2, 2), (2, 10, 2, 3)]
# (tod_m1, tod_m2, tod_s1, tod_s2, tod_s3); первый — расписание выключено
SCHEDULES = [(0, 0, 3, 3, 3),
             (600, 1080, 1, 2, 3),      # гипотеза оператора на круглых границах
             (600, 1080, 3, 2, 1),      # лучшее у живого macd
             (671, 1140, 2, 3, 2)]      # лучшее у bollinger и triple_sma
# (signals2ignor_win, signals2ignor_lose, signals2ignor_value)
IGNORES = [(0, 0, 0), (2, 0, 300), (0, 2, 300), (2, 2, 300), (3, 1, 600), (1, 3, 600)]

# Ключи, которые перебирать НЕЛЬЗЯ: размер только масштабирует результат, а
# инфраструктурные поля к стратегии отношения не имеют.
SKIP_AXES = {"qty", "symbol", "bar_offset_min"}
# Оси райдеров задаются здесь явно и не должны попасть в сигнальную сетку второй раз.
RIDER_KEYS = {"bet_step", "bet_max", "super_y", "super_z",
              "tod_m1", "tod_m2", "tod_s1", "tod_s2", "tod_s3",
              "signals2ignor_win", "signals2ignor_lose", "signals2ignor_value",
              "avg_max", "avg_step_atr", "tp_atr", "sl_frac", "sl_pct", "avg_atr_n",
              "min_gap_pts", "min_gap_atr", "cooldown_min", "cooldown_pct",
              "nd_days", "gap_auto", "k_avg", "dv_bars", "dv_range_pts",
              "allow_long", "allow_short", "reg_n", "reg_band", "reg_mode"}


def signal_grid(sid: str, per_axis: int = 3) -> list[dict]:
    """Сетка СОБСТВЕННЫХ параметров стратегии: низ, середина, верх диапазона.

    Берём из схемы, а не выдумываем: у каждой стратегии свои оси, и жёстко
    прописанный список немедленно разошёлся бы с реестром.
    """
    spec = REGISTRY[sid]
    axes: dict[str, list] = {}
    for p in spec["params_schema"]:
        k = p.get("key", "")
        if p.get("type") != "number" or k in SKIP_AXES or k in RIDER_KEYS:
            continue
        lo, hi = float(p.get("min", 0)), float(p.get("max", 0))
        if hi <= lo:
            continue
        vals = sorted({round(lo), round((lo + hi) / 2), round(hi)})[:per_axis]
        axes[k] = vals
    if not axes:
        return [{}]
    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(axes[k] for k in keys))]


def rider_sets() -> list[dict]:
    out = []
    for (bs, bm, sy, sz), (m1, m2, s1, s2, s3), (iw, il, iv) in itertools.product(
            ESCALATION, SCHEDULES, IGNORES):
        out.append({"bet_step": bs, "bet_max": bm, "super_y": sy, "super_z": sz,
                    "tod_m1": m1, "tod_m2": m2, "tod_s1": s1, "tod_s2": s2, "tod_s3": s3,
                    "signals2ignor_win": iw, "signals2ignor_lose": il,
                    "signals2ignor_value": iv})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--strategies", default="", help="через запятую; пусто = весь реестр")
    ap.add_argument("--signal-per-axis", type=int, default=3)
    ap.add_argument("--max-signal", type=int, default=27,
                    help="потолок сигнальных комбинаций на стратегию")
    args = ap.parse_args()

    sids = [s for s in args.strategies.split(",") if s] or sorted(REGISTRY)
    riders = rider_sets()
    jobs, total = [], 0
    for sid in sids:
        sig = signal_grid(sid, args.signal_per_axis)[:args.max_signal]
        # Базовая часть — умеренная лестница: без неё райдер 1 нечему эскалировать,
        # а с огромной позиция улетает за капы агента ещё до отбора.
        base = {"qty": 1, "avg_max": 4, "avg_step_atr": 14, "tp_atr": 40,
                "avg_atr_n": 14, "sl_frac": 0, "sl_pct": 0,
                "min_gap_pts": 0, "min_gap_atr": 0, "cooldown_min": 0, "cooldown_pct": 1,
                "nd_days": 5, "gap_auto": 0, "k_avg": 10,
                "dv_bars": 0, "dv_range_pts": 0, "allow_long": 1, "allow_short": 1}
        sets = [dict(base, **s, **r) for s in sig for r in riders]
        code = ("from trader.lab.strategies.library import make_on_bar\n"
                f"on_bar = make_on_bar('{sid}')")
        for tag, (sym, a, b) in (("nhrise", RISE), ("nhfall", FALL)):
            jobs.append({"campaign": f"{tag}{sid[:10].replace('_', '')}",
                         "scriptCode": code, "symbol": sym,
                         "baseParams": dict(sets[0], symbol=sym),
                         "dateFrom": a, "dateTo": b, "engine": "remote",
                         "paramSets": [dict(x) for x in sets]})
            total += len(sets)
        print(f"  {sid:18s} сигнальных {len(sig):3d} × райдеров {len(riders)} = "
              f"{len(sets):5d} на окно")
    print(f"\nстратегий {len(sids)} | заданий {len(jobs)} | ВСЕГО ПРОГОНОВ {total:,}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=120) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code not in (200, 201, 202):
                print(f"  ошибка {r.status_code} на {j['campaign']}: {r.text[:160]}")
    print(f"поставлено {ok} из {len(jobs)}")


if __name__ == "__main__":
    main()
