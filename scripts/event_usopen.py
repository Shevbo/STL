"""usopen на точках значимых событий: дни событий против обычных дней.

ЗАКАЗ ОПЕРАТОРА 26.09.2026: проверить стратегию usopen на календарных событиях
(ставка ЦБ, ставка ФРС, инфляция США, запасы нефти) по RI, BR, GD, Si за год.

КАК ЭТО МЕРИТСЯ ЧЕСТНО. Стратегия строит диапазон вокруг заданного времени и
торгует его пробой. Поэтому «проверить на событии» = поставить окно стратегии на
ВРЕМЯ СОБЫТИЯ. Но сам по себе плюс в этой точке ничего не доказывает: он может
быть свойством ВРЕМЕНИ СУТОК, а не новости. Поэтому один прогон идёт по ВСЕМУ
году в одно и то же время, а потом сделки делятся на два множества:
дни, когда событие было, и все остальные дни. Сравниваются они, а не абсолют.

ДВА ВРЕМЕНИ У СОБЫТИЙ США. Летнее время сдвигает выход данных на час, поэтому
каждое такое событие прогоняется дважды — своим временем для летнего и зимнего
отрезка, и каждый прогон засчитывает только свои дни.

ЕДИНИЦЫ. Непрерывные ряды (RI, BR, GD, Si) идут без множителя, поэтому итог
здесь в ПУНКТАХ инструмента, а не в рублях. Складывать инструменты между собой
нельзя.

    PYTHONPATH=. python scripts/event_usopen.py post      # поставить прогоны
    PYTHONPATH=. python scripts/event_usopen.py report    # разобрать результат
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import httpx

from scripts.event_calendar import events

CODE = "from trader.lab.strategies.us_open_fvg import on_bar"
RUNS_FILE = "/tmp/event_usopen_runs.json"

# Покрытие непрерывных рядов в agent_bars (проверено 26.09.2026). За его пределы
# выходить нельзя: движок молча отдаст пустой прогон.
COVER = {"RI": ("2025-09-26", "2026-09-10"),
         "Si": ("2025-09-26", "2026-09-10"),
         "GD": ("2025-09-26", "2026-07-31"),
         "BR": ("2025-12-05", "2026-06-01")}

# Живая спека agent-usopen-RIU6-v1, без подгонки под событие.
LIVE = {"qty": 1, "rr_x10": 36, "stop_pct": 3, "range_min": 6, "signal_min": 132,
        "min_frac": 22, "rej_frac": 57, "req_fvg": 1, "tp_trail": 0,
        "allow_long": 1, "allow_short": 1, "flatten_eod": 1, "entry_mode": 2,
        "invert": 0, "stop_slip": 0, "bar_offset_min": 0}


def plan() -> list[dict]:
    """Что прогоняем: инструмент, тип события, время МСК, отрезок года."""
    a, b = dt.date(2025, 9, 26), dt.date(2026, 9, 26)
    ev = events(a, b)
    out: list[dict] = []
    seen = set()
    for e in ev:
        for sym in e["symbols"]:
            key = (sym, e["kind"], e["hh"], e["mm"])
            if key in seen:
                continue
            seen.add(key)
            lo, hi = COVER[sym]
            out.append({"symbol": sym, "kind": e["kind"], "hh": e["hh"], "mm": e["mm"],
                        "date_from": lo, "date_to": hi})
    return out


def post() -> None:
    tok = os.environ["OPT_AGENT_TOKEN"]
    api = os.environ.get("STL_API", "http://localhost:8000")
    runs = []
    with httpx.Client(base_url=api, headers={"X-Agent-Token": tok}, timeout=120) as c:
        for p in plan():
            params = {**LIVE, "symbol": p["symbol"],
                      "open_hour": p["hh"], "open_min": p["mm"]}
            body = {"scriptCode": CODE, "baseParams": params, "paramSets": [{}],
                    "symbol": p["symbol"],
                    "dateFrom": f"{p['date_from']}T00:00:00",
                    "dateTo": f"{p['date_to']}T23:59:59",
                    "engine": "remote", "priority": 100}
            r = c.post("/api/v1/backtest/run", json=body)
            r.raise_for_status()
            rid = r.json().get("run_id") or r.json().get("id")
            runs.append({**p, "run_id": rid})
            print(f"{p['symbol']:3} {p['kind']:16} {p['hh']:02d}:{p['mm']:02d} -> {rid}")
    json.dump(runs, open(RUNS_FILE, "w"))
    print(f"\nпоставлено {len(runs)} прогонов, список в {RUNS_FILE}")


def _rounds(trades: list[dict]) -> list[tuple[dt.date, float]]:
    """Круги от флэта до флэта: дата входа, итог в пунктах, дата ВЫХОДА.

    ФАНТОМЫ СКЛЕЙКИ (найдено проверкой 26.09.2026, отравляло весь вывод).
    Непрерывные ряды RI/Si/GD не непрерывны: после каждой экспирации дыра в
    12-18 дней. В день экспирации бары кончаются в 18:49-18:58, флэт в 23:45 не
    срабатывает (такого бара нет), и позиция закрывается «страховкой» на первом
    баре СЛЕДУЮЩЕГО контракта. Итог такого круга равен скачку ряда, а не сделке:
    один круг RI 18.06 -> 01.07 дал −13 760 пунктов при разрыве ряда −13 690.
    Из-за него обычные дни RI в 16:30 выглядели как −12 710, а на самом деле
    +1 090. Поэтому возвращаем дату выхода, и вызывающая сторона обязана
    выбросить круги, где выход не в день входа.
    """
    out, pos, avg = [], 0, 0.0
    d0 = None
    for t in trades:
        q = t["qty"] * (1 if t["side"] == "buy" else -1)
        px = float(t["price"])
        if pos == 0:
            avg, pos = px, q
            d0 = dt.datetime.utcfromtimestamp(int(t["time"])).date()
        elif (pos > 0) == (q > 0):
            avg = (avg * abs(pos) + px * abs(q)) / (abs(pos) + abs(q))
            pos += q
        else:
            d1 = dt.datetime.utcfromtimestamp(int(t["time"])).date()
            out.append((d0, (px - avg) * (1 if pos > 0 else -1) * min(abs(pos), abs(q)), d1))
            pos += q
            if pos == 0:
                avg, d0 = 0.0, None
    return out


def report() -> None:
    import asyncio

    import asyncpg

    async def go():
        runs = json.load(open(RUNS_FILE))
        ev = events(dt.date(2025, 9, 26), dt.date(2026, 9, 26))
        c = await asyncpg.connect(os.environ["LAB_DB_URL"], command_timeout=600)
        print(f"{'инстр':5} {'событие':16} {'время':6} | "
              f"{'дни событий':>22} | {'обычные дни':>22}")
        for r in runs:
            row = await c.fetchrow(
                "SELECT trades FROM backtest_results WHERE run_id=$1", r["run_id"])
            if not row or not row["trades"]:
                st = await c.fetchval("SELECT status FROM backtest_runs WHERE id=$1",
                                      r["run_id"])
                print(f"{r['symbol']:5} {r['kind']:16} {r['hh']:02d}:{r['mm']:02d} | "
                      f"статус {st}, сделок нет")
                continue
            tr = row["trades"]
            tr = json.loads(tr) if isinstance(tr, str) else tr
            days = {e["date"] for e in ev
                    if e["kind"] == r["kind"] and e["hh"] == r["hh"]}
            on, off = [], []
            for d, pnl, d1 in _rounds(tr):
                if d1 != d:            # круг через границу дня = фантом склейки
                    continue
                if d.weekday() >= 5:   # выходная сессия: событий в выходные нет
                    continue
                (on if d in days else off).append(pnl)
            def fmt(v):
                if not v:
                    return f"{'нет сделок':>22}"
                pos = 100 * sum(x > 0 for x in v) / len(v)
                return f"{sum(v):+10.0f} пт  {len(v):>3} сд {pos:3.0f}%"
            print(f"{r['symbol']:5} {r['kind']:16} {r['hh']:02d}:{r['mm']:02d} | "
                  f"{fmt(on)} | {fmt(off)}")
        await c.close()

    asyncio.run(go())


if __name__ == "__main__":
    {"post": post, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
