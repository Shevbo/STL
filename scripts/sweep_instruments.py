#!/usr/bin/env python3
"""Полный перебор Williams %R по НЕСКОЛЬКИМ инструментам, последовательно.

Ночной разбор закрыл вопрос только по газу: там валовый на круг +0.1 ₽ против
порога ~5.9 ₽. Узкий срез по RI показал, что у сигнала на индексе валовый
ПОЛОЖИТЕЛЕН на всех окнах и до безубытка не хватает 1.25-1.6 раза, а не 6-33.
Значит вывод «преимущества нет» про инструмент, а не про стратегию, и остальные
надо проверять так же честно: полной сеткой на трёх независимых окнах.

Порядок задан по «цене оборота» (стоимость круга в медианных минутках,
scripts/fee_wall.py): RI 0.2x, Si 0.4x, GD 0.9x. Чем дешевле оборот, тем больше
шансов, что реальный эдж сигнала переживёт издержки.

На каждый инструмент: та же сетка и те же окна, что и по газу, плюс разбор на
валовый и комиссию по СОБСТВЕННОЙ цене инструмента (биржевой сбор считается от
объёма, поэтому порог у каждого свой). Результат — отчёт на инструмент и общий
свод. Пишется ПОСЛЕ КАЖДОГО инструмента: обрыв на третьем не должен стоить
первых двух.

Запуск: cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
        nohup poetry run python -u scripts/sweep_instruments.py > ~/sweep_instr.log 2>&1 &
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from trader.lab.commission import commission_for, fee_group  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "wr", pathlib.Path(__file__).with_name("wr_gz_night.py"))
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)

# (базовый код склейки, серия для группы сбора и ₽/пункт)
INSTRUMENTS = [("RI", "RIU6"), ("GD", "GDU6"), ("Si", "SiU6")]
# Инструменты и оси задаются снаружи: тот же драйвер должен уметь прогнать
# отдельный вопрос (например «чем регулируется частота») без копии файла.
#   SWEEP_INSTRUMENTS="RI:RIU6"          SWEEP_TAG=freq
#   SWEEP_AXES='{"period":[40,70],...}'
if os.environ.get("SWEEP_INSTRUMENTS"):
    INSTRUMENTS = [tuple(x.split(":")) for x in os.environ["SWEEP_INSTRUMENTS"].split(",")]
if os.environ.get("SWEEP_AXES"):
    wr.AXES_COARSE = json.loads(os.environ["SWEEP_AXES"])
    wr.KEYS = list(wr.AXES_COARSE)
TAG = os.environ.get("SWEEP_TAG", "instr")
OUT_MD = os.path.expanduser(f"~/sweep_{TAG}_report.md")
OUT_JSON = os.path.expanduser(f"~/sweep_{TAG}.json")
DEADLINE_SEC = int(os.environ.get("SWEEP_DEADLINE_SEC", 8 * 3600))


def spec_of(sym: str) -> tuple[float, float]:
    """(₽/пункт, ГО) фронтового контракта — из ISS, один раз на инструмент.

    ISS отдаёт спеку не всегда, а на заглушке pv=1.0 весь денежный вывод по
    инструменту становится ложным (у GD сбор выходил 1.78 ₽ вместо 71.54).
    Поэтому пустая спека — исключение, а не «посчитаем по единице»."""
    import asyncio
    import time as _t

    from trader.lab.iss_loader import fetch_contract_spec
    for i in range(4):
        d = asyncio.run(fetch_contract_spec(sym)) or {}
        if d.get("point_value"):
            return float(d["point_value"]), float(d.get("initial_margin") or 0.0)
        if i < 3:
            _t.sleep(2)
    raise RuntimeError(f"{sym}: ISS не отдал ₽/пункт — считать деньги не по чему")


def window_price(key: str, a: str, b: str) -> float:
    """Медианная цена в окне по закэшированной склейке: биржевой сбор считается от
    объёма, поэтому порог безубытка у каждого окна свой."""
    import datetime as dt
    rows = json.load(open(f"agent_bars/{key}.json", encoding="utf-8"))["rows"]
    lo = dt.datetime.fromisoformat(a).replace(tzinfo=dt.timezone.utc).timestamp()
    hi = dt.datetime.fromisoformat(b).replace(tzinfo=dt.timezone.utc).timestamp() + 86399
    px = [r[4] for r in rows if lo <= r[0] <= hi]
    return statistics.median(px) if px else 0.0


def analyse(key: str, fee_sym: str, pv: float, byw: dict) -> dict:
    """Свод по инструменту: выжившие по чистому, по валовому, лучшие наборы."""
    fees = {}
    for w, a, b in wr.WINDOWS:
        p = window_price(key, a, b)
        fees[w] = 2 * commission_for(fee_sym, p, 1, pv, True) if p else 0.0
    stat = {"symbol": key, "group": fee_group(fee_sym), "pv": pv,
            "fees": fees, "windows": {}}
    keyed = {}
    for w, _, _ in wr.WINDOWS:
        rows = byw.get(w) or {}
        f = fees[w]
        nets = [v["net"] for v in rows.values() if v.get("net") is not None]
        gross = {k: (v["net"] + (v["trades"] or 0) * f, v["net"], v["trades"] or 0)
                 for k, v in rows.items()
                 if v.get("net") is not None and (v.get("trades") or 0) >= wr.MIN_TRADES}
        keyed[w] = gross
        per = [g / t for g, _, t in gross.values() if t]
        stat["windows"][w] = {
            "combos": len(rows), "fee_round": round(f, 2),
            "net_win": sum(1 for n in nets if n > 0),
            "net_best": max(nets) if nets else None,
            "net_median": statistics.median(nets) if nets else None,
            "gross_win": sum(1 for g, _, _ in gross.values() if g > 0),
            "gross_best": max((g for g, _, _ in gross.values()), default=None),
            "per_trade_median": round(statistics.median(per), 2) if per else None,
            "trades_min": min((t for _, _, t in gross.values()), default=None),
        }
    common = set.intersection(*(set(v) for v in keyed.values())) if len(keyed) > 1 else set()
    stat["all_windows"] = len(common)
    # Считаем ПОЛНОЕ число выживших и только потом режем список до топ-10: иначе
    # отчёт сообщал «в плюсе 10» там, где в плюсе были все 2 592 (RI, 02.08.2026).
    net_ok = [k for k in common if all(keyed[w][k][1] > 0 for w in keyed)]
    gross_ok = [k for k in common if all(keyed[w][k][0] > 0 for w in keyed)]
    stat["net_all_n"] = len(net_ok)
    stat["gross_all_n"] = len(gross_ok)
    stat["net_all"] = sorted(
        net_ok, key=lambda k: min(keyed[w][k][1] for w in keyed), reverse=True)[:10]
    stat["gross_all"] = sorted(
        gross_ok, key=lambda k: min(keyed[w][k][0] / max(keyed[w][k][2], 1) for w in keyed),
        reverse=True)[:10]
    stat["detail"] = {
        "net_all": [{"params": dict(zip(wr.KEYS, k)),
                     "by_window": {w: {"net": round(keyed[w][k][1]),
                                       "gross": round(keyed[w][k][0]),
                                       "trades": keyed[w][k][2]} for w in keyed}}
                    for k in stat["net_all"]],
        "gross_all": [{"params": dict(zip(wr.KEYS, k)),
                       "by_window": {w: {"net": round(keyed[w][k][1]),
                                         "gross": round(keyed[w][k][0]),
                                         "per_trade": round(keyed[w][k][0] / max(keyed[w][k][2], 1), 2),
                                         "trades": keyed[w][k][2]} for w in keyed}}
                      for k in stat["gross_all"]],
    }
    return stat


def md_for(st: dict) -> str:
    L = [f"## {st['symbol']} (группа сборов «{st['group']}», {st['pv']:.3f} ₽/пункт)\n"]
    L.append("| Окно | Комбинаций | Круг, ₽ | Чистый > 0 | Лучший чистый | "
             "Валовый > 0 | Валовый на круг (медиана) | Сделок мин |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for w, _, _ in wr.WINDOWS:
        d = st["windows"].get(w) or {}
        nb = d.get("net_best")
        L.append(f"| {w} | {d.get('combos', 0)} | {d.get('fee_round', 0):.2f} | "
                 f"{d.get('net_win', 0)} | {'—' if nb is None else f'{nb:+,.0f}'.replace(',', ' ')} | "
                 f"{d.get('gross_win', 0)} | {d.get('per_trade_median')} | {d.get('trades_min')} |")
    L.append("")
    n = max(st["all_windows"], 1)
    L.append(f"Комбинаций, общих для всех окон: {st['all_windows']}. "
             f"**В плюсе по ЧИСТОМУ на всех окнах: {st.get('net_all_n', 0)}.** "
             f"В плюсе по ВАЛОВОМУ на всех окнах: {st.get('gross_all_n', 0)} "
             f"({100 * st.get('gross_all_n', 0) / n:.0f}%).\n")
    for title, kind in (("Лучшие по чистому (в плюсе везде)", "net_all"),
                        ("Лучшие по валовому на круг (в плюсе везде)", "gross_all")):
        items = st["detail"][kind]
        if not items:
            continue
        L.append(f"**{title}**\n")
        L.append("| " + " | ".join(wr.KEYS) + " | "
                 + " | ".join(f"{w}: чистый / валовый / сделок" for w, _, _ in wr.WINDOWS) + " |")
        L.append("|" + "---:|" * (len(wr.KEYS) + len(wr.WINDOWS)))
        for it in items[:6]:
            cells = [str(it["params"][k]) for k in wr.KEYS]
            for w, _, _ in wr.WINDOWS:
                d = it["by_window"][w]
                cells.append(f"{d['net']:+,.0f} / {d['gross']:+,.0f} / {d['trades']}".replace(",", " "))
            L.append("| " + " | ".join(cells) + " |")
        L.append("")
    return "\n".join(L)


def main() -> int:
    t0 = time.time()
    client = httpx.Client(base_url=wr.API,
                          headers={"Authorization": f"Bearer {wr.token()}"})
    grid = wr.combos(wr.AXES_COARSE)
    wr.log(f"перебор по инструментам: {len(grid)} комбинаций × {len(wr.WINDOWS)} окна "
           f"× {len(INSTRUMENTS)} инструмента")
    all_stats = []
    for key, fee_sym in INSTRUMENTS:
        if time.time() - t0 > DEADLINE_SEC:
            wr.log(f"дедлайн — {key} и дальше пропускаю")
            break
        pv, margin = spec_of(fee_sym)
        wr.log(f"══ {key} ({fee_sym}): {pv} ₽/пункт, ГО {margin:.0f} ₽")
        wr.SYMBOL = key
        wr.CAMPAIGN = f"wr{key.lower()}{TAG}"
        byw = {}
        for w, a, b in wr.WINDOWS:
            wr.log(f"  {key}: окно {w} ({a}…{b})")
            byw[w] = wr.run_window(client, "i", f"{key}{w}", a, b, grid)
            if time.time() - t0 > DEADLINE_SEC:
                wr.log("дедлайн внутри инструмента — свожу что есть")
                break
        st = analyse(key, fee_sym, pv, byw)
        all_stats.append(st)
        wr.log(f"  {key}: в плюсе по чистому на всех окнах {len(st['net_all'])}, "
               f"по валовому {len(st['gross_all'])}")
        # Пишем ПОСЛЕ КАЖДОГО: обрыв на третьем не должен стоить первых двух.
        json.dump(all_stats, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
        head = ["# Williams %R по инструментам: полный перебор\n",
                f"Сетка {len(grid)} комбинаций × {len(wr.WINDOWS)} окна на каждый "
                f"инструмент, склейка по переднему контракту.\n",
                "Критерий — плюс на ВСЕХ окнах, а не лучший результат. Отдельно "
                "показан ВАЛОВЫЙ (до комиссии): он отвечает, есть ли у сигнала "
                "предсказательная сила вообще, отдельно от издержек.\n",
                "| Окно | Период |", "|---|---|"]
        head += [f"| {w} | {a} … {b} |" for w, a, b in wr.WINDOWS]
        head.append("")
        open(OUT_MD, "w", encoding="utf-8").write(
            "\n".join(head) + "\n" + "\n".join(md_for(s) for s in all_stats))
        wr.log(f"  отчёт обновлён: {OUT_MD}")
    wr.log(f"готово за {int(time.time() - t0) // 60} мин")
    return 0


if __name__ == "__main__":
    sys.exit(main())
