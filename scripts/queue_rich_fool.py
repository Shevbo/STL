"""Перебор rich_fool — предоткрытийная лестница фейда импульса ОТКРЫТИЯ — на i9.

СПЕЦИФИКАЦИЯ ОПЕРАТОРА (09.09.2026, уточнена 12.09.2026):
  Уровни:  price(0)=вчерашнее закрытие, price(n)=price(n-1)+D/(n+1+F), D=amp·d_coef.
           F сдвигает знаменатель: при F=0 зазоры убывают быстро, при большом F
           выравниваются, и лестница растягивается числом ступеней, а не удалением
           первой от цены.
  Вход:    цена ускакала ВВЕРХ -> ШОРТ с добором по ступеням вверх; ВНИЗ -> ЛОНГ.
  Объём:   qty_first на первую ступень, бюджет max_contracts выбирается РОВНО на
           последней ступени, множитель роста выводится (при заданных first/steps/
           budget он единственный). Декораций нет: работают все ступени.
  Стоп:    sl_beyond_pts ПУНКТОВ за последней ступенью — ловим поклёвку в последнюю
           заявку, пошла цена дальше, выходим почти сразу.
  Тейк:    трейлинг по цене ЗАКРЫТИЯ: tp_arm_pts включает слежение, tp_back_pts
           закрывает на откате от лучшего закрытия. После тейка лестница снимается
           до конца дня.
  Заявки:  снимаются через hold_min ТОЛЬКО если не было ни одной сделки.
  Овернайт ЗАПРЕЩЁН: выход по двум EMA перед закрытием, принудительно на закрытии,
           плюс страховка на первом баре нового дня.
  Сессия:  открытие = первый бар дня, закрытие = последний бар предыдущего дня того
           же типа. Час нигде не прописан: расписание FORTS менялось внутри периода.

ПОКОНТРАКТНО, НЕ ПО СКЛЕЙКЕ: шов переката RI 01.07.2026 составил −13.01%, и «лидер»
первого прогона сделал на нём 96% итога. Внутри контракта швов нет.

d_coef НЕ ПЕРЕБИРАЕТСЯ, А ВЫВОДИТСЯ. Требование оператора: цена не должна доезжать
до последней ступени ни в одном из n_days. Это ФИКСИРУЕТ d_coef при заданных
n_days/step_count/F:
    d_coef = max(ход от вчерашнего закрытия за окно n_days) / (amp · S(m, F))
Свободной осью d_coef развалил бы условие на большей части сетки. Значение считается
ЗДЕСЬ по тем же барам, что увидит i9, и кладётся в каждый paramSet — руками числа не
переписываются.

ПОЧЕМУ ИМЕННО ЭТА ОБЛАСТЬ. Прежние волны жили в вырождении: d_coef=0.05 давал
лестницу в 155 пунктов, позиция упиралась в потолок за четыре минуты каждый день,
стоп стоял вчетверо дальше всего набора, а итог делали три дня из 75. Честная
калибровка при F=0 даёт обратную крайность — 4-9 сделок за квартал. Область F=3..10
при 20 ступенях держит и условие недостижимости (0-1% дней доходят до конца при
n_days=15), и 25-90% дней со сделкой. 12 ступеней выброшены: при них d_coef
приходится задирать до 2.0-3.5, первая ступень уезжает на 650-870 пунктов, а сделок
вдвое меньше на всех четырёх контрактах.

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_rich_fool.py --dry-run
    PYTHONPATH=. $PY scripts/queue_rich_fool.py --submit
"""
from __future__ import annotations

import argparse
import datetime
import itertools
import json
import os
import random

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
CODE = "from trader.lab.strategies.rich_fool import on_bar, on_start, on_stop"

CONTRACTS = [
    ("RIM6", "2026-03-20", "2026-06-17"),
    ("RIU6", "2026-06-19", "2026-09-09"),
    ("SiM6", "2026-03-20", "2026-06-17"),
    ("SiU6", "2026-06-19", "2026-09-09"),
]

STEP_COUNT = 20              # 12 ступеней проигрывают по всем контрактам

# ── режим СЛУЧАЙНОЙ ВЫБОРКИ (--random N) ─────────────────────────────────────
# Непрерывные диапазоны вместо нескольких точек на ось. Вектор выбирается один раз и
# прогоняется на всех контрактах и обеих сторонах invert, иначе гейт не сможет
# сравнить совпадающие строки.
SEED = 20260912
RANGES = {
    "f_shift":       (0, 100),      # F = 0.0 .. 10.0 шагом 0.1
    "n_days":        (3, 20),
    "hold_min":      (30, 150),
    "sl_beyond_pts": (5, 300),      # лог-шкала: мелкие значения важнее
    "tp_arm_pts":    (50, 1200),
    "tp_back_pts":   (20, 400),     # всегда МЕНЬШЕ tp_arm_pts
    "qty_first":     (1, 3),
    "max_contracts": (10, 80),
}

