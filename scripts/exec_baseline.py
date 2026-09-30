"""Базовая линия издержек исполнения по НАШИМ ЖИВЫМ заявкам, шаг 0 программы
docs/execution-cost-program.md (30.09.2026): за один проход по всем дням архива и
всем кодам — судьба каждой заявки (исполнена/частично/снята/отклонена), задержка
PENDING->FILLED, цена филла против mid и против модели рыночной заявки (VWAP по
встречной стороне, trader/lab/book_replay.BookRuntime._walk), разрезы по часу,
объёму, роботу, стороне, классу минуты, дню недели и месту лимитки в стакане на
момент отправки, и перевод в рубли/месяц через point_value из instrument_meta (БД
STL, зеркало MOEX ISS — см. load_point_values).

Переиспользует fills_vs_model._iter_order_events (парсер order-*.jsonl(.gz)) и
trader.lab.book_replay.MSK_SHIFT/BookRuntime (VWAP-модель), сам не парсит архив
заново — только классифицирует и агрегирует.

v2 (30.09.2026, проверка fable к v1):
  - mid брался из снимка с допуском 60 с, а раннер шлёт по свежей котировке — на
    старом снимке классы «глубже»/«сквозь» были лагом, не поведением робота.
    Теперь каждая запись несёт book_age_ms (возраст снимка НА МОМЕНТ ОТПРАВКИ);
    место лимитки (и, значит, taker/maker) считается ТОЛЬКО при возрасте <=
    --max-age-ms (по умолчанию 3000), а факт/модель к mid остаются для любого
    возраста в пределах --gap — чтобы показать сам разрез по возрасту (раздел 2a).
  - exec_price архива (цена события FILLED) сверяется с VWAP реальных сделок из
    ~/apps/shectory-trader/data/trades/<дата>.jsonl по order_num == order_id
    FILLED (раздел 6, окно --verify-from..--verify-to).

ПАМЯТЬ. Один день стакана может весить сотню мегабайт в распакованном виде. v1
грузил load_book(paths, code) целиком на (день, код) и один проход съел 2.6 ГБ RSS
на хостере (earlyoom рядом с ~3.3 ГБ свободных). v2 НЕ грузит книгу списком: читает
book-<дата>(-recovered) построчно (_iter_book_lines, генератор) и идёт по уже
отсортированным заявкам ДВУМЯ КУРСОРАМИ (heapq.merge книги по всем путям дня +
скользящие prev/cur) — в памяти одновременно только текущий и предыдущий снимок на
(день, код), не весь день.

Запуск (на хостере, под nice):
    cd ~/apps/shectory-trader && PYTHONPATH=. nice -n 19 \
      $(ls -d ~/.cache/pypoetry/virtualenvs/shectory-trader-*/bin/python | head -1) \
      scripts/exec_baseline.py --orders ~/market-archive --book ~/market-archive \
      --trades ~/apps/shectory-trader/data/trades \
      --out-csv /tmp/fills.csv --out-report /tmp/baseline.md
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gc
import gzip
import heapq
import json
import os
import sys
from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone
from statistics import median

sys.path.insert(0, os.path.dirname(__file__))  # bare same-dir import, см. scripts/rf_gap.py
from fills_vs_model import _iter_order_events  # noqa: E402

from trader.lab.book_replay import MSK_SHIFT, BookRuntime  # noqa: E402
from trader.lab.commission import commission_for  # noqa: E402

TERMINAL_OUTCOME = {
    "ORDER_STATE_FILLED": "filled",
    "ORDER_STATE_PARTIAL": "partial_open",   # архив кончился, остаток ещё в стакане
    "ORDER_STATE_ACTIVE": "active_eod",      # то же для неисполненной лимитки
}
QTY_BUCKETS = ((1, 1), (2, 5), (6, 10), (11, 10**9))
EVENT_MINUTES = {(10, 0), (15, 30), (16, 30), (17, 0), (23, 0)}
AGE_BUCKETS = ((1000, "≤1 с"), (3000, "1-3 с"), (10000, "3-10 с"), (60000, "10-60 с"))


def robot_tag(cid: str) -> str:
    if cid.startswith("rr:"):
        parts = cid.split(":")
        return parts[1] if len(parts) > 1 else "rr:?"
    if cid.startswith("so:"):
        return "smart_order"           # client_id несёт свой ID заявки, не робота
    return "other"


def classify(terminal_state: str, filled_qty: int) -> str:
    if terminal_state == "ORDER_STATE_CANCELLED":
        return "partial_cancelled" if filled_qty > 0 else "cancelled"
    if terminal_state == "ORDER_STATE_REJECTED":
        return "rejected"
    return TERMINAL_OUTCOME.get(terminal_state, f"other:{terminal_state}")


def qty_bucket(q: int) -> str:
    for lo, hi in QTY_BUCKETS:
        if lo <= q <= hi:
            return f"{lo}" if lo == hi else f"{lo}-{hi if hi < 10**9 else '+'}"
    return "?"


def minute_class(hour: int, minute: int) -> str:
    if (hour, minute) in EVENT_MINUTES:
        return "событие"
    if minute % 30 == 0:
        return "граница(:00/:30)"
    return "прочее"


def age_bucket(age_ms: float) -> str:
    for hi, label in AGE_BUCKETS:
        if age_ms <= hi:
            return label
    return ">60 с"


def placement_class(side: str, price_sent: float, best_bid: float, best_ask: float) -> str:
    """Где легла наша лимитка относительно стакана НА МОМЕНТ ОТПРАВКИ."""
    eps = 1e-6
    if side == "SIDE_BUY":
        if price_sent >= best_ask - eps:
            return "маркетабельная"
        if abs(price_sent - best_bid) < eps:
            return "свой_best"
        if best_bid < price_sent < best_ask:
            return "внутри_спреда"
        return "глубже"
    else:
        if price_sent <= best_bid + eps:
            return "маркетабельная"
        if abs(price_sent - best_ask) < eps:
            return "свой_best"
        if best_bid < price_sent < best_ask:
            return "внутри_спреда"
        return "глубже"


def build_orders(root: str) -> list[dict]:
    """order-*.jsonl(.gz) -> одна запись на заявку (нужен PENDING; событий без него,
    как 'cli-1' без ts, уже нет — их отсеял _iter_order_events)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in _iter_order_events(root):
        groups[r["client_id"]].append(r)

    orders = []
    for cid, events in groups.items():
        events.sort(key=lambda e: e["ts"])
        pend = next((e for e in events if e["state"] == "ORDER_STATE_PENDING"), None)
        if pend is None:
            continue
        terminal = events[-1]
        first_fill = next((e for e in events if e["state"] == "ORDER_STATE_FILLED"), None)
        last_partial = max((e for e in events if e["filled"] > 0),
                            key=lambda e: e["ts"], default=None)
        filled_qty = terminal["filled"] or (pend["qty"] if terminal["state"] == "ORDER_STATE_FILLED" else 0)
        outcome = classify(terminal["state"], filled_qty)

        if first_fill is not None:
            exec_ts, exec_px, exec_approx = first_fill["ts"], first_fill["price"], False
        elif last_partial is not None:
            exec_ts, exec_px, exec_approx = last_partial["ts"], last_partial["price"], True
        else:
            exec_ts, exec_px, exec_approx = None, None, None
        # order_id брокера (нужен для сверки с журналом сделок data/trades/<дата>.jsonl,
        # где он же лежит в поле order_num) — есть только у FILLED/PARTIAL, у чистого
        # PENDING/REJECTED брокер его ещё не присвоил.
        oid_src = first_fill or last_partial
        order_id = oid_src["order_id"] if oid_src else None

        orders.append({
            "file_date": pend["file_date"], "code": pend["code"], "client_id": cid,
            "robot": robot_tag(cid), "side": pend["side"], "qty": pend["qty"],
            "send_ts": pend["ts"], "send_price": pend["price"],
            "outcome": outcome, "filled_qty": filled_qty,
            "exec_ts": exec_ts, "exec_price": exec_px, "exec_approx": exec_approx,
            "wait_s": (exec_ts - pend["ts"]) if exec_ts is not None else None,
            "terminal_state": terminal["state"], "order_id": order_id,
            "real_vwap": None, "real_trade_qty": None, "price_gap_pts": None,
        })
    return orders


