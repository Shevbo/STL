"""Перебор impulse_fade под спецификацию оператора 12.09.2026.

ЧТО ЗАМЕНИЛ. `queue_night_impulse{,2,3}.py` (волны 10-11.09) удалены вместе с этим
коммитом: они гоняли оси `imp_atr` / `take_atr` / `mean_n`-как-цель, которых в
стратегии больше нет. Запуск такого файла по новому коду дал бы самый опасный вид
результата — не ошибку, а ОДНО И ТО ЖЕ число во всех ячейках оси (несуществующий
параметр молча берёт default). Эту ловушку уже ловили на i9 16.08.2026.
Те же волны стоит считать недействительными и в лидерборде: механизм входа другой.

СИММЕТРИЧНО: кампании imf1/imf2/imf3, если какие-то ещё висят в очереди i9, надо
снять — они посчитают новый механизм под именем старого.

ВОЛНА imf4 (12.09, 6912 прогонов) НЕДЕЙСТВИТЕЛЬНА ПО ТОЙ ЖЕ ПРИЧИНЕ, но на другом
уровне: i9 держит СВОЮ копию кода и тянет её только при смене update_token, а его
никто не бампил. 864 строки на задание дали ровно 12 различных результатов —
mean_n × max_hold × stop_atr, то есть работали только те три оси, что существуют
в прежней версии стратегии. Перед КАЖДЫМ запуском: бампнуть update_token и дождаться
непустого code_sha в i9_heartbeat, совпадающего с отпечатком репозитория.

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

# СЕТКА ПОСЛЕ ДВУХ ПРОБ 12.09 (scripts/imf_probe.py, 4 контракта).
#
# Дистанция задана ТОЛЬКО в долях средней дневной свечи (lvl_amp): версия в ATR уже
# промерена и на частоте, достаточной для статистики, убыточна у фейда на всех
# четырёх контрактах. Две оси дистанции сразу дали бы дубли строк — при lvl_amp>0
# параметр lvl_atr не читается вовсе, и половина сетки повторяла бы другую.
#
# slip_atr ЗАКРЕПЛЁН, а не перебирается: проскальзывание — это издержка, как
# комиссия, а не выбор стратегии. Ставим 0.25 ATR. Оно бьёт ТОЛЬКО вход по проколу
# (invert=1) и стоп-лосс: лимитник против прокола не проскальзывает. Без этой
# поправки пробой показывал win 0.85-0.92 и был чистым артефактом исполнения.
AXES = {
    "lvl_amp": [15, 25, 40],             # доля средней дневной свечи
    "mean_n": [60, 480, 1440],           # якорь: час, смена, сутки
    "step_count": [1, 3],
    "imp_frac": [0, 50],
    "ret_pct": [50, 67, 100],
    "max_hold": [60, 240],
    "flat_only": [0, 1],
    "stop_atr": [50, 100],
}
# Закреплено: ATR длинный, дневной размах за 5 дней, окно режима ~10 торговых
# суток, пауза 5 минут. lvl_atr инертен, пока lvl_amp>0 — оставлен для читаемости.
PIN = dict(qty=1, lvl_atr=60, amp_days=5, atr_n=200, step_atr=20, imp_bars=5,
           slip_atr=25, cooldown=5, reg_win=14400, reg_drift=300,
           allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--symbols", default="", help="только эти контракты через запятую")
    # priority DESC: чем больше число, тем раньше считается. 10 — заведомо
    # позади текущей кампании rich_fool, перебивать её незачем.
    ap.add_argument("--priority", type=int, default=10)
    # Имя волны в названии кампании. Меняется при КАЖДОМ перезапуске: строки imf4
    # посчитаны устаревшим кодом на i9 (жили только 3 оси из 8), и смешать их с
    # честными в одном имени значило бы похоронить разницу.
    ap.add_argument("--tag", default="imf5")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, v)) for v in itertools.product(*AXES.values())]
    picked = [c for c in CONTRACTS
              if not args.symbols or c[0] in args.symbols.split(",")]

    jobs = []
    for sym, d_from, d_to in picked:
        for inv in (0, 1):
            jobs.append({
                "campaign": f"{args.tag}-{sym.lower()}-i{inv}",
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
