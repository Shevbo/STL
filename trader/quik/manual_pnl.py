"""Доходность РУЧНОЙ торговли оператора за период: день, неделя, месяц.

Ручное — это всё, что не робот: дети умных заявок (brokerref `stl-so-*`) и сделки,
сделанные руками в терминале QUIK (тег пустой). Роботы считаются отдельно, в
algo_trades, и смешивать их сюда нельзя — иначе «итог ручных» будет мерить чужую
работу.

МЕТОДИКА — та же, по которой живёт журнал алготорговли: сведение сделок по средней
цене (`algo_ledger.apply_fill`), реализация приписывается ЗАКРЫВАЮЩЕЙ сделке, а
открытый остаток показывается ОТДЕЛЬНОЙ строкой и в итог не входит. Две причины так:
единая арифметика во всей системе (иначе два экрана спорят о деньгах), и честность —
пока позиция открыта, её результат не деньги, а переоценка, которая ещё изменится.

Источник фактов — журнал сделок `data/trades/YYYY-MM-DD.jsonl`, который пишет
trader/quik/truth.py. Он начат 23.09.2026, поэтому за «месяц» отдаётся ещё и
`coverage_from`: экран обязан сказать, с какой даты данные вообще есть, а не
выдавать неполный период за полный.

КОМИССИЯ — ОЦЕНКА. QUIK в таблице сделок комиссию не отдаёт, поэтому берётся модель
trader/lab/commission (биржевой сбор + брокер, скальперская скидка на закрывающей
ноге внутри дня, удвоение в выходные). Это оценка сверху: реальная маркетная скидка
может быть больше. Отдаётся отдельным числом, а не молча внутри итога.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from trader.lab.commission import commission_for
from trader.quik.algo_ledger import apply_fill, msk_date
from trader.quik.truth import channel as tag_channel

TRADES_DIR = "data/trades"
MSK = datetime.timezone(datetime.timedelta(hours=3))
PERIODS = ("day", "week", "month")
# Три канала, которыми торгует оператор, — так их называет он сам: терминал QUIK,
# приложение брокера и умные заявки STL. Робот и выравнивание ручными не являются.
MANUAL_CHANNELS = ("quik", "broker", "smart")


def period_days(period: str, today: datetime.date) -> list[str]:
    """Даты МСК, входящие в период. Неделя — последние 7 дней включая сегодня,
    месяц — последние 30: календарные границы («с первого числа») на вопрос
    «сколько я заработал за месяц» отвечают хуже, чем скользящее окно."""
    span = {"day": 1, "week": 7, "month": 30}.get(period, 1)
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(span - 1, -1, -1)]


def read_trades(days: list[str], directory: str | None = None,
                robot_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Ручные сделки за перечисленные дни, по времени, с проставленным каналом.

    КАНАЛ СЧИТАЕТСЯ ЗАНОВО, из тега, а записанному в строке `owner` доверия нет:
    журнал пишется в реальном времени, и строки, записанные до исправления
    классификации (23.09.2026), несут прежний ответ. Тег — факт от QUIK,
    классификация — наше суждение о нём, и пересматривать его задним числом
    можно, а переписывать факт нельзя."""
    directory = directory or TRADES_DIR
    out: list[dict[str, Any]] = []
    for day in days:
        path = os.path.join(directory, f"{day}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ch = tag_channel(row.get("tag"), robot_ids)
                if ch in MANUAL_CHANNELS:
                    out.append({**row, "channel": ch})
    out.sort(key=lambda r: int(r.get("ts_ms") or 0))
    return out


def coverage_from(directory: str | None = None) -> str:
    try:
        days = sorted(f[:-6] for f in os.listdir(directory or TRADES_DIR)
                      if f.endswith(".jsonl"))
    except OSError:
        return ""
    return days[0] if days else ""


def summarize(trades: list[dict[str, Any]], point_values: dict[str, float],
              last_prices: dict[str, float] | None = None) -> dict[str, Any]:
    """Свести ручные сделки в итог: закрытое (деньги) и открытое (переоценка).

    `point_values` — ₽ за пункт по инструменту (algo_ledger.point_values). Без него
    результат остаётся в ПУНКТАХ и деньгами не притворяется: пункт не рубль, и
    домножать на единицу в денежном пути запрещено."""
    last_prices = last_prices or {}
    state: dict[str, tuple[int, float, int]] = {}       # symbol -> (pos, avg, entry_ts)
    by_symbol: dict[str, dict[str, Any]] = {}
    by_channel: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    orders: dict[str, set[str]] = {}                   # канал -> номера заявок

    for t in trades:
        sym = str(t.get("sec") or "")
        side = str(t.get("side") or "").lower()
        if sym == "" or side not in ("buy", "sell"):
            continue
        try:
            qty, price = int(t.get("qty") or 0), float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        ts = int(t.get("ts_ms") or 0)
        pv = float(point_values.get(sym) or 0)
        pos, avg, entry_ts = state.get(sym, (0, 0.0, 0))
        delta = qty if side == "buy" else -qty
        is_close = pos != 0 and (pos > 0) != (delta > 0)
        new_pos, new_avg, new_entry, realized_pts = apply_fill(pos, avg, entry_ts,
                                                              delta, price, ts)
        state[sym] = (new_pos, new_avg, new_entry)

        scalper = bool(is_close and entry_ts and msk_date(entry_ts) == msk_date(ts))
        comm = commission_for(sym, price, qty, pv, taker=True, scalper=scalper,
                              ts=(ts + 3 * 3600_000) / 1000.0) if pv else 0.0

        s = by_symbol.setdefault(sym, {
            "symbol": sym, "point_value": pv, "fills": 0, "lots": 0,
            "realized_points": 0.0, "gross_rub": 0.0, "commission_rub": 0.0})
        s["fills"] += 1
        s["lots"] += qty
        s["realized_points"] += realized_pts
        s["gross_rub"] += realized_pts * pv
        s["commission_rub"] += comm

        # Канал: реализация приписывается ЗАКРЫВАЮЩЕЙ сделке — той, что превратила
        # переоценку в деньги. Комиссия и объём — каждой своей. Поэтому круг,
        # открытый руками и закрытый умной заявкой, отдаёт деньги умной: это не
        # огрех, а единственный способ не делить один результат надвое.
        ch = str(t.get("channel") or "quik")
        b = by_channel.setdefault(ch, {"channel": ch, "fills": 0, "lots": 0,
                                       "orders": 0, "gross_rub": 0.0,
                                       "commission_rub": 0.0})
        b["fills"] += 1
        b["lots"] += qty
        b["gross_rub"] += realized_pts * pv
        b["commission_rub"] += comm
        num = str(t.get("order_num") or "")
        if num:
            orders.setdefault(ch, set()).add(num)

        day = msk_date(ts)
        d = by_day.setdefault(day, {"date": day, "fills": 0, "lots": 0,
                                    "gross_rub": 0.0, "commission_rub": 0.0})
        d["fills"] += 1
        d["lots"] += qty
        d["gross_rub"] += realized_pts * pv
        d["commission_rub"] += comm

    open_rows = []
    for sym, (pos, avg, _ts) in state.items():
        if not pos:
            continue
        pv = float(point_values.get(sym) or 0)
        last = float(last_prices.get(sym) or 0)
        open_rows.append({
            "symbol": sym, "position": pos, "avg_price": round(avg, 4),
            "last": last or None, "point_value": pv,
            # Переоценка считается только когда цена ИЗВЕСТНА: нулём подменять нельзя,
            # иначе «минус вся позиция» появится на пустом месте.
            "unrealized_rub": (round((last - avg) * pos * pv, 2)
                               if (last and pv) else None),
        })

    for ch, rows in orders.items():
        by_channel[ch]["orders"] = len(rows)
    for r in by_channel.values():
        r["net_rub"] = round(r["gross_rub"] - r["commission_rub"], 2)
        r["gross_rub"], r["commission_rub"] = round(r["gross_rub"], 2), round(r["commission_rub"], 2)
    for r in by_day.values():
        r["net_rub"] = round(r["gross_rub"] - r["commission_rub"], 2)
        r["gross_rub"], r["commission_rub"] = round(r["gross_rub"], 2), round(r["commission_rub"], 2)

    gross = sum(r["gross_rub"] for r in by_symbol.values())
    comm = sum(r["commission_rub"] for r in by_symbol.values())
    priced = all(r["point_value"] > 0 for r in by_symbol.values())
    return {
        "fills": sum(r["fills"] for r in by_symbol.values()),
        "lots": sum(r["lots"] for r in by_symbol.values()),
        "gross_rub": round(gross, 2),
        "commission_rub": round(comm, 2),
        "net_rub": round(gross - comm, 2),
        # Хоть один инструмент без ₽/пункт — итог в рублях НЕПОЛНЫЙ, и экран обязан
        # это сказать, а не показывать заниженную сумму как окончательную.
        "priced": priced,
        "orders": sum(len(v) for v in orders.values()),
        "by_symbol": sorted(by_symbol.values(), key=lambda r: r["symbol"]),
        "by_channel": sorted(by_channel.values(), key=lambda r: r["channel"]),
        # by_source — прежнее имя тех же строк: экран компаньона уже выложен с ним.
        "by_source": [{**r, "source": r["channel"]}
                      for r in sorted(by_channel.values(), key=lambda r: r["channel"])],
        "by_day": sorted(by_day.values(), key=lambda r: r["date"]),
        "open": sorted(open_rows, key=lambda r: r["symbol"]),
    }


def report(period: str, point_values: dict[str, float],
           last_prices: dict[str, float] | None = None,
           now: datetime.datetime | None = None,
           directory: str | None = None,
           robot_ids: set[str] | None = None) -> dict[str, Any]:
    """Готовый ответ для экрана: итог за период плюс честные границы данных."""
    period = period if period in PERIODS else "day"
    today = (now or datetime.datetime.now(MSK)).date()
    days = period_days(period, today)
    trades = read_trades(days, directory, robot_ids)
    out = summarize(trades, point_values, last_prices)
    have = coverage_from(directory)
    out.update({
        "period": period, "from": days[0], "to": days[-1],
        "coverage_from": have,
        # Журнал начат позже, чем начинается период: часть окна не покрыта фактами.
        "partial": bool(have and have > days[0]),
    })
    return out