def book_paths_for_date(root: str, date: str) -> list[str]:
    out = []
    for suffix in ("", "-recovered"):
        for ext in (".jsonl.gz", ".jsonl"):
            p = os.path.join(root, f"book-{date}{suffix}{ext}")
            if os.path.exists(p):
                out.append(p)
    return out


def _iter_book_lines(path: str, code: str):
    """Один файл book-*.jsonl(.gz) -> генератор (ts, bids, asks) для совпадающего
    кода, БЕЗ накопления в список (в отличие от book_replay.load_book) — весь день
    стакана в память не грузим, см. докстринг модуля. Строки внутри файла уже идут
    по времени (проверено на архиве), поэтому единственный проход = отсортированный
    поток."""
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            if code not in ln:
                continue                      # дешёвый отсев; точная проверка ниже
            try:
                r = json.loads(ln)
                if r.get("code") != code:     # не по подстроке: форматирование JSON разное
                    continue
                ts = int(r["received_at_unix_ms"]) // 1000 + MSK_SHIFT
                bids = sorted(((float(x["price"]), int(x["quantity"]))
                               for x in r.get("bids") or []), reverse=True)
                asks = sorted((float(x["price"]), int(x["quantity"]))
                              for x in r.get("asks") or [])
            except (KeyError, TypeError, ValueError):
                continue
            if not bids or not asks:
                continue
            yield ts, bids, asks


