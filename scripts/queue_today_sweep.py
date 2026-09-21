"""Перебор ВСЕХ осей живого робота на СЕГОДНЯШНЕМ дне, исполнение по стакану.

Запрос оператора 21.09.2026: найти топ наборов параметров, которые отторговали бы
сегодняшний день по максимуму. Это заведомо ПОДГОНКА на одном дне — она и нужна:
оператор хочет видеть, что на этом дне вообще было возможно и какими параметрами.
Никаких выводов о будущем из этой таблицы делать нельзя.

Окно: бары 19-21.09 (из 1199 баров сегодня 767), прогрев съедает 19-20.09 —
то есть торгуется практически только сегодня. Исполнение по архивному стакану
(ключ выжимки передаётся в baseParams как book_key).

Считает ТОЛЬКО i9.

    STL_API=http://localhost:8000 python scripts/queue_today_sweep.py \
        --symbol spRIZ6d0921 --book-key bookRIZ6d0921 --campaign day0921 --draws 3000
"""
from __future__ import annotations

import argparse
import os
import random
import sys

import httpx

# Живая спека lxk22 — то, от чего отсчитываем. Всё, что не в сетке, остаётся боевым.
# ВНИМАНИЕ: fast/slow/signal и avg_atr_n ПЕРЕБИРАЮТСЯ (см. AXES), здесь они только
# как значения по умолчанию для полноты словаря.
LIVE = {"qty": 1, "fast": 57, "slow": 48, "signal": 10, "avg_atr_n": 25,
        "cooldown_min": 0, "cooldown_pct": 1, "nd_days": 5, "gap_auto": 0,
        "sl_frac": 0, "dv_bars": 60, "bet_max": 10, "super_y": 2, "super_z": 2,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1,
        "bar_offset_min": 0, "flatten_end": 1}

# ОСИ СТРОЯТСЯ ИЗ САМОЙ СХЕМЫ СТРАТЕГИИ — чтобы ни один параметр не остался за
# бортом по моему усмотрению (замечание оператора 21.09: «я сказал ВСЕ ПАРАМЕТРЫ»).
# Схема macd_shectory1 — 44 ключа; text-параметр symbol и служебные
# bar_offset_min/flatten_end остаются фиксированными: это не настройки робота, а
# конвенции данных и замера.
FIXED = {"symbol", "bar_offset_min", "flatten_end"}
# Границы ШИРЕ схемных там, где схема ограничивает UI, а движок принимает больше.
WIDEN = {"fast": (4, 120), "slow": (4, 120), "avg_max": (1, 30), "tp_atr": (5, 200),
         "sl_pct": (10, 400), "sl_frac": (0, 200), "k_avg": (10, 50),
         "flip_min_pts": (0, 5000), "cooldown_min": (0, 480), "reg_n": (0, 2000),
         "dv_range_pts": (0, 1500), "min_gap_pts": (0, 1500), "spread_pts": (0, 30)}
STEPS = 24                      # столько значений на ось, кроме флагов и мелких


def build_axes() -> dict:
    from trader.lab.strategies.library import REGISTRY
    axes: dict[str, list] = {}
    for spec in REGISTRY["macd_shectory1"]["params_schema"]:
        k = spec["key"]
        if k in FIXED or spec.get("type") != "number":
            continue
        lo, hi = WIDEN.get(k, (int(spec["min"]), int(spec["max"])))
        if hi - lo <= STEPS:
            axes[k] = list(range(lo, hi + 1))
        else:
            step = max(1, (hi - lo) // STEPS)
            axes[k] = list(range(lo, hi + 1, step))
    # Ставочная эскалация живого робота: в схеме её нет, но код её читает.
    axes["super_y"] = list(range(0, 6))
    axes["super_z"] = list(range(0, 6))
    axes["bet_step"] = list(range(0, 6))
    axes["bet_max"] = list(range(0, 31, 3))
    return axes


AXES = build_axes()


def draw(rng: random.Random) -> dict:
    p = {k: rng.choice(v) for k, v in AXES.items()}
    # fast == slow — вырождение: EMA(n) − EMA(n) тождественно ноль, сигнала нет
    # вовсе (sig_macd возвращает None). Такие наборы в сетку не пускаем.
    if p.get("fast") == p.get("slow"):
        p["slow"] = p["slow"] + 2
    # Обе стороны запрещены = робот вообще не торгует: бессмысленный набор.
    if not p.get("allow_long") and not p.get("allow_short"):
        p["allow_long"] = 1
    # Запреты удержания убытка армятся только со стопом (инвариант движка).
    if not p.get("sl_pct") and not p.get("sl_frac"):
        p["sl_pct"] = 100
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--book-key")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--draws", type=int, default=3000)
    ap.add_argument("--chunk", type=int, default=5000,
                    help="наборов в одном задании: тело запроса не резиновое")
    ap.add_argument("--date-from", default="2026-09-19")
    ap.add_argument("--date-to", default="2026-09-21")
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    a = ap.parse_args()

    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        sys.exit("нет OPT_AGENT_TOKEN")
    rng = random.Random(20260921)
    seen, sets = set(), []
    while len(sets) < a.draws:
        p = draw(rng)
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)
        sets.append(p)

    base = {**LIVE, "symbol": a.symbol}
    if a.book_key:
        base["book_key"] = a.book_key
    code = ("from trader.lab.strategies.library import make_on_bar\n"
            "on_bar = make_on_bar('macd_shectory1')")
    h = {"X-Agent-Token": tok, "Content-Type": "application/json"}
    # Кусками: тело запроса не резиновое (nginx рубит большие POST), и одно
    # задание на сотни тысяч наборов заняло бы один claim на часы, не отдавая
    # результат в хит-парад по ходу дела.
    chunks = [sets[i:i + a.chunk] for i in range(0, len(sets), a.chunk)]
    ok = 0
    with httpx.Client(base_url=a.api, headers=h, timeout=300) as c:
        for i, part in enumerate(chunks):
            body = {"scriptCode": code, "baseParams": base, "paramSets": part,
                    "symbol": a.symbol, "dateFrom": f"{a.date_from}T00:00:00",
                    "dateTo": f"{a.date_to}T23:59:59", "engine": "remote",
                    # номер куска в имени: id прогона = кампания+стратегия+символ,
                    # одинаковые имена теряли бы задания на duplicate key
                    "campaign": f"{a.campaign}{i:03d}"}
            try:
                c.post("/api/v1/backtest/run", json=body).raise_for_status()
                ok += len(part)
            except Exception as exc:  # noqa: BLE001
                print(f"  кусок {i} не встал: {exc}")
    print(f"поставлено {ok} наборов в {len(chunks)} заданиях, кампания {a.campaign}*, "
          f"символ {a.symbol}, стакан {a.book_key or 'нет'}")


if __name__ == "__main__":
    main()
