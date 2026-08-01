"""Перебор параметров team-46 на i9 через очередь задач агента.

ЦЕЛЬ ПЕРЕБОРА (заказ оператора 01.08.2026): базовая конфигурация делает 217 сделок в
день при валовом эдже +0.105% за 3 месяца и комиссии 6.8% — комиссия больше эджа в 65
раз. Нужен режим НА ПОРЯДОК реже и НА ДВА ПОРЯДКА крупнее по эджу на сделку. Поэтому
оси делятся на две группы:
  * избирательность (реже входить): ofi_thr, shock_z, min_agreement, cooldown;
  * величина сделки (крупнее эдж): primary_hold, take_pct.

Комиссия ТЕЙКЕРСКАЯ: стратегия входит по рынку, мейкерская модель здесь была бы
самообманом (прошлые прогоны считали taker=False и потому выглядели «почти в нуле»).

Таблица ранжируется по НЕТТО, но главные колонки — эдж и комиссия НА СДЕЛКУ: именно
их отношение решает, жизнеспособна ли конфигурация, и оно не зависит от размера
позиции (и то и другое линейно по size_pct).

    OPT_AGENT_TOKEN=... PYTHONPATH=. python scripts/sweep_i9.py [--probe] [--id ...]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import time
from datetime import date, timedelta

import httpx

API = os.environ.get("STL_API", "https://stl.shectory.ru").rstrip("/")
TOKEN = os.environ.get("OPT_AGENT_TOKEN", "")
H = {"X-Agent-Token": TOKEN, "Content-Type": "application/json"}

# Ликвидные, с полным покрытием за 3 месяца, из РАЗНЫХ тарифных групп: индекс, валюта,
# товар, акция. BR/BM исключены — их кэш баров обрывается 01.07.
SYMBOLS = ["Si", "RI", "GD", "CR", "NG", "GZ"]

# Первое значение каждой оси = текущий живой дефолт, поэтому комбинация №0 это БАЗА.
GRID = {
    "ofi_thr":       [0.7, 1.5],       # порог аномалии потока заявок
    "shock_z":       [2.0, 3.5],       # порог ценового шока, сигм
    "min_agreement": [0.5, 0.8],       # согласие таймфреймов
    "cooldown":      [300.0, 3600.0],  # пауза между сигналами одного типа
    "primary_hold":  [900.0, 7200.0],  # удержание основной ноги, с
    "take_pct":      [0.025, 0.06],    # тейк
}
CFG = {"step": 900, "window_days": 2, "model_window": 240, "model_iter": 18,
       "refresh": 7200.0, "ofi_mode": "proxy", "taker": True}
DAYS = 95                              # то же окно, на котором измерена база


def _point_values() -> dict:
    for p in (os.path.join("data", "ai46_bt_3m", "point_values.json"),
              os.path.join("data", "ai46_bt", "point_values.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    print("WARN: point_values.json не найден — комиссия будет считаться при ₽/пункт=1")
    return {}


def _combos(probe: bool) -> list[dict]:
    if probe:                                   # калибровка: только база
        return [{k: v[0] for k, v in GRID.items()}]
    return [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]


def _enqueue(tid: str, args: list) -> None:
    r = httpx.post(f"{API}/api/v1/agent/task/enqueue", headers=H, timeout=60,
                   json={"id": tid, "module": "trader.lab.ai46.sweep_task",
                         "func": "run_combo", "args": args})
    print("enqueue ->", r.status_code, r.text[:200])
    r.raise_for_status()


def _poll(tid: str, minutes: int) -> dict:
    last, t0 = None, time.time()
    g: dict = {}
    while time.time() - t0 < minutes * 60:
        time.sleep(15)
        g = httpx.get(f"{API}/api/v1/agent/task/{tid}", headers=H, timeout=30).json()
        if g["status"] != last:
            print(f"  [{time.time() - t0:>5.0f}s] status={g['status']} "
                  f"agent={g.get('agent_id')} claimed={g.get('claimed_at')}")
            last = g["status"]
        if g["status"] in ("done", "failed"):
            break
    return g


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true",
                   help="только базовая комбинация — замерить скорость i9 перед полным перебором")
    p.add_argument("--id", default="", help="id задачи (по умолчанию по дате)")
    p.add_argument("--minutes", type=int, default=90, help="сколько ждать результат")
    args_cli = p.parse_args()
    if not TOKEN:
        raise SystemExit("set OPT_AGENT_TOKEN")

    pvs = _point_values()
    today = date.today()
    d_from, d_to = str(today - timedelta(days=DAYS)), str(today)
    combos = _combos(args_cli.probe)
    units = [{"key": s, "fields": c, "date_from": d_from, "date_to": d_to,
              "point_value": pvs.get(s, 1.0), "cfg": CFG}
             for c in combos for s in SYMBOLS]
    tid = args_cli.id or f"ai46-{'probe' if args_cli.probe else 'sweep'}-{d_to.replace('-', '')}"
    print(f"{tid}: {len(combos)} комбинаций × {len(SYMBOLS)} инстр. = {len(units)} юнитов, "
          f"окно {d_from}..{d_to}, комиссия {'тейкер' if CFG['taker'] else 'мейкер'}")

    t0 = time.time()
    _enqueue(tid, units)
    g = _poll(tid, args_cli.minutes)
    wall = time.time() - t0
    if g.get("status") != "done":
        print("НЕ ЗАВЕРШЕНО:", g.get("error") or g.get("status"))
        return

    rows = g.get("result") or []
    agg = {i: {"net": 0.0, "gross": 0.0, "fees": 0.0, "trades": 0, "n": 0, "err": 0}
           for i in range(len(combos))}
    for row in rows:
        i = next((j for j, c in enumerate(combos) if c == row.get("combo")), None)
        if i is None:
            continue
        a = agg[i]
        if row.get("error"):
            a["err"] += 1
            continue
        a["net"] += row.get("net", 0.0)
        a["gross"] += row.get("gross", 0.0)
        a["fees"] += row.get("fees", 0.0)
        a["trades"] += row.get("trades", 0)
        a["n"] += 1

    ranked = sorted(range(len(combos)), key=lambda i: agg[i]["net"], reverse=True)
    base_i = next((i for i, c in enumerate(combos) if c == {k: v[0] for k, v in GRID.items()}), None)
    base_tr = agg[base_i]["trades"] if base_i is not None else 0
    base_edge = (agg[base_i]["gross"] / agg[base_i]["trades"]) if base_tr else 0.0

    out = []
    for i in ranked:
        a = agg[i]
        edge = a["gross"] / a["trades"] if a["trades"] else 0.0
        fee = a["fees"] / a["trades"] if a["trades"] else 0.0
        out.append({"combo": combos[i], "net": round(a["net"], 6), "gross": round(a["gross"], 6),
                    "fees": round(a["fees"], 6), "trades": a["trades"], "symbols": a["n"],
                    "err": a["err"], "edge_per_trade_ppm": round(edge * 1e6, 4),
                    "fee_per_trade_ppm": round(fee * 1e6, 4),
                    "fee_over_edge": round(fee / edge, 1) if edge > 0 else None})
    path = os.path.join("data", "ai46_bt_3m", f"{tid}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"grid": GRID, "symbols": SYMBOLS, "cfg": CFG, "days": DAYS,
                   "wall_secs": round(wall, 1), "ranked": out}, f, ensure_ascii=False, indent=2)

    print(f"\n=== ТАБЛИЦА по НЕТТО (тейкер, {len(SYMBOLS)} инстр., {DAYS} дней, {wall / 60:.0f} мин) ===")
    print(f"{'#':>3} {'НЕТТО':>9} {'валовый':>9} {'комис':>8} {'сделок':>7} {'×реже':>6} "
          f"{'эдж/сд':>8} {'ком/сд':>8} {'ком/эдж':>8}  параметры")
    for rank, i in enumerate(ranked):
        a = agg[i]
        edge = a["gross"] / a["trades"] if a["trades"] else 0.0
        fee = a["fees"] / a["trades"] if a["trades"] else 0.0
        rarer = (base_tr / a["trades"]) if a["trades"] else 0.0
        ratio = f"{fee / edge:.0f}x" if edge > 0 else "—"
        tag = " (БАЗА)" if i == base_i else ""
        diff = {k: v for k, v in combos[i].items() if base_i is None or v != combos[base_i][k]}
        print(f"{rank + 1:>3} {a['net'] * 100:>+8.3f}% {a['gross'] * 100:>+8.3f}% "
              f"{a['fees'] * 100:>7.3f}% {a['trades']:>7} {rarer:>5.1f}x "
              f"{edge * 1e6:>+7.3f} {fee * 1e6:>7.3f} {ratio:>8}  {diff or 'база'}{tag}")
    if base_edge:
        best = ranked[0]
        be = agg[best]["gross"] / agg[best]["trades"] if agg[best]["trades"] else 0.0
        print(f"\nЛучшая против базы: эдж на сделку ×{be / base_edge:.1f}, "
              f"сделок ×{agg[best]['trades'] / base_tr:.2f}")
    print(f"\n{path}")


if __name__ == "__main__":
    main()
