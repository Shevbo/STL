"""Базовая линия издержек исполнения по НАШИМ ЖИВЫМ заявкам, шаг 0 программы
docs/execution-cost-program.md (30.09.2026): за один проход по всем дням архива и
всем кодам — судьба каждой заявки (исполнена/частично/снята/отклонена), задержка
PENDING->FILLED, цена филла против mid и против модели рыночной заявки (VWAP по
встречной стороне, trader/lab/book_replay.BookRuntime._walk), разрезы по часу,
объёму, роботу, стороне, классу минуты, дню недели и месту лимитки в стакане на
момент отправки, и перевод в рубли/месяц через point_value из instrument_meta (БД
STL, зеркало MOEX ISS — см. load_point_values).

Переиспользует fills_vs_model._iter_order_events (парсер order-*.jsonl(.gz)) и
trader.lab.book_replay (парсер book-*.jsonl(.gz), MSK_SHIFT, VWAP-модель), поэтому
сам не парсит архив заново — только классифицирует и агрегирует.

ПАМЯТЬ. Один день стакана может весить сотню мегабайт в распакованном виде.
Обрабатываем ПО ОДНОМУ ДНЮ (и по одному коду внутри дня): грузим book-<дата>(-
recovered) только для кода, заявки которого есть в этот день, сопоставляем, затем
явно освобождаем (del + gc.collect()) перед следующим днём/кодом — never load_dir
на весь каталог сразу.

Запуск (на хостере, под nice):
    cd ~/apps/shectory-trader && PYTHONPATH=. nice -n 19 \
      $(ls -d ~/.cache/pypoetry/virtualenvs/shectory-trader-*/bin/python | head -1) \
      scripts/exec_baseline.py --orders ~/market-archive --book ~/market-archive \
      --out-csv /tmp/fills.csv --out-report /tmp/baseline.md
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import csv
import gc
import os
import sys
from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone
from statistics import median

sys.path.insert(0, os.path.dirname(__file__))  # bare same-dir import, см. scripts/rf_gap.py
from fills_vs_model import _iter_order_events  # noqa: E402

from trader.lab.book_replay import MSK_SHIFT, BookRuntime, load_book  # noqa: E402
from trader.lab.commission import commission_for, is_weekend  # noqa: E402

TERMINAL_OUTCOME = {
    "ORDER_STATE_FILLED": "filled",
    "ORDER_STATE_PARTIAL": "partial_open",   # архив кончился, остаток ещё в стакане
    "ORDER_STATE_ACTIVE": "active_eod",      # то же для неисполненной лимитки
}
QTY_BUCKETS = ((1, 1), (2, 5), (6, 10), (11, 10**9))
EVENT_MINUTES = {(10, 0), (15, 30), (16, 30), (17, 0), (23, 0)}


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

        orders.append({
            "file_date": pend["file_date"], "code": pend["code"], "client_id": cid,
            "robot": robot_tag(cid), "side": pend["side"], "qty": pend["qty"],
            "send_ts": pend["ts"], "send_price": pend["price"],
            "outcome": outcome, "filled_qty": filled_qty,
            "exec_ts": exec_ts, "exec_price": exec_px, "exec_approx": exec_approx,
            "wait_s": (exec_ts - pend["ts"]) if exec_ts is not None else None,
            "terminal_state": terminal["state"],
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


def match_book(orders: list[dict], book_root: str, gap: int) -> tuple[int, int]:
    """Дополняет каждую запись стаканными полями IN PLACE, по одному (дата, код) за
    раз, освобождая книгу перед следующей парой. Возвращает (сопоставлено, без_книги)."""
    by_date_code: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in orders:
        by_date_code[(o["file_date"], o["code"])].append(o)

    matched = no_book = 0
    by_date: dict[str, list[str]] = {}
    for date, code in sorted(by_date_code):
        paths = by_date.setdefault(date, book_paths_for_date(book_root, date))
        recs = by_date_code[(date, code)]
        if not paths:
            no_book += len(recs)
            for o in recs:
                o["mid"] = o["best_bid"] = o["best_ask"] = o["model_vwap"] = None
                o["placement"] = None
            continue
        times, books = load_book(paths, code)
        for o in recs:
            ts = o["send_ts"] + MSK_SHIFT
            i = bisect.bisect_right(times, ts) - 1   # снимок ДО отправки, см. fills_vs_model.py
            if i < 0 or ts - times[i] > gap:
                no_book += 1
                o["mid"] = o["best_bid"] = o["best_ask"] = o["model_vwap"] = None
                o["placement"] = None
                continue
            bids, asks = books[i]
            mid = (bids[0][0] + asks[0][0]) / 2
            model, _deep = BookRuntime._walk(asks if o["side"] == "SIDE_BUY" else bids, o["qty"])
            o["mid"], o["best_bid"], o["best_ask"], o["model_vwap"] = mid, bids[0][0], asks[0][0], model
            o["placement"] = placement_class(o["side"], o["send_price"], bids[0][0], asks[0][0])
            matched += 1
        del times, books
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
    # (commission.py: "лимитка простоявшая в стакане исполняется мейкером").
    o["taker"] = o["placement"] == "маркетабельная"


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


def pctl(s: list[float], p: int) -> float:
    return s[max(0, min(len(s) - 1, p * len(s) // 100))]


def fmt_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def write_csv(orders: list[dict], path: str) -> None:
    cols = ["file_date", "code", "robot", "side", "qty", "send_ts", "send_price",
            "outcome", "filled_qty", "exec_ts", "exec_price", "exec_approx", "wait_s",
            "hour", "minute_class", "weekday", "is_weekend", "placement", "taker",
            "mid", "best_bid", "best_ask", "model_vwap", "fact_vs_mid", "model_vs_mid"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for o in orders:
            w.writerow(o)


def build_report(orders: list[dict], point_values: dict[str, tuple[float, str]],
                  no_book: int, gap: int) -> str:
    codes = sorted({o["code"] for o in orders})
    filled = [o for o in orders if o["outcome"] == "filled"]
    priced = [o for o in filled if o["fact_vs_mid"] is not None]
    out = ["# Базовая линия издержек исполнения (шаг 0)\n",
           f"Заявок всего {len(orders)}, кодов {len(codes)}: {', '.join(codes)}. "
           f"Без свежего стакана (порог {gap} с) {no_book} сопоставлений.\n"]

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

    # 2. цена исполнения против mid и против модели
    out.append("\n## 2. Цена исполнения против mid / против модели (только полные исполнения)\n")
    rows = []
    for c in codes:
        part = [o for o in priced if o["code"] == c]
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

    # 3. разрезы
    def cut_table(key_fn, label, order_keys=None):
        buckets = defaultdict(list)
        for o in priced:
            buckets[key_fn(o)].append(o["fact_vs_mid"])
        keys = order_keys or sorted(buckets)
        rows = []
        for k in keys:
            v = sorted(buckets.get(k, []))
            if v:
                rows.append([k, len(v), f"{median(v):+.1f}", f"{sum(v)/len(v):+.1f}"])
        return f"\n### {label}\n" + fmt_table(["разрез", "филлов", "медиана", "среднее"], rows)

    out.append("\n## 3. Разрезы (факт к mid, только полные исполнения)\n")
    out.append(cut_table(lambda o: f"{o['hour']:02d}", "по часу МСК",
                          [f"{h:02d}" for h in range(24)]))
    out.append(cut_table(lambda o: qty_bucket(o["qty"]), "по объёму заявки (лотов)",
                          [f"{lo}" if lo == hi else f"{lo}-{hi if hi<10**9 else '+'}"
                           for lo, hi in QTY_BUCKETS]))
    robots = sorted({o["robot"] for o in priced}, key=lambda r: -sum(1 for o in priced if o["robot"] == r))
    out.append(cut_table(lambda o: o["robot"], "по роботу", robots))
    out.append(cut_table(lambda o: o["side"], "по стороне", ["SIDE_BUY", "SIDE_SELL"]))
    out.append(cut_table(lambda o: o["minute_class"], "по классу минуты",
                          ["событие", "граница(:00/:30)", "прочее"]))
    wd_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    out.append(cut_table(lambda o: o["weekday"], "по дню недели (Sat/Sun = выходные)", wd_order))

    # 4. класс размещения
    out.append("\n## 4. Место лимитки в стакане на момент отправки\n")
    placed = [o for o in orders if o.get("placement")]
    cnt = defaultdict(int)
    qty_cnt = defaultdict(int)
    for o in placed:
        cnt[o["placement"]] += 1
        qty_cnt[o["placement"]] += o["qty"]
    rows = []
    tot_n, tot_q = len(placed), sum(o["qty"] for o in placed) or 1
    for cls in ("маркетабельная", "свой_best", "внутри_спреда", "глубже"):
        part = [o for o in priced if o["placement"] == cls]
        fm = sorted(o["fact_vs_mid"] for o in part)
        rows.append([cls, cnt.get(cls, 0), f"{cnt.get(cls,0)/tot_n:.0%}",
                     f"{qty_cnt.get(cls,0)/tot_q:.0%}",
                     f"{median(fm):+.1f}" if fm else "-", len(fm)])
    out.append(fmt_table(
        ["класс", "заявок", "доля_заявок", "доля_объёма", "факт_к_mid_медиана", "филлов_в_классе"],
        rows))

    # 5. деньги
    out.append("\n## 5. Рубли в месяц (издержки исполнения vs комиссия)\n")
    dates = sorted({o["file_date"] for o in orders})
    last_d = _date.fromisoformat(dates[-1])
    cutoff = (last_d - timedelta(days=29)).isoformat()
    window = [o for o in filled if o["file_date"] >= cutoff]
    out.append(f"Окно «последние 30 дней архива»: {cutoff} .. {dates[-1]} "
               f"({sum(1 for _ in window)} полных исполнений).\n")
    rows = []
    tot_exec = tot_comm = 0.0
    for c in codes:
        part = [o for o in window if o["code"] == c and o["fact_vs_mid"] is not None]
        if not part:
            continue
        pv, src = point_values.get(c, (1.0, "?"))
        mean_pts = sum(o["fact_vs_mid"] for o in part) / len(part)
        # ₽ = пункты_хуже_mid * point_value * ОБЪЁМ КОНТРАКТОВ, не число заявок: заявка
        # на N контрактов теряет N*fact_vs_mid пунктов, не fact_vs_mid один раз на
        # заявку (проверено fable 30.09.2026 — mean_pts*pv*len(part) занижал RIU6 в
        # 4.24 раза, т.к. крупные заявки скользят сильнее мелких, а не пропорционально
        # среднему объёму).
        exec_rub_month = pv * sum(o["fact_vs_mid"] * o["filled_qty"] for o in part)
        comm_rub_month = sum(
            commission_for(c, o["exec_price"], o["filled_qty"], pv, taker=o["taker"],
                            scalper=False, ts=o["send_ts"] + MSK_SHIFT)
            for o in part)
        tot_exec += exec_rub_month
        tot_comm += comm_rub_month
        rows.append([c, len(part), f"{mean_pts:+.1f}", f"{pv:.4f}", src,
                     f"{exec_rub_month:+,.0f}".replace(",", " "),
                     f"{comm_rub_month:,.0f}".replace(",", " ")])
    rows.append(["ИТОГО", sum(1 for o in window if o["fact_vs_mid"] is not None), "", "", "",
                 f"{tot_exec:+,.0f}".replace(",", " "), f"{tot_comm:,.0f}".replace(",", " ")])
    out.append(fmt_table(
        ["код", "филлов_30д", "среднее_пт_к_mid", "₽/пт", "источник_₽/пт",
         "₽/мес_издержки_к_mid", "₽/мес_комиссия(сравнение)"], rows))
    out.append("\n«₽/мес_издержки_к_mid» = ₽/пт × Σ(пункты_хуже_mid × объём_заявки), НЕ "
                "среднее_пт_к_mid × ₽/пт × число_филлов — колонка «среднее» не взвешена по "
                "объёму и для прикидки в рублях не годится, крупные заявки скользят сильнее "
                "мелких (см. коммит).\n"
                "Комиссия: taker/maker взят по классу размещения (маркетабельная=тейкер, "
                "иначе мейкер), БЕЗ скальперской скидки (нужна реконструкция позиции по "
                "роботу — вне этого прохода) и без учёта выходных отдельно от будней (модель "
                "их уже удваивает по ts самого филла).\n")
    return "\n".join(out)


def _selftest() -> None:
    """Проверка классификации/сопоставления на синтетических файлах того же формата,
    что архив (без сети и БД): исполнена / частично-снята / отклонена / нет книги,
    место лимитки в стакане, знак пунктов. Нетривиальная логика без теста — не
    готова; запуск: python scripts/exec_baseline.py --selftest."""
    import json as _json
    import tempfile

    base = 1700000000
    with tempfile.TemporaryDirectory() as tmp:
        orders_ln = [
            # A: BUY 2 @100, книга best_bid=100/best_ask=101 на момент отправки ->
            # свой_best, filled по 100 (лучше mid 100.5 на 0.5 для нас)
            {"client_id": "rr:robotA:1:a1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_PENDING", "price": 100.0, "quantity": "2",
             "ts_unix_ms": str((base + 1) * 1000)},
            {"client_id": "rr:robotA:1:a1", "code": "RIU6", "side": "SIDE_BUY",
             "state": "ORDER_STATE_FILLED", "price": 100.0, "quantity": "2",
             "filled": "2", "ts_unix_ms": str((base + 3) * 1000)},
            # B: SELL 6 @110, книга выше ask -> глубже; частично 3, остаток снят
            {"client_id": "rr:robotA:2:b1", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_PENDING", "price": 110.0, "quantity": "6",
             "ts_unix_ms": str((base + 1) * 1000)},
            {"client_id": "rr:robotA:2:b1", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_PARTIAL", "price": 110.0, "quantity": "6",
             "filled": "3", "ts_unix_ms": str((base + 5) * 1000)},
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
            # D: smart-order, исполнена, но книги для её дня в архиве нет
            {"client_id": "so:deadbeef01", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_PENDING", "price": 95.0, "quantity": "4",
             "ts_unix_ms": str((base + 90000) * 1000)},
            {"client_id": "so:deadbeef01", "code": "RIU6", "side": "SIDE_SELL",
             "state": "ORDER_STATE_FILLED", "price": 94.0, "quantity": "4",
             "filled": "4", "ts_unix_ms": str((base + 90002) * 1000)},
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

        orders = build_orders(tmp)
        by_cid = {o["client_id"]: o for o in orders}
        assert len(orders) == 4, orders
        assert by_cid["rr:robotA:1:a1"]["outcome"] == "filled"
        assert by_cid["rr:robotA:2:b1"]["outcome"] == "partial_cancelled"
        assert by_cid["rr:robotA:2:b1"]["filled_qty"] == 3
        assert by_cid["rr:robotA:3:c1"]["outcome"] == "rejected"
        assert by_cid["rr:robotA:3:c1"]["filled_qty"] == 0
        assert by_cid["so:deadbeef01"]["robot"] == "smart_order"
        assert by_cid["rr:robotA:1:a1"]["robot"] == "robotA"

        matched, no_book = match_book(orders, tmp, gap=60)
        assert matched == 3, matched          # A, B, C сопоставлены (книга нужна любой заявке)
        assert no_book == 1, no_book          # D: файла книги под её дату нет

        for o in orders:
            enrich(o)
        a1 = by_cid["rr:robotA:1:a1"]
        assert a1["placement"] == "свой_best", a1["placement"]
        assert abs(a1["fact_vs_mid"] - (-0.5)) < 1e-9, a1["fact_vs_mid"]
        assert abs(a1["model_vs_mid"] - 0.5) < 1e-9, a1["model_vs_mid"]
        assert a1["taker"] is False
        b1 = by_cid["rr:robotA:2:b1"]
        assert b1["placement"] == "глубже", b1["placement"]
        d1 = by_cid["so:deadbeef01"]
        assert d1["fact_vs_mid"] is None        # без книги пункты не считаем

        write_csv(orders, os.path.join(tmp, "out", "fills.csv"))
        assert os.path.getsize(os.path.join(tmp, "out", "fills.csv")) > 0
    print("SELFTEST OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--orders")
    ap.add_argument("--book")
    ap.add_argument("--gap", type=int, default=60)
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

    print("сопоставляю со стаканом (по дню и коду, с освобождением памяти)...", flush=True)
    matched, no_book = match_book(orders, a.book, a.gap)
    print(f"сопоставлено {matched}, без книги {no_book}", flush=True)

    for o in orders:
        enrich(o)

    codes = {o["code"] for o in orders}
    print(f"читаю point_value из instrument_meta для {sorted(codes)}...", flush=True)
    point_values = asyncio.run(load_point_values(codes))
    for c, (pv, src) in point_values.items():
        print(f"  {c}: {pv:.4f} ₽/пт ({src})")

    write_csv(orders, a.out_csv)
    report = build_report(orders, point_values, no_book, a.gap)
    os.makedirs(os.path.dirname(a.out_report), exist_ok=True)
    with open(a.out_report, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"CSV: {a.out_csv}\nОтчёт: {a.out_report}")


if __name__ == "__main__":
    main()
