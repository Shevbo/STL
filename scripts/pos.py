#!/usr/bin/env python3
"""Что ПРЯМО СЕЙЧАС в рынке: позиции, роботы, живые умные заявки, сделки.

    python3 scripts/pos.py                    снимок (data/truth.json)
    python3 scripts/pos.py --trades           сделки сегодня из журнала
    python3 scripts/pos.py --trades 2026-09-22   сделки за день

Снимок пишет STL раз в две секунды (trader/quik/truth.py). Первая строка вывода
всегда про ВОЗРАСТ данных: старше десяти секунд — печатается СТАРО, и тогда
единственный честный ответ про позиции «не знаю», а не то, что лежит в файле.
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUTH = os.path.join(ROOT, "data", "truth.json")
TRADES = os.path.join(ROOT, "data", "trades")
STALE_MS = 10_000


def hhmm(ms):
    return time.strftime("%d.%m %H:%M:%S", time.localtime((ms or 0) / 1000)) if ms else "-"


def show_trades(day: str | None) -> int:
    day = day or time.strftime("%Y-%m-%d", time.localtime())
    path = os.path.join(TRADES, f"{day}.jsonl")
    if not os.path.exists(path):
        print(f"журнала сделок за {day} нет: {path}")
        return 1
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    print(f"сделки {day}: {len(rows)}")
    net = {}
    for r in rows:
        sign = 1 if str(r.get("side", "")).upper().startswith("B") else -1
        key = (r.get("sec"), r.get("owner"))
        net[key] = net.get(key, 0) + sign * int(r.get("qty") or 0)
        print("  %s %-6s %-4s %3d @ %-9s %-7s %s" % (
            hhmm(r.get("ts_ms")), r.get("sec"), r.get("side"), r.get("qty"),
            r.get("price"), r.get("owner"), r.get("tag") or ""))
    print("итог по сделкам (контракты):")
    for (sec, own), n in sorted(net.items()):
        print(f"  {sec} {own}: {n:+d}")
    return 0


def main() -> int:
    if "--trades" in sys.argv:
        i = sys.argv.index("--trades")
        return show_trades(sys.argv[i + 1] if len(sys.argv) > i + 1 else None)
    if not os.path.exists(TRUTH):
        print("СНИМКА НЕТ: data/truth.json не создан — STL не перезапускался после "
              "выкладки сторожа, или задача упала. Позиции НЕИЗВЕСТНЫ.")
        return 2
    with open(TRUTH, encoding="utf-8") as fh:
        t = json.load(fh)
    age = int(t.get("age_ms") or -1)
    file_age = int(time.time() * 1000) - int(os.path.getmtime(TRUTH) * 1000)
    why = t.get("stale_why") or ""
    if file_age > STALE_MS:
        why = why or f"STL не обновляет снимок {file_age/1000:.0f} с (сторож упал?)"
    verdict = "СТАРО" if why else "СВЕЖО"
    print(f"{verdict}: линк {int(t.get('link_age_ms') or -1)/1000:.1f} с, "
          f"зеркало {age/1000:.1f} с, файл {file_age/1000:.1f} с "
          f"(снят {hhmm(t.get('ts_ms'))})")
    if why:
        print(f"  {why} — про позиции честный ответ «не знаю», проверяй QUIK")
    print("позиции счёта:")
    for p in t.get("positions") or []:
        print("  %-6s net %+d (роботы %+d, рука %+d) avg %s ВМ %s" % (
            p.get("sec"), p.get("net"), p.get("robots"), p.get("manual"),
            p.get("avg"), p.get("varmargin")))
    if not t.get("positions"):
        print("  пусто")
    live = [r for r in t.get("robots") or [] if r.get("position")]
    print(f"роботы в позиции: {len(live)} из {len(t.get('robots') or [])}")
    for r in live:
        print("  %-28s %-6s %-5s %+d @ %s%s" % (
            r.get("id"), r.get("symbol"), r.get("mode"), r.get("position"),
            r.get("avg_price"), " ПАУЗА" if r.get("paused") else ""))
    so = t.get("smart_orders") or []
    print(f"умные заявки живые: {len(so)}")
    for o in so:
        print("  %s %-8s %-4s %3d %-6s %-7s уровень %s откат %s" % (
            o.get("so_id"), o.get("kind"), o.get("side"), o.get("qty"), o.get("code"),
            o.get("status"), o.get("level"), o.get("trail_offset")))
    tt = t.get("trades_today") or {}
    print(f"сделок сегодня: {tt.get('count')} {tt.get('by_owner') or {}}")
    for r in (t.get("last_trades") or [])[-5:]:
        print("  %s %-6s %-4s %3d @ %-9s %s" % (
            hhmm(r.get("ts_ms")), r.get("sec"), r.get("side"), r.get("qty"),
            r.get("price"), r.get("owner")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