def match_book(orders: list[dict], book_root: str, gap: int, max_age_ms: int) -> tuple[int, int]:
    """Дополняет каждую запись стаканными полями IN PLACE, по одному (дата, код) за
    раз. Заявки сортируются по времени отправки и сверяются с потоком снимков
    (heapq.merge по путям дня) ДВУМЯ КУРСОРАМИ (prev/cur) — держим в памяти только
    текущий и предыдущий снимок, не весь день. gap (с) — верхняя граница поиска
    снимка вообще (иначе no_book); max_age_ms — порог «свежий снимок», НИЖЕ него
    считается место лимитки (placement/taker), fact/model к mid считаются для
    любого возраста в пределах gap. Возвращает (сопоставлено, без_книги)."""
    by_date_code: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in orders:
        by_date_code[(o["file_date"], o["code"])].append(o)

    matched = no_book = 0
    by_date: dict[str, list[str]] = {}
    for date, code in sorted(by_date_code):
        paths = by_date.setdefault(date, book_paths_for_date(book_root, date))
        recs = sorted(by_date_code[(date, code)], key=lambda o: o["send_ts"])
        if not paths:
            no_book += len(recs)
            for o in recs:
                o["mid"] = o["best_bid"] = o["best_ask"] = o["model_vwap"] = None
                o["placement"] = o["book_age_ms"] = None
            continue

        book_iter = heapq.merge(*(_iter_book_lines(p, code) for p in paths), key=lambda t: t[0])
        prev = None
        cur = next(book_iter, None)
        for o in recs:
            ts = o["send_ts"] + MSK_SHIFT
            while cur is not None and cur[0] <= ts:
                prev = cur
                cur = next(book_iter, None)
            if prev is None or ts - prev[0] > gap:
                no_book += 1
                o["mid"] = o["best_bid"] = o["best_ask"] = o["model_vwap"] = None
                o["placement"] = o["book_age_ms"] = None
                continue
            snap_ts, bids, asks = prev
            age_ms = (ts - snap_ts) * 1000
            mid = (bids[0][0] + asks[0][0]) / 2
            model, _deep = BookRuntime._walk(asks if o["side"] == "SIDE_BUY" else bids, o["qty"])
            o["mid"], o["best_bid"], o["best_ask"], o["model_vwap"] = mid, bids[0][0], asks[0][0], model
            o["book_age_ms"] = age_ms
            o["placement"] = (placement_class(o["side"], o["send_price"], bids[0][0], asks[0][0])
                               if age_ms <= max_age_ms else None)
            matched += 1
        del book_iter, prev, cur
        gc.collect()
    return matched, no_book


def enrich(o: dict) -> None:
    """Пункты «хуже для нас», час/минута/день недели МСК, taker-флаг по месту
    размещения. IN PLACE, после match_book."""
    ts_msk_label = o["send_ts"] + MSK_SHIFT     # шкала бара: UTC-метка = московская стенка
    dt = datetime.fromtimestamp(ts_msk_label, timezone.utc)
    o["hour"], o["minute"] = dt.hour, dt.minute
    o["minute_class"] = minute_class(dt.hour, dt.minute)
    o["weekday"] = dt.strftime("%a")
    o["is_weekend"] = dt.weekday() >= 5
    sign = 1 if o["side"] == "SIDE_BUY" else -1
    if o["mid"] is not None and o["exec_price"] is not None:
        o["fact_vs_mid"] = sign * (o["exec_price"] - o["mid"])
        o["model_vs_mid"] = sign * (o["model_vwap"] - o["mid"])
    else:
        o["fact_vs_mid"] = o["model_vs_mid"] = None
    # мейкер/тейкер по месту размещения НА МОМЕНТ ОТПРАВКИ: маркетабельная лимитка
    # бьёт о встречный best сразу = тейкер; всё остальное ждёт своей очереди = мейкер
    # (commission.py: "лимитка простоявшая в стакане исполняется мейкером"). Место
    # неизвестно (снимок старше --max-age-ms) -> taker тоже неизвестен (None), а не
    # "не тейкер" — иначе стухший снимок молча превращается в мейкера.
    o["taker"] = None if o["placement"] is None else (o["placement"] == "маркетабельная")


