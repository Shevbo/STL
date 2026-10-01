"""Якоря реальных заявок роботов для exec_policy (шаг 2b программы
docs/execution-cost-program.md): моменты СЛУЧАЙНЫХ снимков стакана не похожи
на моменты заявок робота (lxk22 усредняет против хода, macdshort/usopen
входят по ходу) — этот скрипт вынимает реальные (ts, side, qty, robot,
outcome, fact_vs_mid) из базовой линии fills.csv (scripts/exec_baseline.py)
и приводит их к шкале полной выжимки стакана (book_full_digest.py), чтобы
exec_policy.run(..., anchors_key=...) мог сравнить политики на тех же самых
моментах, что видел робот.

ШКАЛА ВРЕМЕНИ. fills.csv.send_ts — реальный UTC unix-epoch (секунды), как
его пишет fills_vs_model._iter_order_events. Выжимка стакана хранит ts_ms в
МСК-стенке-как-UTC (time_base "bars (+3h от UTC архива)", тот же сдвиг, что
MSK_SHIFT в trader.lab.book_replay — см. scripts/exec_baseline.enrich).
Якорь без этого сдвига целился бы в стакан на 3 часа раньше реальной
заявки. ts_ms_якоря = (send_ts + MSK_SHIFT) * 1000.

ВЫХОД. JSON тем же каналом, что агентские бары (retro_reverse._load_bars
читает только ключ "rows", остальное — для человека):
    {"ts_unit": "ms", "code": <code>, "rows": [[ts_ms, side, qty, robot,
     outcome, fact_vs_mid|null], ...]}
Отбор: только code == --code, file_date в [--since, --until] (обе границы
включительно, сравнение строк ISO-дат), mid непустой (был свежий снимок на
момент отправки — без него сравнивать не с чем).
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter

from trader.lab.book_replay import MSK_SHIFT

SIDE_MAP = {"SIDE_BUY": "buy", "SIDE_SELL": "sell"}


def build_anchors(csv_path: str, code: str, since: str | None, until: str | None,
                  min_qty: int = 0, robots: list[str] | None = None) -> list[list]:
    """robots: префиксы имён (lxk22 берёт lxk22*); None = все."""
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["code"] != code:
                continue
            if since and r["file_date"] < since:
                continue
            if until and r["file_date"] > until:
                continue
            if not r["mid"]:
                continue
            if robots and not r["robot"].startswith(tuple(robots)):
                continue
            if int(float(r["qty"])) < min_qty:
                continue
            side = SIDE_MAP.get(r["side"])
            if side is None:
                continue
            ts_ms = round((float(r["send_ts"]) + MSK_SHIFT) * 1000)
            fact_vs_mid = float(r["fact_vs_mid"]) if r["fact_vs_mid"] else None
            rows.append([ts_ms, side, int(float(r["qty"])), r["robot"], r["outcome"], fact_vs_mid])
    rows.sort(key=lambda x: x[0])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--min-qty", type=int, default=0)
    ap.add_argument("--robots", help="префиксы через запятую, по умолчанию все")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rows = build_anchors(a.csv, a.code, a.since, a.until, a.min_qty,
                         a.robots.split(",") if a.robots else None)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"ts_unit": "ms", "code": a.code, "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))

    by_robot_side = Counter((r[3], r[1]) for r in rows)
    print(f"якорей: {len(rows)} -> {a.out}")
    by_cls = Counter((r[3], r[1], "6-10" if r[2] <= 10 else "11+") for r in rows)
    for (robot, side, cls), n in sorted(by_cls.items()):
        print(f"  класс {robot:24s} {side:4s} {cls:5s} {n}")
    for (robot, side), n in sorted(by_robot_side.items()):
        print(f"  {robot:28s} {side:4s} {n}")


if __name__ == "__main__":
    main()