# ГРУБАЯ СЕТКА (волна rf8, 12.09 вечер): первая часть случайной выборки rf7 дала
# ноль совпадений между кварталами. Вместо 37 часов тех же диапазонов — редкие
# точки через ВЕСЬ диапазон каждой оси, ~27.6k прогонов, около двух часов на i9.
AXES = {
    "f_shift":        [0, 30, 60, 100],        # F = 0, 3, 6, 10
    "n_days":         [4, 10, 18],
    "hold_min":       [30, 90, 150],
    # rf9: стоп за калиброванной лестницей недостижим (rf8: пары векторов, отличных
    # только стопом, давали одинаковый итог), поэтому ось стопа схлопнута в одну
    # точку, а освободившийся объём отдан выходу по времени после последнего налива.
    "sl_beyond_pts":  [150],
    "time_exit_min":  [30, 90, 240],
    "tp_arm_pts":     [200, 500, 1000],        # активация слежения
    "tp_back_pts":    [40, 150],               # допустимый откат, всегда < активации
    "qty_first":      [1, 2],
    "max_contracts":  [40, 80],                # >= qty_first × 20: все ступени рабочие
    "invert":         [0, 1],
}
PIN = dict(step_count=STEP_COUNT, place_lead_min=10, slip_guard_pts=50, slip_pct=0,
           ema_fast=9, ema_slow=21, exit_lead_min=120,
           allow_long=1, allow_short=1, bar_offset_min=0)

CHUNK_KEYS = ("invert", "hold_min")