def load_trade_vwap(trades_root: str, dates: set[str]) -> dict[str, tuple[float, int]]:
    """order_num -> (Σ qty*price, Σ qty) по data/trades/<дата>.jsonl за нужные даты
    + день следующий (вечерняя сессия датируется СЛЕДУЮЩИМ торговым днём — см.
    reference_journalsync_evening_replay.md). Файлы мелкие (десятки-сотни КБ),
    грузим целиком, отдельного стриминга не нужно. Дедуп по trade num — в архиве
    заявок видели задвоенные строки, на всякий случай не даём задвоить и тут."""
    all_dates = set()
    for d in dates:
        all_dates.add(d)
        all_dates.add((_date.fromisoformat(d) + timedelta(days=1)).isoformat())

    agg: dict[str, list] = defaultdict(lambda: [0.0, 0])
    seen_trade_num: set[str] = set()
    for d in sorted(all_dates):
        path = os.path.join(trades_root, f"{d}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                num, onum = r.get("num"), r.get("order_num")
                qty, price = r.get("qty"), r.get("price")
                if not onum or not qty or price is None:
                    continue
                if num and num in seen_trade_num:
                    continue
                if num:
                    seen_trade_num.add(num)
                s = agg[onum]
                s[0] += qty * price
                s[1] += qty
    return {k: (v[0], v[1]) for k, v in agg.items()}


def verify_against_trades(orders: list[dict], trades_root: str | None,
                           date_lo: str, date_hi: str) -> None:
    """Сверяет exec_price архива заявок (цена FILLED) с VWAP реальных сделок из
    журнала за order_num == order_id. IN PLACE: real_vwap, real_trade_qty,
    price_gap_pts = sign*(real_vwap - exec_price), знак «хуже для нас» (как
    fact_vs_mid): положительно = архив занижает реальные издержки."""
    if not trades_root:
        return
    window = [o for o in orders if date_lo <= o["file_date"] <= date_hi
              and o["order_id"] and o["exec_price"] is not None]
    if not window:
        return
    vwap_by_onum = load_trade_vwap(trades_root, {o["file_date"] for o in window})
    for o in window:
        s = vwap_by_onum.get(o["order_id"])
        if not s or s[1] <= 0:
            continue
        real_vwap = s[0] / s[1]
        sign = 1 if o["side"] == "SIDE_BUY" else -1
        o["real_vwap"] = real_vwap
        o["real_trade_qty"] = s[1]
        o["price_gap_pts"] = sign * (real_vwap - o["exec_price"])


def pctl(s: list[float], p: int) -> float:
    return s[max(0, min(len(s) - 1, p * len(s) // 100))]


def rub(x: float) -> str:
    return f"{x:+,.0f}".replace(",", " ")


def fmt_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def write_csv(orders: list[dict], path: str) -> None:
    cols = ["file_date", "code", "robot", "side", "qty", "send_ts", "send_price",
            "outcome", "filled_qty", "exec_ts", "exec_price", "exec_approx", "wait_s",
            "hour", "minute_class", "weekday", "is_weekend", "placement", "taker",
            "mid", "best_bid", "best_ask", "model_vwap", "fact_vs_mid", "model_vs_mid",
            "book_age_ms", "order_id", "real_vwap", "real_trade_qty", "price_gap_pts"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for o in orders:
            w.writerow(o)


def money_table(window: list[dict], codes: list[str],
                 point_values: dict[str, tuple[float, str]]) -> tuple[list[list], float, float]:
    rows = []
    tot_exec = tot_comm = 0.0
    for c in codes:
        part = [o for o in window if o["code"] == c and o["fact_vs_mid"] is not None]
        if not part:
            continue
        pv, src = point_values.get(c, (1.0, "?"))
        mean_pts = sum(o["fact_vs_mid"] for o in part) / len(part)
        # ₽ = пункты_хуже_mid * point_value * ОБЪЁМ КОНТРАКТОВ, не число заявок (см.
        # коммит a9527f3/предыдущий проход — иначе крупные заявки недосчитаны).
        exec_rub = pv * sum(o["fact_vs_mid"] * o["filled_qty"] for o in part)
        comm_rub = sum(
            commission_for(c, o["exec_price"], o["filled_qty"], pv, taker=bool(o["taker"]),
                            scalper=False, ts=o["send_ts"] + MSK_SHIFT)
            for o in part)
        tot_exec += exec_rub
        tot_comm += comm_rub
        rows.append([c, len(part), f"{mean_pts:+.1f}", f"{pv:.4f}", src, rub(exec_rub), rub(comm_rub)])
    rows.append(["ИТОГО", sum(1 for o in window if o["fact_vs_mid"] is not None), "", "", "",
                 rub(tot_exec), rub(tot_comm)])
    return rows, tot_exec, tot_comm


def build_report(orders: list[dict], point_values: dict[str, tuple[float, str]],
                  no_book: int, gap: int, max_age_ms: int,
                  verify_from: str, verify_to: str) -> str:
    codes = sorted({o["code"] for o in orders})
    filled = [o for o in orders if o["outcome"] == "filled"]
    priced_all = [o for o in filled if o["fact_vs_mid"] is not None]     # любой возраст в пределах gap
    fresh = [o for o in priced_all
             if o["book_age_ms"] is not None and o["book_age_ms"] <= max_age_ms]
    share_fresh = len(fresh) / len(priced_all) if priced_all else 0.0

    out = ["# Базовая линия издержек исполнения (шаг 0, v2)\n",
           f"Заявок всего {len(orders)}, кодов {len(codes)}: {', '.join(codes)}. "
           f"Без свежего стакана (порог {gap} с) {no_book} сопоставлений.\n",
           f"Возраст снимка на момент отправки <= {max_age_ms} мс: {len(fresh)}/{len(priced_all)} "
           f"филлов с ценой ({share_fresh:.0%}).\n"]

    # 1. судьба заявок по коду
    out.append("## 1. Судьба заявок по коду\n")
    rows = []
    for c in codes + ["ИТОГО"]:
        part = orders if c == "ИТОГО" else [o for o in orders if o["code"] == c]
        n = len(part)
        cnt = defaultdict(int)
        for o in part:
            cnt[o["outcome"]] += 1
        unfilled = 1 - cnt["filled"] / n if n else 0
        rows.append([c, n, cnt["filled"], cnt["partial_cancelled"] + cnt["partial_open"],
                     cnt["cancelled"], cnt["rejected"], cnt["active_eod"], f"{unfilled:.0%}"])
    out.append(fmt_table(
        ["код", "отправлено", "исполнено_полн", "частично(откр+отменён_остаток)",
         "отменено_полн", "отклонено", "активна_на_конец_архива", "доля_неисполн"], rows))

    w = sorted(o["wait_s"] for o in filled if o["wait_s"] is not None)
    if w:
        out.append(f"\nЗадержка PENDING->FILLED (только полные исполнения, n={len(w)}): "
                    f"медиана {median(w)*1000:.0f} мс, p90 {pctl(w,90)*1000:.0f} мс "
                    f"(разрешение исходных ts — секунды).\n")

    # 2. цена исполнения против mid и против модели, ТОЛЬКО свежий снимок (<= max_age_ms)
    out.append(f"\n## 2. Цена исполнения против mid / против модели "
                f"(полные исполнения, возраст снимка ≤ {max_age_ms} мс)\n")
    rows = []
    for c in codes:
        part = [o for o in fresh if o["code"] == c]
        if not part:
            continue
        fm = sorted(o["fact_vs_mid"] for o in part)
        mm = sorted(o["model_vs_mid"] for o in part)
        rows.append([c, len(part),
                     f"{median(fm):+.1f}", f"{sum(fm)/len(fm):+.1f}", f"{pctl(fm,90):+.1f}",
                     f"{median(mm):+.1f}", f"{sum(mm)/len(mm):+.1f}", f"{pctl(mm,90):+.1f}"])
    out.append(fmt_table(
        ["код", "филлов", "факт_медиана", "факт_среднее", "факт_p90",
         "модель_медиана", "модель_среднее", "модель_p90"], rows))
    out.append("\nПункты, знак «хуже для нас» (покупка: fill-mid, продажа: mid-fill).\n")

    # 2a. издержки по возрасту снимка — если растут с возрастом, это лаг, не робот
    out.append("\n## 2a. Факт к mid по возрасту снимка (любой возраст в пределах gap)\n")
    rows = []
    for _hi, label in AGE_BUCKETS:
        part = [o for o in priced_all if o["book_age_ms"] is not None and age_bucket(o["book_age_ms"]) == label]
        if part:
            v = sorted(o["fact_vs_mid"] for o in part)
            rows.append([label, len(v), f"{median(v):+.1f}", f"{sum(v)/len(v):+.1f}"])
    out.append(fmt_table(["возраст_снимка", "филлов", "медиана", "среднее"], rows))
    out.append("\nРост издержки с возрастом снимка = лаг снимка относительно заявки, не "
                "поведение робота (робот об этом не знает).\n")

    # 3. разрезы, ТОЛЬКО свежий снимок
    def cut_table(key_fn, label, order_keys=None):
        buckets = defaultdict(list)
        for o in fresh:
            buckets[key_fn(o)].append(o["fact_vs_mid"])
        keys = order_keys or sorted(buckets)
        rows = []
        for k in keys:
            v = sorted(buckets.get(k, []))
            if v:
                rows.append([k, len(v), f"{median(v):+.1f}", f"{sum(v)/len(v):+.1f}"])
        return f"\n### {label}\n" + fmt_table(["разрез", "филлов", "медиана", "среднее"], rows)

    out.append(f"\n## 3. Разрезы (факт к mid, возраст снимка ≤ {max_age_ms} мс)\n")
    out.append(cut_table(lambda o: f"{o['hour']:02d}", "по часу МСК",
                          [f"{h:02d}" for h in range(24)]))
    out.append(cut_table(lambda o: qty_bucket(o["qty"]), "по объёму заявки (лотов)",
                          [f"{lo}" if lo == hi else f"{lo}-{hi if hi<10**9 else '+'}"
                           for lo, hi in QTY_BUCKETS]))
    robots = sorted({o["robot"] for o in fresh}, key=lambda r: -sum(1 for o in fresh if o["robot"] == r))
    out.append(cut_table(lambda o: o["robot"], "по роботу", robots))
    out.append(cut_table(lambda o: o["side"], "по стороне", ["SIDE_BUY", "SIDE_SELL"]))
    out.append(cut_table(lambda o: o["minute_class"], "по классу минуты",
                          ["событие", "граница(:00/:30)", "прочее"]))
    wd_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    out.append(cut_table(lambda o: o["weekday"], "по дню недели (Sat/Sun = выходные)", wd_order))

    # 4. класс размещения (уже только возраст <= max_age_ms — иначе placement=None)
    out.append(f"\n## 4. Место лимитки в стакане на момент отправки (возраст снимка ≤ {max_age_ms} мс)\n")
    placed = [o for o in orders if o.get("placement")]
    cnt = defaultdict(int)
    qty_cnt = defaultdict(int)
    for o in placed:
        cnt[o["placement"]] += 1
        qty_cnt[o["placement"]] += o["qty"]
    rows = []
    tot_n, tot_q = len(placed), sum(o["qty"] for o in placed) or 1
    for cls in ("маркетабельная", "свой_best", "внутри_спреда", "глубже"):
        part = [o for o in fresh if o["placement"] == cls]
        fm = sorted(o["fact_vs_mid"] for o in part)
        rows.append([cls, cnt.get(cls, 0), f"{cnt.get(cls,0)/tot_n:.0%}" if tot_n else "-",
                     f"{qty_cnt.get(cls,0)/tot_q:.0%}" if tot_q else "-",
                     f"{median(fm):+.1f}" if fm else "-", len(fm)])
    out.append(fmt_table(
        ["класс", "заявок", "доля_заявок", "доля_объёма", "факт_к_mid_медиана", "филлов_в_классе"],
        rows))
    taker_known = [o for o in placed if o["taker"] is not None]
    if taker_known:
        taker_share = sum(1 for o in taker_known if o["taker"]) / len(taker_known)
        out.append(f"\nДоля тейкера (возраст снимка ≤ {max_age_ms} мс, n={len(taker_known)}): "
                    f"{taker_share:.0%}.\n")

    # 5. деньги: сентябрь, верхняя (любой возраст) / нижняя (только свежий снимок)
    out.append("\n## 5. Рубли в месяц (издержки исполнения vs комиссия), сентябрь\n")
    sept_start = "2026-09-01"
    window_sept = [o for o in filled if o["file_date"] >= sept_start]
    trading_dates = sorted({o["file_date"] for o in window_sept})
    n_trading = len(trading_dates)
    out.append(f"Данные архива за сентябрь: {trading_dates[0] if trading_dates else '-'} .. "
               f"{trading_dates[-1] if trading_dates else '-'}, дней с данными в архиве "
               f"{n_trading} (торговых), в календаре сентября 30.\n")

    for label, window in (
        ("ВЕРХНЯЯ (любой возраст снимка в пределах gap)", window_sept),
        (f"НИЖНЯЯ (возраст снимка ≤ {max_age_ms} мс)",
         [o for o in window_sept if o["book_age_ms"] is not None and o["book_age_ms"] <= max_age_ms]),
    ):
        rows, tot_exec, tot_comm = money_table(window, codes, point_values)
        out.append(f"\n### {label}\n")
        out.append(fmt_table(
            ["код", "филлов", "среднее_пт_к_mid", "₽/пт", "источник_₽/пт",
             "₽/мес_издержки_к_mid", "₽/мес_комиссия(сравнение)"], rows))
        if n_trading:
            out.append(f"\nФакт за {n_trading} торговых дней сентября: {rub(tot_exec)} ₽. "
                        f"Приведено к 30 календарным дням месяца: "
                        f"{rub(tot_exec * 30 / n_trading)} ₽.\n")
    out.append("\n«₽/мес_издержки_к_mid» = ₽/пт × Σ(пункты_хуже_mid × объём_заявки), НЕ "
                "среднее_пт_к_mid × ₽/пт × число_филлов — колонка «среднее» не взвешена по "
                "объёму. Комиссия: taker/maker по месту размещения (маркетабельная=тейкер, "
                "неизвестно (возраст>max-age-ms)=мейкер по умолчанию), БЕЗ скальперской "
                "скидки и без отдельного учёта выходных (модель уже удваивает по ts филла).\n")

    # 6. сверка exec_price архива с журналом сделок
    out.append(f"\n## 6. Сверка exec_price архива с журналом сделок ({verify_from}..{verify_to})\n")
    vwindow = [o for o in orders if verify_from <= o["file_date"] <= verify_to
               and o["order_id"] and o["exec_price"] is not None]
    vmatched = [o for o in vwindow if o["price_gap_pts"] is not None]
    eps = 1e-6
    exact = [o for o in vwindow if abs(o["exec_price"] - o["send_price"]) < eps]
    exact_matched = [o for o in exact if o["price_gap_pts"] is not None]

    def gap_stats(rows: list[dict], label: str) -> str:
        if not rows:
            return f"{label}: сопоставлений с журналом сделок нет.\n"
        diffs = sorted(o["price_gap_pts"] for o in rows)
        differ = sum(1 for d in diffs if abs(d) > eps)
        return (f"{label}: сопоставлено {len(rows)}, различаются {differ} "
                f"({differ/len(rows):.0%}), пункты real_vwap-exec_price (знак «хуже для "
                f"нас»): медиана {median(diffs):+.1f}, среднее {sum(diffs)/len(diffs):+.1f}.\n")

    if not vwindow:
        out.append("Заявок с исполнением и order_id в окне нет (или --trades не задан).\n")
    else:
        out.append(f"Заявок в окне с исполнением и order_id: {len(vwindow)}, найдено в журнале "
                    f"сделок: {len(vmatched)} ({len(vmatched)/len(vwindow):.0%}).\n\n")
        out.append(gap_stats(vmatched, "Все сопоставленные"))
        out.append(gap_stats(exact_matched,
                              f"Из них «ровно по цене заявки» (exec_price==send_price, "
                              f"всего в окне {len(exact)}, из них в журнале {len(exact_matched)})"))
    return "\n".join(out)


def _selftest() -> None:
    """Проверка классификации/сопоставления на синтетических файлах того же формата,
    что архив (без сети и БД): исполнена / частично-снята / отклонена / нет книги,
    место лимитки в стакане, знак пунктов, возраст снимка (гейт placement/taker на
    max_age_ms при fact/model посчитанных для любого возраста), сверка exec_price с
    журналом сделок. Нетривиальная логика без теста — не готова; запуск:
    python scripts/exec_baseline.py --selftest."""
    import json as _json
    import tempfile

    base = 1700000000
    with tempfile.TemporaryDirectory() as tmp:
        orders_ln = [
            # A: BUY 2 @100, книга best_bid=100/best_ask=101 на момент отправки (age
            # 1 с, свежий) -> свой_best, filled по 100 (лучше mid 100.5 для нас), и
            # exec_price==send_price (класс "ровно по цене заявки" из сверки с
            # журналом сделок)
            {"client_id": "rr:robotA:1:a1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_PENDING", "price": 100.0, "quantity": "2",
             "ts_unix_ms": str((base + 1) * 1000)},
            {"client_id": "rr:robotA:1:a1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_FILLED", "price": 100.0, "quantity": "2",
             "filled": "2", "order_id": "oid-a1", "ts_unix_ms": str((base + 3) * 1000)},
            # B: SELL 6 @110, книга выше ask -> глубже; частично 3, остаток снят
            {"client_id": "rr:robotA:2:b1", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_PENDING", "price": 110.0, "quantity": "6",
             "ts_unix_ms": str((base + 1) * 1000)},
            {"client_id": "rr:robotA:2:b1", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_PARTIAL", "price": 110.0, "quantity": "6",
             "filled": "3", "order_id": "oid-b1", "ts_unix_ms": str((base + 5) * 1000)},
            {"client_id": "rr:robotA:2:b1", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_CANCELLED", "price": 110.0, "quantity": "6",
             "filled": "3", "ts_unix_ms": str((base + 9) * 1000)},
            # C: BUY 1 @90, отклонена биржей целиком
            {"client_id": "rr:robotA:3:c1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_PENDING", "price": 90.0, "quantity": "1",
             "ts_unix_ms": str((base + 1) * 1000)},
            {"client_id": "rr:robotA:3:c1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_REJECTED", "price": 90.0, "quantity": "1",
             "ts_unix_ms": str((base + 2) * 1000)},
            # E: BUY 1 @100, тот же снимок что A, но отправлена на 10с позже ->
            # возраст снимка 10с > max_age_ms(3с): placement/taker неизвестны (None),
            # а fact_vs_mid/model_vs_mid всё равно считаются (раздел 2a)
            {"client_id": "rr:robotA:5:e1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_PENDING", "price": 100.0, "quantity": "1",
             "ts_unix_ms": str((base + 10) * 1000)},
            {"client_id": "rr:robotA:5:e1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_FILLED", "price": 100.0, "quantity": "1",
             "filled": "1", "order_id": "oid-e1", "ts_unix_ms": str((base + 11) * 1000)},
            # D: smart-order, исполнена, но книги для её дня в архиве нет
            {"client_id": "so:deadbeef01", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_PENDING", "price": 95.0, "quantity": "4",
             "ts_unix_ms": str((base + 90000) * 1000)},
            {"client_id": "so:deadbeef01", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_FILLED", "price": 94.0, "quantity": "4",
             "filled": "4", "order_id": "oid-d1", "ts_unix_ms": str((base + 90002) * 1000)},
        ]
        with open(os.path.join(tmp, "order-2026-01-01.jsonl"), "w") as f:
            for r in orders_ln:
                f.write(_json.dumps(r) + "\n")
        book_ln = {"code": "RIU6", "received_at_unix_ms": base * 1000,
                   "bids": [{"price": 100.0, "quantity": 5}, {"price": 99.0, "quantity": 5}],
                   "asks": [{"price": 101.0, "quantity": 5}, {"price": 102.0, "quantity": 5}]}
        with open(os.path.join(tmp, "book-2026-01-01.jsonl"), "w") as f:
            f.write(_json.dumps(book_ln) + "\n")
        # день D (90000с позже = другая календарная дата UTC) остаётся без book-файла

        # журнал сделок: order_num == order_id FILLED. A: реальный VWAP 100.5 (лучше
        # заявленных 100.0 для нас на стороне покупки, т.е. архив занижает издержки
        # на 0.5 пт по знаку "хуже для нас"). E: не встречается в журнале вовсе.
        trades_dir = os.path.join(tmp, "trades")
        os.makedirs(trades_dir)
        with open(os.path.join(trades_dir, "2026-01-01.jsonl"), "w") as f:
            f.write(_json.dumps({"num": "t1", "ts_ms": (base + 3) * 1000, "sec": "RIU6",
                                  "side": "buy", "qty": 1, "price": 100.0,
                                  "order_num": "oid-a1"}) + "\n")
            f.write(_json.dumps({"num": "t2", "ts_ms": (base + 3) * 1000, "sec": "RIU6",
                                  "side": "buy", "qty": 1, "price": 101.0,
                                  "order_num": "oid-a1"}) + "\n")
            f.write(_json.dumps({"num": "t2", "ts_ms": (base + 3) * 1000, "sec": "RIU6",
                                  "side": "buy", "qty": 1, "price": 101.0,
                                  "order_num": "oid-a1"}) + "\n")  # дубль trade num -> не задвоить

        orders = build_orders(tmp)
        by_cid = {o["client_id"]: o for o in orders}
        assert len(orders) == 5, orders
        assert by_cid["rr:robotA:1:a1"]["outcome"] == "filled"
        assert by_cid["rr:robotA:1:a1"]["order_id"] == "oid-a1"
        assert by_cid["rr:robotA:2:b1"]["outcome"] == "partial_cancelled"
        assert by_cid["rr:robotA:2:b1"]["filled_qty"] == 3
        assert by_cid["rr:robotA:3:c1"]["outcome"] == "rejected"
        assert by_cid["rr:robotA:3:c1"]["filled_qty"] == 0
        assert by_cid["rr:robotA:3:c1"]["order_id"] is None    # REJECTED без order_id
        assert by_cid["so:deadbeef01"]["robot"] == "smart_order"
        assert by_cid["rr:robotA:1:a1"]["robot"] == "robotA"

        matched, no_book = match_book(orders, tmp, gap=60, max_age_ms=3000)
        assert matched == 4, matched          # A, B, C, E сопоставлены (книга нужна любой заявке)
        assert no_book == 1, no_book          # D: файла книги под её дату нет

        for o in orders:
            enrich(o)
        a1 = by_cid["rr:robotA:1:a1"]
        assert a1["book_age_ms"] == 1000, a1["book_age_ms"]        # снимок base, отправка base+1
        assert a1["placement"] == "свой_best", a1["placement"]
        assert abs(a1["fact_vs_mid"] - (-0.5)) < 1e-9, a1["fact_vs_mid"]
        assert abs(a1["model_vs_mid"] - 0.5) < 1e-9, a1["model_vs_mid"]
        assert a1["taker"] is False
        b1 = by_cid["rr:robotA:2:b1"]
        assert b1["placement"] == "глубже", b1["placement"]
        e1 = by_cid["rr:robotA:5:e1"]
        assert e1["book_age_ms"] == 10000, e1["book_age_ms"]       # снимок base, отправка base+10
        assert e1["placement"] is None, e1["placement"]            # возраст > max_age_ms(3000)
        assert e1["taker"] is None, e1["taker"]
        assert e1["fact_vs_mid"] is not None                       # но факт к mid всё равно есть
        d1 = by_cid["so:deadbeef01"]
        assert d1["fact_vs_mid"] is None        # без книги пункты не считаем

        verify_against_trades(orders, trades_dir, "2026-01-01", "2026-01-01")
        assert abs(a1["real_vwap"] - 100.5) < 1e-9, a1["real_vwap"]
        assert a1["real_trade_qty"] == 2, a1["real_trade_qty"]      # дубль trade num не задвоен
        assert abs(a1["price_gap_pts"] - 0.5) < 1e-9, a1["price_gap_pts"]   # BUY: sign*(100.5-100.0)
        assert e1["real_vwap"] is None          # order_id не встречается в журнале

        write_csv(orders, os.path.join(tmp, "out", "fills.csv"))
        assert os.path.getsize(os.path.join(tmp, "out", "fills.csv")) > 0

        report = build_report(orders, {"RIU6": (1.0, "тест")}, no_book, gap=60,
                               max_age_ms=3000, verify_from="2026-01-01", verify_to="2026-01-01")
        assert "## 6. Сверка exec_price" in report
        assert "## 2a. Факт к mid по возрасту снимка" in report
    print("SELFTEST OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--orders")
    ap.add_argument("--book")
    ap.add_argument("--trades", help="корень data/trades/<дата>.jsonl для сверки exec_price (раздел 6)")
    ap.add_argument("--gap", type=int, default=60,
                     help="верхняя граница поиска снимка вообще, секунды (иначе no_book)")
    ap.add_argument("--max-age-ms", type=int, default=3000,
                     help="порог «свежий снимок»: ниже него считаем место лимитки/taker "
                          "и разрезы раздела 3; было 60000 (60 с) в v1")
    ap.add_argument("--verify-from", default="2026-09-23")
    ap.add_argument("--verify-to", default="2026-09-30")
    ap.add_argument("--out-csv")
    ap.add_argument("--out-report")
    a = ap.parse_args()

    if a.selftest:
        _selftest()
        return
    if not a.orders or not a.book or not a.out_csv or not a.out_report:
        ap.error("--orders/--book/--out-csv/--out-report обязательны без --selftest")

    print("читаю заявки...", flush=True)
    orders = build_orders(a.orders)
    print(f"заявок с PENDING: {len(orders)}", flush=True)

    print("сопоставляю со стаканом (потоково, по дню и коду, два курсора)...", flush=True)
    matched, no_book = match_book(orders, a.book, a.gap, a.max_age_ms)
    print(f"сопоставлено {matched}, без книги {no_book}", flush=True)

    for o in orders:
        enrich(o)

    if a.trades:
        print(f"сверяю exec_price с журналом сделок {a.verify_from}..{a.verify_to}...", flush=True)
        verify_against_trades(orders, a.trades, a.verify_from, a.verify_to)

    codes = {o["code"] for o in orders}
    print(f"читаю point_value из instrument_meta для {sorted(codes)}...", flush=True)
    point_values = asyncio.run(load_point_values(codes))
    for c, (pv, src) in point_values.items():
        print(f"  {c}: {pv:.4f} ₽/пт ({src})")

    write_csv(orders, a.out_csv)
    report = build_report(orders, point_values, no_book, a.gap, a.max_age_ms,
                           a.verify_from, a.verify_to)
    os.makedirs(os.path.dirname(a.out_report), exist_ok=True)
    with open(a.out_report, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"CSV: {a.out_csv}\nОтчёт: {a.out_report}")


async def load_point_values(codes: set[str]) -> dict[str, tuple[float, str]]:
    """code -> (₽/пункт, источник). Источник: instrument_meta в БД STL — зеркало
    MOEX ISS (price_step_value/price_step), см. trader/lab/market_store.py
    refresh_instrument_spec + trader/api/app.py _point_value_of. Код без записи
    (напр. истёкший/редкий контракт) получает ближайший по тикеру, самый свежий по
    updated_at — приближение, помечается явно."""
    db_url = os.environ.get("LAB_DB_URL")
    if not db_url:
        return {c: (1.0, "LAB_DB_URL не задан, заглушка 1.0") for c in codes}
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                "SELECT symbol, point_value, updated_at FROM instrument_meta "
                "WHERE point_value IS NOT NULL")
        finally:
            await conn.close()
    except Exception as exc:  # БД недоступна - не блокирует остальной отчёт
        return {c: (1.0, f"instrument_meta недоступна ({exc}), заглушка 1.0") for c in codes}

    out: dict[str, tuple[float, str]] = {}
    for c in codes:
        row = next((r for r in rows if r["symbol"] == c), None)
        if row:
            out[c] = (float(row["point_value"]), "instrument_meta")
            continue
        base = c[:2]
        cands = [r for r in rows if r["symbol"][:2] == base]
        if cands:
            best = max(cands, key=lambda r: r["updated_at"])
            out[c] = (float(best["point_value"]), f"приближение по {best['symbol']}")
        else:
            out[c] = (1.0, "нет в instrument_meta, заглушка 1.0")
    return out


if __name__ == "__main__":
    main()
