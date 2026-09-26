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


def day_window(now: datetime.datetime) -> tuple[int, int]:
    """Границы окна «день» в миллисекундах: с 07:00 МСК текущего дня по сейчас.

    Не сутки с полуночи. QUIK датирует вечернюю сессию СЛЕДУЮЩИМ торговым днём,
    поэтому в файл сегодняшней даты попадают сделки, сделанные вчера вечером — и
    утром оператор видел их в «итоге за день» как сегодняшние. 07:00 МСК стоит
    после вечерки (закрытие 23:50) и до утренней сессии, то есть режет ровно
    между торговыми днями. Ночью до семи торговый день ещё вчерашний, иначе окно
    получилось бы пустым.
    """
    n = (now if now.tzinfo else now.replace(tzinfo=MSK)).astimezone(MSK)
    start = n.replace(hour=7, minute=0, second=0, microsecond=0)
    if n < start:
        start -= datetime.timedelta(days=1)
    return int(start.timestamp() * 1000), int(n.timestamp() * 1000)


def window_days(from_ms: int, to_ms: int) -> list[str]:
    """Даты файлов, в которых могут лежать сделки окна, — с запасом в сутки по
    обе стороны: из-за датировки вечерки следующим днём сегодняшний вечер лежит
    в файле ЗАВТРАШНЕЙ даты, а вчерашний — в сегодняшнем. Что попало в окно
    решает ts_ms, имя файла тут только подсказка, где искать."""
    d0 = datetime.datetime.fromtimestamp(from_ms / 1000, MSK).date()
    d1 = datetime.datetime.fromtimestamp(to_ms / 1000, MSK).date() + datetime.timedelta(days=1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def read_trades(days: list[str], directory: str | None = None,
                robot_ids: set[str] | None = None,
                from_ms: int = 0, to_ms: int = 0) -> list[dict[str, Any]]:
    """Ручные сделки за перечисленные дни, по времени, с проставленным каналом.

    КАНАЛ СЧИТАЕТСЯ ЗАНОВО, из тега, а записанному в строке `owner` доверия нет:
    журнал пишется в реальном времени, и строки, записанные до исправления
    классификации (23.09.2026), несут прежний ответ. Тег — факт от QUIK,
    классификация — наше суждение о нём, и пересматривать его задним числом
    можно, а переписывать факт нельзя.

    `from_ms`/`to_ms` (0 = без границы) режут по ВРЕМЕНИ СДЕЛКИ, а не по имени
    файла: дата файла — это торговый день QUIK, и вечерние сделки в нём старше
    своей даты."""
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
                ts = int(row.get("ts_ms") or 0)
                if (from_ms and ts < from_ms) or (to_ms and ts > to_ms):
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
              last_prices: dict[str, float] | None = None,
              account_positions: dict[str, int] | None = None) -> dict[str, Any]:
    """Свести ручные сделки в итог: закрытое (деньги) и открытое (переоценка).

    `point_values` — ₽ за пункт по инструменту (algo_ledger.point_values). Без него
    результат остаётся в ПУНКТАХ и деньгами не притворяется: пункт не рубль, и
    домножать на единицу в денежном пути запрещено.

    ОБ ОТКРЫТОМ ОТДАЮТСЯ ДВА РАЗНЫХ УТВЕРЖДЕНИЯ, и путать их нельзя:

      `open`            — позиция СЧЁТА (ручная часть: нетто QUIK минус позиции
                          РЕАЛЬНЫХ роботов). Приходит параметром `account_positions`
                          из API-слоя: этот модуль до store не достаёт и не должен.
      `window_residual` — что осталось незакрытым ВНУТРИ ОКНА, проигрыванием сделок
                          периода. К позиции счёта отношения не имеет.

    26.09.2026 экран ручной торговли назвал открытым шорт RIZ6 -4 со средней
    85565.4167, когда на счёте по RIZ6 был FLAT: вчерашний лонг закрывался внутри
    дня четырьмя продажами, и остаток окна оказался ровно -4. Остаток при этом не
    выбрасывается — расхождение между ним и счётом это признак НЕПОЛНОГО журнала
    (флаг journal_complete в trader/api/quik_manual.py считается именно по нему).

    `account_positions=None` — позиции счёта нет (зеркало агента молчит): тогда
    `open` повторяет остаток окна и `open_source` говорит об этом прямо."""
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

    def open_row(sym: str, pos: int, avg: float) -> dict[str, Any]:
        pv = float(point_values.get(sym) or 0)
        last = float(last_prices.get(sym) or 0)
        return {
            "symbol": sym, "position": pos,
            "avg_price": round(avg, 4) if avg else None,
            "last": last or None, "point_value": pv,
            # Переоценка считается только когда цена И СРЕДНЯЯ известны: нулём
            # подменять нельзя, иначе «минус вся позиция» появится на пустом месте.
            # У позиции счёта средней может не быть вовсе — она набрана до начала
            # окна, и выдумывать цену входа вместо null запрещено.
            "unrealized_rub": (round((last - avg) * pos * pv, 2)
                               if (last and pv and avg) else None),
        }

    residual = {sym: (pos, avg) for sym, (pos, avg, _ts) in state.items() if pos}
    window_rows = [open_row(s, p, a) for s, (p, a) in sorted(residual.items())]
    if account_positions is None:
        open_rows, open_source = window_rows, "window"
    else:
        open_rows, open_source = [], "account"
        for sym, pos in sorted(account_positions.items()):
            if not pos:
                continue
            r_pos, r_avg = residual.get(sym, (0, 0.0))
            # Средняя из окна годится ТОЛЬКО если остаток окна той же стороны: у
            # позиции противоположного знака это средняя ЧУЖОЙ позиции, и считать
            # по ней переоценку значит врать в рублях.
            avg = r_avg if (r_pos and (r_pos > 0) == (pos > 0)) else 0.0
            open_rows.append(open_row(sym, pos, avg))

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
        "open": open_rows,
        # Откуда взята строка «открыто»: "account" — позиция счёта, "window" —
        # остаток окна (позиции счёта не знаем). Экран обязан называть это разное
        # разными словами.
        "open_source": open_source,
        "window_residual": window_rows,
    }