def _day_stats(secid: str, d_from: str, d_to: str):
    """По каждому дню: размах и ход от вчерашнего закрытия — как у стратегии."""
    rows = json.load(open(f"agent_bars/{secid}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(d_from).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(d_to).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    per: dict[datetime.date, list] = {}
    for ts, _o, h, low, c, _v in rows:
        if lo <= ts <= hi:
            d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()
            per.setdefault(d, []).append((h, low, c))
    out = []
    for d in sorted(per):
        bb = per[d]
        out.append({"hi": max(x[0] for x in bb), "lo": min(x[1] for x in bb),
                    "close": bb[-1][2]})
    for i, r in enumerate(out):
        r["rng"] = r["hi"] - r["lo"]
        r["exc"] = None
        if i:
            pc = out[i - 1]["close"]
            r["exc"] = max(r["hi"] - pc, pc - r["lo"])
    return out


def _d_coef(stats: list[dict], n_days: int, steps: int, f: float) -> int:
    """Минимальный d_coef (×100), при котором последняя ступень недостижима ни в
    одном из n_days. Округляем ВВЕРХ: условие должно выполняться, а не почти."""
    S = sum(1.0 / (j + 1 + f) for j in range(1, steps + 1))
    need = []
    for i in range(n_days, len(stats)):
        amp = sum(stats[j]["rng"] for j in range(i - n_days, i)) / n_days
        win = [stats[j]["exc"] for j in range(i - n_days, i) if stats[j]["exc"]]
        if amp > 0 and win:
            need.append(max(win) / (amp * S))
    if not need:
        return 100
    return int(max(need) * 100) + 1


def _sample(n: int) -> list[dict]:
    """n случайных векторов параметров. Семя фиксировано — набор воспроизводим."""
    rnd = random.Random(SEED)
    out, seen = [], set()
    guard = 0
    while len(out) < n and guard < 200 * n:
        guard += 1
        v = {
            "f_shift": rnd.randint(*RANGES["f_shift"]),
            "n_days": rnd.randint(*RANGES["n_days"]),
            "hold_min": rnd.randint(*RANGES["hold_min"]),
            # стоп по лог-шкале: 5-300 пунктов, мелкий конец разрешён плотнее
            "sl_beyond_pts": int(round(RANGES["sl_beyond_pts"][0] * (
                (RANGES["sl_beyond_pts"][1] / RANGES["sl_beyond_pts"][0]) ** rnd.random()))),
            "tp_arm_pts": rnd.randint(*RANGES["tp_arm_pts"]),
            "qty_first": rnd.randint(*RANGES["qty_first"]),
            "max_contracts": rnd.randint(*RANGES["max_contracts"]),
        }
        # откат ОБЯЗАН быть меньше активации, иначе тейк срабатывает в тот же бар,
        # что и включается. В сетке такие ячейки занимали около трети объёма.
        hi = min(RANGES["tp_back_pts"][1], v["tp_arm_pts"] - 1)
        if hi < RANGES["tp_back_pts"][0]:
            continue
        v["tp_back_pts"] = rnd.randint(RANGES["tp_back_pts"][0], hi)
        # бюджет обязан вмещать по контракту на ступень, иначе ступеней меньше
        if v["max_contracts"] < v["qty_first"] * STEP_COUNT:
            v["max_contracts"] = v["qty_first"] * STEP_COUNT
        key = tuple(sorted(v.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _run_random(args) -> None:
    vecs = _sample(args.random)
    print(f"семя {SEED} | векторов {len(vecs)} | прогонов "
          f"{len(vecs) * len(CONTRACTS) * 2}")
    # Статистика и d_coef — по одному разу на контракт.
    prep = {}
    for secid, d_from, d_to in CONTRACTS:
        prep[secid] = (_day_stats(secid, d_from, d_to), d_from, d_to, {})

    # ЧАСТЯМИ, А НЕ КОНТРАКТАМИ. Гейт сравнивает СОВПАДАЮЩИЕ векторы: одна и та же
    # строка нужна на обоих кварталах инструмента и при invert 0/1. Если идти
    # контракт за контрактом, первая такая пара сложится только к концу прогона.
    # При порядке «часть -> все контракты -> обе стороны» полный набор для сверки
    # готов уже после восьми заданий.
    jobs = []
    for ci in range(args.chunks):
        part = vecs[ci::args.chunks]
        for secid, _df, _dt in CONTRACTS:
            stats, d_from, d_to, cache = prep[secid]
            sets = []
            for v in part:
                k = (v["n_days"], v["f_shift"])
                if k not in cache:
                    cache[k] = _d_coef(stats, v["n_days"], STEP_COUNT, v["f_shift"] / 10.0)
                sets.append(dict(v, d_coef=cache[k]))
            for inv in (0, 1):
                side = "fade" if inv == 0 else "brk"
                jobs.append({
                    "campaign": f"rf7{side}{secid}p{ci}",
                    "scriptCode": CODE, "symbol": secid,
                    "baseParams": dict(PIN, symbol=secid, invert=inv),
                    "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                    "priority": 40,
                    "paramSets": sets,
                })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | прогонов {total} | в задании {len(jobs[0]['paramSets'])}")
    v0 = vecs[0]
    print("пример вектора: " + ", ".join(f"{k}={v0[k]}" for k in sorted(v0)))
    uniq = {k: len({v[k] for v in vecs}) for k in vecs[0]}
    print("различных значений по осям: " + ", ".join(f"{k}={n}" for k, n in sorted(uniq.items())))
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return
    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=300) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                ok += 1
            else:
                err += 1
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}, ошибок {err}, прогонов {total}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--random", type=int, default=0,
                    help="случайная выборка: сколько ВЕКТОРОВ (каждый идёт на 4 контракта × 2 стороны)")
    ap.add_argument("--chunks", type=int, default=20,
                    help="на сколько частей резать векторы: чем больше, тем раньше складывается первый совпадающий набор для гейта")
    args = ap.parse_args()

    if args.random:
        return _run_random(args)

    keys = list(AXES)
    combos = [dict(zip(keys, v)) for v in itertools.product(*AXES.values())]
    jobs, shown, dmaps = [], [], {}
    for secid, d_from, d_to in CONTRACTS:
        stats = _day_stats(secid, d_from, d_to)
        # d_coef зависит только от (n_days, F) — считаем один раз на контракт
        dmaps[secid] = {(n, f): _d_coef(stats, n, STEP_COUNT, f / 10.0)
                        for n in AXES["n_days"] for f in AXES["f_shift"]}
        shown.append((secid, dmaps[secid]))
    # hold -> контракт -> сторона: полный набор для гейта складывается после 8 заданий
    for hold in AXES["hold_min"]:
        for (secid, d_from, d_to), inv in itertools.product(CONTRACTS, AXES["invert"]):
            dmap = dmaps[secid]
            sets = []
            for c in combos:
                if c["invert"] != inv or c["hold_min"] != hold:
                    continue
                ps = {k: c[k] for k in keys if k not in CHUNK_KEYS}
                ps["d_coef"] = dmap[(c["n_days"], c["f_shift"])]
                sets.append(ps)
            side = "fade" if inv == 0 else "brk"
            jobs.append({
                "campaign": f"rf9{side}{secid}h{hold}",
                "scriptCode": CODE, "symbol": secid,
                "baseParams": dict(PIN, symbol=secid, invert=inv, hold_min=hold),
                "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                "priority": 40,
                "paramSets": sets,
            })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"контрактов {len(CONTRACTS)} | заданий {len(jobs)} | комбо {total}")
    print(f"в задании paramSets: {len(jobs[0]['paramSets'])}")
    print("\nвыведенный d_coef (недостижимость последней ступени, 20 ступеней):")
    for secid, dmap in shown:
        parts = [f"n={n} F={f // 10}: {dmap[(n, f)] / 100:.2f}"
                 for n in AXES["n_days"] for f in AXES["f_shift"]]
        print(f"  {secid}: " + ", ".join(parts))
    if not args.submit or args.dry_run:
        print("\nсухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=300) as cl:
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
