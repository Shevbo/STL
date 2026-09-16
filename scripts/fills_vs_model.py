"""Сверка модели исполнения по НАШИМ ЖИВЫМ ФИЛЛАМ (16.09.2026).

Мои оценки («исполнение по бару оптимистично на 6-19 пт за заявку») держатся на модели
BookRuntime, которую никто не сверял с реальностью. В архиве есть 4 тыс наших реальных
филлов: order-*.jsonl хранит отправку заявки (PENDING, своя цена) и исполнение (FILLED,
цена филла). Для каждого филла берём снимок стакана НА МОМЕНТ ОТПРАВКИ и считаем:
  модель   — рыночная заявка: VWAP по встречной стороне на тот же объём;
  факт     — цена, по которой реально налилось;
  середина — mid того же снимка.
Разница «модель минус факт» со знаком «модель хуже факта» = насколько модель
пессимистична. Живой робот ставит ЛИМИТКИ, поэтому факт обязан быть лучше рыночной
модели; если он хуже — модель врёт в нашу пользу, и все оценки издержек занижены.

    PYTHONPATH=. python scripts/fills_vs_model.py --orders orders/ --book book/ --code RIU6
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

from trader.lab.book_replay import MSK_SHIFT, BookRuntime, load_dir


def load_orders(root: str, code: str):
    """(исполненные, всего отправлено): неисполненные заявки тоже нужны — их цена не
    спред, а упущенная сделка, и в выборке филлов их не видно (выжившие)."""
    sent, filled = {}, {}
    for name in sorted(os.listdir(root)):
        if not name.startswith("order-"):
            continue
        op = gzip.open if name.endswith(".gz") else open
        with op(os.path.join(root, name), "rt", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("code") != code or not r.get("client_id"):
                    continue
                st, cid = r.get("state"), r["client_id"]
                # ts_unix_ms есть не у всех кадров: у части только метка приёма STL
                raw = r.get("ts_unix_ms") or r.get("stl_recv_ms")
                if not raw:
                    continue
                ts = int(raw) // 1000
                if st == "ORDER_STATE_PENDING" and cid not in sent:
                    sent[cid] = (ts, r.get("side"), int(r.get("quantity") or 0), float(r.get("price") or 0))
                elif st == "ORDER_STATE_FILLED" and cid not in filled:
                    filled[cid] = (ts, float(r.get("price") or 0), int(r.get("filled") or 0))
    out = []
    for cid, (t_send, side, qty, px_sent) in sent.items():
        if cid in filled:
            t_fill, px_fill, n = filled[cid]
            if qty and px_fill:
                out.append((t_send, t_fill, side, n or qty, px_fill, px_sent))
    return sorted(out), len(sent)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", required=True)
    ap.add_argument("--book", required=True)
    ap.add_argument("--code", default="RIU6")
    ap.add_argument("--gap", type=int, default=60)
    a = ap.parse_args()

    fills, n_sent = load_orders(a.orders, a.code)
    times, books = load_dir(a.book, a.code)
    print(f"{a.code}: отправлено заявок {n_sent}, из них исполнено {len(fills)} "
          f"({len(fills) / max(n_sent, 1):.0%}); снимков стакана {len(times)}")

    rows, by_hour, no_book = [], defaultdict(list), 0
    for t_send, t_fill, side, qty, px_fill, px_sent in fills:
        ts = t_send + MSK_SHIFT
        # СНИМОК ДО ОТПРАВКИ. bisect_left брал первый снимок ПОСЛЕ отправки, а при
        # медиане ожидания 0 с это книга уже ПОСЛЕ нашего филла и движения цены:
        # модель шагала по обглоданной книге, mid был сдвинут, и получалось, что факт
        # лучше модели на 4.5 пт (и p10 −10, невозможный для заявки по касанию).
        i = bisect.bisect_right(times, ts) - 1
        if i < 0 or ts - times[i] > a.gap:
            no_book += 1
            continue
        bids, asks = books[i]
        model, _deep = BookRuntime._walk(asks if side == "SIDE_BUY" else bids, qty)
        mid = (bids[0][0] + asks[0][0]) / 2
        sign = 1 if side == "SIDE_BUY" else -1
        rows.append({
            "model_vs_mid": sign * (model - mid),      # чего стоила бы рыночная
            "fact_vs_mid": sign * (px_fill - mid),     # чего стоила наша лимитка
            "model_minus_fact": sign * (model - px_fill),
            "wait_s": t_fill - t_send, "qty": qty,
            "hour": datetime.fromtimestamp(ts, timezone.utc).hour,
        })
        by_hour[rows[-1]["hour"]].append(rows[-1]["fact_vs_mid"])

    if not rows:
        print("нет филлов со свежим стаканом")
        return
    def line(name, k):
        s = sorted(r[k] for r in rows)
        print(f"  {name:28} медиана {median(s):+7.1f}  среднее {sum(s) / len(s):+7.1f}  "
              f"p10 {s[len(s) // 10]:+7.1f}  p90 {s[9 * len(s) // 10]:+7.1f}")
    print(f"сопоставлено со стаканом {len(rows)}, без свежего стакана {no_book}")
    print("пункты, знак «хуже для нас»:")
    line("модель (рыночная) к mid", "model_vs_mid")
    line("ФАКТ (наша лимитка) к mid", "fact_vs_mid")
    line("модель минус факт", "model_minus_fact")
    worse = sum(1 for r in rows if r["fact_vs_mid"] > r["model_vs_mid"])
    print(f"  факт ХУЖЕ рыночной модели в {worse}/{len(rows)} филлах = {worse / len(rows):.0%}")
    q = sorted(r["qty"] for r in rows)
    w = sorted(r["wait_s"] for r in rows)
    print(f"  объём заявки: медиана {median(q):.0f}, p90 {q[9 * len(q) // 10]:.0f}; "
          f"ожидание филла: медиана {median(w):.0f} с, p90 {w[9 * len(w) // 10]:.0f} с")
    print("  факт к mid по часам МСК: " + " ".join(f"{h:02d}:{median(v):+.0f}"
                                                   for h, v in sorted(by_hour.items())))
    # Разбивка по объёму: модель шагает по уровням, факт — нет, и на 1 лоте оба должны
    # сходиться к полспреда. Расхождение именно там показывает, врёт ли модель сама по
    # себе, а не из-за объёма.
    print("  по объёму заявки (лотов: филлов, модель к mid, факт к mid):")
    for lo, hi in ((1, 1), (2, 4), (5, 9), (10, 1000)):
        part = [r for r in rows if lo <= r["qty"] <= hi]
        if part:
            m = sorted(r["model_vs_mid"] for r in part)
            f2 = sorted(r["fact_vs_mid"] for r in part)
            tag = f"{lo}" if lo == hi else f"{lo}-{hi if hi < 1000 else '+'}"
            print(f"    {tag:>5}: {len(part):5}  модель {median(m):+6.1f}  факт {median(f2):+6.1f}")


if __name__ == "__main__":
    main()
