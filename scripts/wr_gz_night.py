#!/usr/bin/env python3
"""Ночной перебор Williams %R на GZU6 с проверкой на ТРЁХ независимых окнах.

Зачем. Живой бумажный робот (period=38, oversold=67, overbought=39) сделал
+2 715 ₽ за 15.06-02.08, а перебор ТЕХ ЖЕ параметров на 01.01-31.07 дал
-21 646 ₽. Одно из двух: либо набор подогнан под последние полтора месяца,
либо на газе есть режим, в котором эта логика работает. Отличить можно только
вне окна подгонки — поэтому здесь не «лучший результат», а «в плюсе на ВСЕХ
окнах». На одном окне выигрывает и мусор (см. OB BRU6: лидер кампании был
убыточен у 6 из 6 соседей за пределами окна подгонки).

Что делает:
  1. Грубая сетка period × oversold × overbought × tp_atr × sl_pct на ТРЁХ
     окнах: янв-мар, апр-май, июн-авг (последнее = период живого робота).
  2. Уточняющая сетка вокруг выживших — снова на всех трёх окнах.
  3. Отчёт: кто выжил, устойчива ли ОКРЕСТНОСТЬ (соседи по сетке — одиночная
     точка на плато из убытков это шум), и что на этих же окнах показывают
     ТЕКУЩИЕ параметры живого робота.

sl_pct (стоп процентом от цены входа) перебирается вместо sl_frac намеренно:
sl_frac — доля дистанции тейка, при tp_atr=0 он не работает вовсе.

Считает i9 (перебор НИКОГДА не на хостере). Скрипт только ставит задания в
очередь, ждёт и сводит результаты.

Запуск на хостере:
  cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
  nohup $(poetry env info --path)/bin/python scripts/wr_gz_night.py \
        > ~/wr_gz_night.log 2>&1 &
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time
from datetime import datetime

import httpx

API = os.environ.get("STL_API_LOCAL", "http://localhost:8000")
EMAIL = os.environ.get("STL_REPORT_EMAIL", "bshevelev75@gmail.com")
SYMBOL = "GZU6"
STRAT = "williams_r"
SCRIPT_CODE = ("from trader.lab.strategies.library import make_on_bar; "
               "on_bar = make_on_bar('williams_r')")
# Приоритет 50: выше фоновых кампаний оптимизатора (0), ниже прогона, за которым
# прямо сейчас сидит человек у экрана (100).
PRIORITY = 50
OUT_JSON = os.path.expanduser("~/wr_gz_night.json")
OUT_MD = os.path.expanduser("~/wr_gz_night_report.md")
DEADLINE_SEC = 7 * 3600          # общий предохранитель: под утро всё равно закончить
JOB_TIMEOUT_SEC = 90 * 60        # одно задание дольше полутора часов = что-то не так
CHUNK = 2600                     # комбинаций в одном задании (payload результата ~МБ)

# Три НЕЗАВИСИМЫХ окна. Третье — то, на котором живой робот показал плюс.
WINDOWS = [
    ("w1", "2026-01-01", "2026-03-31"),
    ("w2", "2026-04-01", "2026-05-31"),
    ("w3", "2026-06-01", "2026-08-02"),
]

# Параметры живого робота — базовая линия, с которой сравниваем всё остальное.
LIVE = {"period": 38, "oversold": 67, "overbought": 39, "tp_atr": 0, "sl_pct": 0}

AXES_COARSE = {
    "period": [8, 12, 16, 20, 24, 28, 32, 36, 40],
    "oversold": [62, 68, 74, 80, 86, 92],
    "overbought": [8, 14, 20, 26, 32, 38],
    "tp_atr": [0, 10, 20, 30],
    "sl_pct": [0, 20, 45, 75],
}
KEYS = list(AXES_COARSE)
# Границы схемы стратегии: за них выходить нельзя, движок примет, но это уже не
# та стратегия (period>40 в схеме запрещён, oversold/overbought тоже).
BOUNDS = {"period": (5, 40), "oversold": (60, 95), "overbought": (5, 40),
          "tp_atr": (0, 60), "sl_pct": (0, 200)}
FIXED = {"qty": 1, "avg_max": 1, "avg_step_atr": 0, "sl_frac": 0,
         "avg_atr_n": 14, "min_gap_pts": 0}
MIN_TRADES = 30                  # меньше — статистики нет, любой плюс случаен
REFINE_MAX = 4200                # потолок уточняющей сетки


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def token() -> str:
    from trader.auth.portal import make_session_token
    secret = os.environ["SHECTORY_AUTH_BRIDGE_SECRET"]
    return make_session_token(EMAIL, secret)


def combos(axes: dict) -> list[dict]:
    return [dict(zip(axes, v)) for v in itertools.product(*axes.values())]


def full(ps: dict) -> dict:
    return {**FIXED, **ps, "symbol": SYMBOL}


def key_of(ps: dict) -> tuple:
    return tuple(ps[k] for k in KEYS)


def submit(client: httpx.Client, tag: str, d_from: str, d_to: str,
           param_sets: list[dict]) -> str:
    body = {
        "symbol": SYMBOL,
        "dateFrom": f"{d_from}T00:00:00Z",
        "dateTo": f"{d_to}T23:59:59Z",
        "paramSets": [full(p) for p in param_sets],
        "engine": "remote",
        "scriptCode": SCRIPT_CODE,
        "baseParams": {"symbol": SYMBOL},
        "priority": PRIORITY,
        "campaign": f"wrgznight{tag}",
    }
    r = client.post("/api/v1/backtest/run", json=body, timeout=180)
    r.raise_for_status()
    run_id = r.json()["run_id"]
    log(f"  задание {tag}: {len(param_sets)} комбинаций -> {run_id}")
    return run_id


def wait(client: httpx.Client, run_id: str) -> list[dict]:
    """Дождаться задания и забрать метрики. Сетевой сбой не роняет ночь."""
    t0 = time.time()
    last = ""
    while time.time() - t0 < JOB_TIMEOUT_SEC:
        try:
            st = client.get(f"/api/v1/backtest/{run_id}/status", timeout=60).json()
        except Exception as exc:  # noqa: BLE001
            log(f"  опрос статуса не удался ({exc}) — повторю")
            time.sleep(20)
            continue
        s = st.get("status")
        if s != last:
            log(f"  {run_id[:34]}: {s}")
            last = s
        if s == "failed":
            log(f"  ОШИБКА движка: {st.get('error_msg')}")
            return []
        if s == "done":
            for _ in range(5):
                try:
                    rows = client.get(f"/api/v1/backtest/{run_id}/results",
                                      timeout=300).json()
                    log(f"  {run_id[:34]}: получено {len(rows)} результатов "
                        f"за {int(time.time() - t0)} с")
                    return rows
                except Exception as exc:  # noqa: BLE001
                    log(f"  выгрузка результатов не удалась ({exc}) — повторю")
                    time.sleep(15)
            return []
        time.sleep(15)
    log(f"  {run_id}: таймаут ожидания")
    return []


def run_window(client: httpx.Client, phase: str, wname: str, d_from: str, d_to: str,
               param_sets: list[dict]) -> dict[tuple, dict]:
    """Прогнать набор на одном окне, вернуть {ключ: метрики}."""
    out: dict[tuple, dict] = {}
    for i in range(0, len(param_sets), CHUNK):
        part = param_sets[i:i + CHUNK]
        rid = submit(client, f"{phase}{wname}c{i // CHUNK}", d_from, d_to, part)
        for row in wait(client, rid):
            p = row.get("params") or {}
            try:
                k = tuple(p[kk] for kk in KEYS)
            except KeyError:
                continue
            out[k] = {"net": row.get("net_profit"), "rf": row.get("recovery_factor"),
                      "trades": row.get("total_trades"), "ann": row.get("ann_return_go"),
                      "dd": row.get("max_drawdown")}
    return out


def survivors(byw: dict[str, dict[tuple, dict]]) -> list[tuple]:
    """Ключи, которые в плюсе на ВСЕХ окнах и торговали достаточно часто."""
    if not byw:
        return []
    common = set.intersection(*(set(v) for v in byw.values()))
    ok = []
    for k in common:
        rows = [byw[w][k] for w in byw]
        if all((r.get("net") or 0) > 0 for r in rows) and \
           all((r.get("trades") or 0) >= MIN_TRADES for r in rows):
            ok.append(k)
    return sorted(ok, key=lambda k: min((byw[w][k].get("net") or 0) for w in byw),
                  reverse=True)


def neighbourhood(k: tuple, axes: dict) -> list[tuple]:
    """Соседи по сетке: ±1 шаг по каждой оси. Одиночная точка среди убытков —
    это шум подгонки, а не находка; плато — уже разговор."""
    out = []
    for ai, name in enumerate(KEYS):
        vals = axes[name]
        try:
            idx = vals.index(k[ai])
        except ValueError:
            continue
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(vals):
                n = list(k)
                n[ai] = vals[j]
                out.append(tuple(n))
    return out


def refine_axes(keys: list[tuple]) -> dict:
    """Уточняющая сетка: вокруг выживших, шаг вдвое мельче, в границах схемы."""
    steps = {"period": 2, "oversold": 3, "overbought": 3, "tp_atr": 5, "sl_pct": 10}
    axes: dict[str, set] = {k: set() for k in KEYS}
    for k in keys:
        for ai, name in enumerate(KEYS):
            lo, hi = BOUNDS[name]
            for d in (-1, 0, 1):
                v = k[ai] + d * steps[name]
                if lo <= v <= hi:
                    axes[name].add(v)
    return {k: sorted(v) for k, v in axes.items()}


def fmt(v, nd=0, dash="—"):
    if v is None:
        return dash
    return f"{v:,.{nd}f}".replace(",", " ")


def report(coarse: dict, fine: dict, live_rows: dict, final: list[tuple],
           axes_fine: dict) -> str:
    L = []
    L.append(f"# Williams %R на {SYMBOL}: ночной перебор {datetime.now():%d.%m.%Y}\n")
    L.append("Критерий — не «лучший результат», а **плюс на всех трёх окнах**. "
             "Одно окно выигрывает и мусор.\n")
    L.append("| Окно | Период |\n|---|---|")
    for w, a, b in WINDOWS:
        L.append(f"| {w} | {a} … {b} |")
    L.append("")

    L.append("## Текущие параметры живого робота\n")
    L.append(f"`period={LIVE['period']} oversold={LIVE['oversold']} "
             f"overbought={LIVE['overbought']} tp_atr=0 sl_pct=0`\n")
    L.append("| Окно | Финрез, ₽ | Сделок | RF | Просадка |\n|---|---:|---:|---:|---:|")
    for w, _, _ in WINDOWS:
        r = live_rows.get(w) or {}
        L.append(f"| {w} | {fmt(r.get('net'))} | {fmt(r.get('trades'))} | "
                 f"{fmt(r.get('rf'), 2)} | {fmt((r.get('dd') or 0) * 100, 1)}% |")
    L.append("")

    n_all = len(next(iter(coarse.values()))) if coarse else 0
    L.append(f"## Грубая сетка: {n_all} комбинаций × {len(WINDOWS)} окна\n")
    surv_c = survivors(coarse)
    L.append(f"В плюсе на всех трёх окнах: **{len(surv_c)}** "
             f"({100 * len(surv_c) / max(1, n_all):.1f}%).\n")

    if fine:
        n_f = len(next(iter(fine.values())))
        L.append(f"## Уточнение: {n_f} комбинаций × {len(WINDOWS)} окна\n")

    if not final:
        L.append("## Вывод\n")
        L.append("**Устойчивых параметров нет.** Ни один набор не удержался в плюсе "
                 "на всех трёх окнах при достаточном числе сделок. Плюс живого "
                 "робота за июнь-август — свойство ЭТОГО отрезка, а не параметров: "
                 "на январе-мае та же логика теряет деньги. Ставить на реальные "
                 "деньги нечего.\n")
        return "\n".join(L)

    src = fine or coarse
    L.append("## Кандидаты, выжившие на всех окнах\n")
    L.append("| # | period | oversold | overbought | tp_atr | sl_pct | "
             + " | ".join(f"{w}, ₽" for w, _, _ in WINDOWS)
             + " | худшее окно | сделок (мин) | соседей в плюсе |")
    L.append("|---:|---:|---:|---:|---:|---:|" + "---:|" * (len(WINDOWS) + 3))
    for i, k in enumerate(final[:25], 1):
        nets = [(src[w][k].get("net") or 0) for w, _, _ in WINDOWS]
        tmin = min((src[w][k].get("trades") or 0) for w, _, _ in WINDOWS)
        nb = neighbourhood(k, axes_fine)
        good = sum(1 for n in nb
                   if all(n in src[w] and (src[w][n].get("net") or 0) > 0
                          for w, _, _ in WINDOWS))
        L.append(f"| {i} | " + " | ".join(str(x) for x in k) + " | "
                 + " | ".join(fmt(x) for x in nets)
                 + f" | {fmt(min(nets))} | {fmt(tmin)} | {good}/{len(nb)} |")
    L.append("")
    L.append("**Как читать «соседей»:** это доля соседних по сетке наборов, которые "
             "тоже в плюсе на всех окнах. Мало соседей — точка стоит на игле, такой "
             "результат живёт до первой смены режима. Много — есть плато, "
             "параметр можно двигать без обвала.\n")
    return "\n".join(L)


def main() -> int:
    t_start = time.time()
    log(f"старт: {SYMBOL} / {STRAT}, окон {len(WINDOWS)}")
    client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token()}"})

    grid = combos(AXES_COARSE)
    log(f"грубая сетка: {len(grid)} комбинаций "
        + ", ".join(f"{k}×{len(v)}" for k, v in AXES_COARSE.items()))
    # Параметры живого робота в сетку не попадают (period=38, oversold=67) —
    # добавляем явно, иначе не с чем сравнивать.
    grid_plus = grid + [LIVE]

    coarse: dict[str, dict] = {}
    for w, a, b in WINDOWS:
        log(f"грубая сетка, окно {w} ({a}…{b})")
        coarse[w] = run_window(client, "c", w, a, b, grid_plus)
        json.dump({"phase": "coarse", "window": w, "n": len(coarse[w])},
                  open(OUT_JSON + f".{w}.progress", "w"))
        if time.time() - t_start > DEADLINE_SEC:
            log("дедлайн на грубой фазе — свожу что есть")
            break

    live_rows = {w: coarse.get(w, {}).get(key_of(LIVE), {}) for w, _, _ in WINDOWS}
    surv = survivors(coarse)
    log(f"выжило на всех окнах после грубой сетки: {len(surv)}")

    fine: dict[str, dict] = {}
    axes_fine = AXES_COARSE
    if surv and time.time() - t_start < DEADLINE_SEC * 0.6:
        axes_fine = refine_axes(surv[:40])
        fgrid = combos(axes_fine)
        log(f"уточняющая сетка: {len(fgrid)} комбинаций "
            + ", ".join(f"{k}×{len(v)}" for k, v in axes_fine.items()))
        if len(fgrid) > REFINE_MAX:
            log(f"сетка больше потолка {REFINE_MAX} — сужаю до 15 лучших")
            axes_fine = refine_axes(surv[:15])
            fgrid = combos(axes_fine)
            log(f"уточняющая сетка: {len(fgrid)} комбинаций")
        for w, a, b in WINDOWS:
            log(f"уточнение, окно {w}")
            fine[w] = run_window(client, "f", w, a, b, fgrid)
            if time.time() - t_start > DEADLINE_SEC:
                log("дедлайн на уточнении — свожу что есть")
                break
        if len(fine) != len(WINDOWS):
            fine = {}                 # неполное уточнение сравнивать нельзя

    final = survivors(fine) if fine else surv
    log(f"итог: устойчивых наборов {len(final)}")

    src = fine or coarse
    json.dump({
        "symbol": SYMBOL, "strategy": STRAT, "windows": WINDOWS, "live": LIVE,
        "live_rows": live_rows, "keys": KEYS,
        "coarse_n": len(grid), "coarse_survivors": len(surv),
        "final": [{"params": dict(zip(KEYS, k)),
                   "by_window": {w: src[w].get(k) for w, _, _ in WINDOWS}}
                  for k in final[:60]],
    }, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)

    md = report(coarse, fine, live_rows, final, axes_fine)
    open(OUT_MD, "w", encoding="utf-8").write(md)
    log(f"отчёт: {OUT_MD} ({len(md)} символов), данные: {OUT_JSON}")
    log(f"всего заняло {int(time.time() - t_start) // 60} мин")
    return 0


if __name__ == "__main__":
    sys.exit(main())
