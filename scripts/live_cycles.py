"""Уроки ЖИВОЙ торговли: круги робота из журнала algo_trades.

ЗАЧЕМ. Оператор спросил про «неоправданно частые сделки» и «ошибочные ранние
выходы с убытком». Оба вопроса про КРУГ (вход-выход), а журнал хранит филлы.
Скрипт склеивает филлы в круги по pos_after (круг = от нуля до нуля) и считает
то, что решает: сколько круг заработал валом, сколько отдал комиссии, сколько
жил, и какая доля кругов не покрыла собственные издержки.

Судья здесь журнал, а не отчёт раннера: pnl_net_rub/pos_after считает сам
журнал по филлам (trader/quik/algo_ledger.py).

    python scripts/live_cycles.py real_fills.csv [--robot lxk22...]

CSV — выгрузка /api/v1/quik/algo-trades?mode=real&format=csv (разделитель «;»).
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
from datetime import datetime


def read_fills(path: str, robot: str | None) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    out = []
    for r in rows:
        if robot and r["robot_id"] != robot:
            continue
        out.append({
            "robot": r["robot_id"],
            "ts": datetime.strptime(r["dt_msk"], "%Y-%m-%d %H:%M:%S"),
            "side": r["side"],
            "qty": int(float(r["qty"])),
            "price": float(r["price"]),
            "gross": float(r["pnl_gross_rub"] or 0),
            "comm": float(r["commission_rub"] or 0),
            "net": float(r["pnl_net_rub"] or 0),
            "pos": int(float(r["pos_after"])),
        })
    out.sort(key=lambda f: f["ts"])
    return out


def cycles(fills: list[dict]) -> list[dict]:
    """Круг = серия филлов от позиции 0 до возврата в 0.

    Незакрытый хвост отбрасываем: у него нет итога. Филлы до первого нуля тоже
    (робот мог начать с унаследованной позиции).
    """
    out: list[dict] = []
    cur: list[dict] = []
    for f in fills:
        cur.append(f)
        if f["pos"] == 0:
            entries = sum(x["qty"] for x in cur if x["gross"] == 0)
            out.append({
                "t0": cur[0]["ts"], "t1": f["ts"],
                "mins": (f["ts"] - cur[0]["ts"]).total_seconds() / 60.0,
                "fills": len(cur),
                "peak": max(abs(x["pos"]) for x in cur),
                "entries": entries,
                "gross": sum(x["gross"] for x in cur),
                "comm": sum(x["comm"] for x in cur),
                "net": sum(x["net"] for x in cur),
                "dir": "long" if cur[0]["side"] == "buy" else "short",
            })
            cur = []
    return out


def pct(v: float, tot: float) -> str:
    return f"{100.0 * v / tot:5.1f}%" if tot else "  n/a"


def report(name: str, cs: list[dict]) -> None:
    if not cs:
        print(f"{name}: кругов нет")
        return
    tot_net = sum(c["net"] for c in cs)
    tot_gross = sum(c["gross"] for c in cs)
    tot_comm = sum(c["comm"] for c in cs)
    win = [c for c in cs if c["net"] > 0]
    loss = [c for c in cs if c["net"] <= 0]
    mins = sorted(c["mins"] for c in cs)
    print(f"\n=== {name}: {len(cs)} кругов, "
          f"{cs[0]['t0']:%d.%m} – {cs[-1]['t1']:%d.%m} ===")
    print(f"вал {tot_gross:+11.0f} руб | комиссия {tot_comm:10.0f} руб "
          f"({pct(tot_comm, abs(tot_gross))} вала) | итог {tot_net:+11.0f} руб")
    print(f"плюсовых {len(win)} ({pct(len(win), len(cs))}) сред {
          st.mean([c['net'] for c in win]) if win else 0:+8.0f} руб | "
          f"минусовых {len(loss)} сред "
          f"{st.mean([c['net'] for c in loss]) if loss else 0:+8.0f} руб")
    print(f"длительность круга, мин: медиана {st.median(mins):6.1f} "
          f"p10 {mins[len(mins)//10]:5.1f} p90 {mins[9*len(mins)//10]:7.1f}")

    # Частота: круги, чей ВАЛ не покрыл собственную комиссию = чистая пила.
    churn = [c for c in cs if c["gross"] < c["comm"]]
    print(f"круги, где вал < своей комиссии: {len(churn)} "
          f"({pct(len(churn), len(cs))}), их итог {
          sum(c['net'] for c in churn):+10.0f} руб")

    # Ранние выходы: короткие круги против остальных.
    for cut in (5, 15, 60):
        short = [c for c in cs if c["mins"] <= cut]
        rest = [c for c in cs if c["mins"] > cut]
        if not short or not rest:
            continue
        print(f"  <= {cut:3d} мин: {len(short):4d} кругов, итог "
              f"{sum(c['net'] for c in short):+10.0f} руб, "
              f"{st.mean([c['net'] for c in short]):+7.0f} руб/круг "
              f"| остальные {st.mean([c['net'] for c in rest]):+7.0f} руб/круг")

    # Усреднение: круги с добором против одноразовых.
    avg = [c for c in cs if c["peak"] > 1]
    one = [c for c in cs if c["peak"] == 1]
    if avg and one:
        print(f"с усреднением {len(avg)} кругов "
              f"{st.mean([c['net'] for c in avg]):+8.0f} руб/круг "
              f"(пик до {max(c['peak'] for c in avg)}) | "
              f"без {len(one)} {st.mean([c['net'] for c in one]):+8.0f} руб/круг")

    for d in ("long", "short"):
        sub = [c for c in cs if c["dir"] == d]
        if sub:
            print(f"{d:>5}: {len(sub):4d} кругов, итог "
                  f"{sum(c['net'] for c in sub):+10.0f} руб")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--robot", default=None)
    ap.add_argument("--min-fills", type=int, default=50,
                    help="роботов мельче не печатать")
    args = ap.parse_args()

    fills = read_fills(args.csv, args.robot)
    by_robot: dict[str, list[dict]] = {}
    for f in fills:
        by_robot.setdefault(f["robot"], []).append(f)
    for rid, fs in sorted(by_robot.items(), key=lambda kv: -len(kv[1])):
        if len(fs) < args.min_fills:
            continue
        report(f"{rid} ({len(fs)} филлов)", cycles(fs))


if __name__ == "__main__":
    main()
