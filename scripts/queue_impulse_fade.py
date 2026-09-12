"""Перебор impulse_fade под спецификацию оператора 12.09.2026.

ЧТО ЗАМЕНИЛ. `queue_night_impulse{,2,3}.py` (волны 10-11.09) удалены вместе с этим
коммитом: они гоняли оси `imp_atr` / `take_atr` / `mean_n`-как-цель, которых в
стратегии больше нет. Запуск такого файла по новому коду дал бы самый опасный вид
результата — не ошибку, а ОДНО И ТО ЖЕ число во всех ячейках оси (несуществующий
параметр молча берёт default). Эту ловушку уже ловили на i9 16.08.2026.
Те же волны стоит считать недействительными и в лидерборде: механизм входа другой.

СИММЕТРИЧНО: кампании imf1/imf2/imf3, если какие-то ещё висят в очереди i9, надо
снять — они посчитают новый механизм под именем старого.

ПО КОНТРАКТАМ, НЕ ПО СШИТОЙ СЕРИИ. Прежние волны гонялись по базовым кодам
RI/Si/GZ — это непрерывная серия, сшитая через перекат без выравнивания базиса
(шов 01.07.2026 у RI: −13.01%). Позиция, пережившая шов, даёт фантом. Здесь две
пары контрактов одного инструмента: пара и есть проверка воспроизводимости —
настройка, живущая только на одном квартале, кандидатом не является.

ОСИ — ровно вопросы постановки, не больше:
  lvl_atr    как далеко от цены висит заявка (в ATR). Главная ось: она задаёт,
             что считать проколом, и от неё зависит частота.
  step_count одна заявка или лестница вглубь прокола.
  imp_frac   нужен ли гейт скорости (0 = выключен) — отличает рывок от доползания.
  ret_pct    насколько возвращаемся: 1/2, 2/3 или полностью к якорю.
  max_hold   1-240 минут по постановке.
  flat_only  гейт боковика: он и есть проверка «не спутать прокол с трендом».
  stop_atr   запас за последней ступенью.

invert=0/1 идёт ОТДЕЛЬНЫМИ заданиями: зеркальный гейт обязателен, фейд должен
бить прямой пробой на ТЕХ ЖЕ параметрах, иначе доказана сторона, а не механизм.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_impulse_fade.py --dry-run
    PYTHONPATH=. $PY scripts/queue_impulse_fade.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
CODE = "from trader.lab.strategies.impulse_fade import on_bar, on_start, on_stop"

# Контракт и его ликвидный квартал. Пара M6/U6 одного инструмента = гейт
# воспроизводимости (см. rf_gate.py, тот же принцип).
CONTRACTS = [
    ("RIM6", "2026-03-20", "2026-06-17"),
    ("RIU6", "2026-06-19", "2026-09-10"),
    ("SiM6", "2026-03-20", "2026-06-17"),
    ("SiU6", "2026-06-19", "2026-09-10"),
]

# Сетка сдвинута по пробе 12.09 (scripts/imf_probe.py, 4 контракта):
#   lvl_atr=200 выброшен — 2-6 сделок за квартал, это не статистика;
#   stop_atr расширен до 100 — на узком стопе фейд НЕ доживает до отката, и весь
#   диапазон [20,50] оказался не той областью (win 0.20-0.56 против 0.63-0.79).
AXES = {
    "lvl_atr": [40, 60, 90, 140],        # 4..14 ATR от якоря
    "step_count": [1, 3],
    "imp_frac": [0, 50, 80],
    "ret_pct": [50, 67, 100],
    "max_hold": [30, 120, 240],
    "flat_only": [0, 1],
    "stop_atr": [20, 50, 100],
}
# Закреплено: якорь часовой, ATR длинный (одиночный прокол не имеет права раздуть
# дистанцию), окно режима ~10 торговых суток, пауза 5 минут.
PIN = dict(qty=1, mean_n=60, atr_n=200, step_atr=20, imp_bars=5,
           cooldown=5, reg_win=14400, reg_drift=300,
           allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--symbols", default="", help="только эти контракты через запятую")
    ap.add_argument("--priority", type=int, default=15)
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, v)) for v in itertools.product(*AXES.values())]
    picked = [c for c in CONTRACTS
              if not args.symbols or c[0] in args.symbols.split(",")]

    jobs = []
    for sym, d_from, d_to in picked:
        for inv in (0, 1):
            jobs.append({
                "campaign": f"imf4-{sym.lower()}-i{inv}",
                "scriptCode": CODE, "symbol": sym,
                "baseParams": dict(PIN, symbol=sym, invert=inv),
                "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                "priority": args.priority,
                "paramSets": combos,
            })

    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | комбо {total}")
    for j in jobs:
        print(f"  {j['campaign']:18s} {j['dateFrom']}..{j['dateTo']}  {len(j['paramSets'])} шт")
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