def report(period: str, point_values: dict[str, float],
           last_prices: dict[str, float] | None = None,
           now: datetime.datetime | None = None,
           directory: str | None = None,
           robot_ids: set[str] | None = None,
           account_positions: dict[str, int] | None = None) -> dict[str, Any]:
    """Готовый ответ для экрана: итог за период плюс честные границы данных."""
    period = period if period in PERIODS else "day"
    now = now or datetime.datetime.now(MSK)
    if now.tzinfo is None:                 # деньги: наивное время молча уехало бы в tz машины
        now = now.replace(tzinfo=MSK)
    now = now.astimezone(MSK)
    today = now.date()
    # ДЕНЬ — не сутки, а торговый день с 07:00 МСК (см. day_window). Неделя и
    # месяц остались скользящими сутками: там граница вечерки на итог не влияет.
    if period == "day":
        from_ms, to_ms = day_window(now)
        trades = read_trades(window_days(from_ms, to_ms), directory, robot_ids,
                             from_ms, to_ms)
        start = datetime.datetime.fromtimestamp(from_ms / 1000, MSK).date().isoformat()
    else:
        days = period_days(period, today)
        trades = read_trades(days, directory, robot_ids)
        from_ms = int(datetime.datetime.fromisoformat(days[0])
                      .replace(tzinfo=MSK).timestamp() * 1000)
        to_ms = int(now.timestamp() * 1000)
        start = days[0]
    out = summarize(trades, point_values, last_prices, account_positions)
    have = coverage_from(directory)
    out.update({
        "period": period, "from": start, "to": today.isoformat(),
        # ГРАНИЦЫ ОКНА ЯВНО, в мс: экран пишет «с 07:00 26.09» словами, а не
        # подразумевает начало дня. Подразумеваемая граница и была причиной того,
        # что вчерашний вечер читался как сегодняшний итог.
        "from_ms": from_ms, "to_ms": to_ms,
        "coverage_from": have,
        # Журнал начат позже, чем начинается период: часть окна не покрыта фактами.
        "partial": bool(have and have > start),
    })
    return out
