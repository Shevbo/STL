"""Статистика ЖИВОГО робота по кругам: доля выигранных и recovery factor.

ЗАЧЕМ. Хит-парад ранжирует по финрезу, и робот с маленьким объёмом там не виден,
хотя его качество может быть выше: 70% выигранных кругов при RF 3 на одном
контракте — это кандидат на увеличение объёма, а +300 тысяч на двадцати
контрактах при RF 0.4 — кандидат на выключение (замечание оператора 25.09.2026).
Качество и масштаб — разные оси, и мерить их надо порознь.

МЕТОДИКА ТА ЖЕ, ЧТО В БЭКТЕСТЕ (trader/lab/backtest.py), иначе экраны спорят:
  • сделка = КРУГ (от нулевой позиции до нулевой), а не филл: робот, доливший
    позицию тремя филлами и закрывший одним, сделал одну сделку, а не четыре;
  • win_rate = доля кругов с net > 0 (net — уже после комиссии);
  • recovery_factor = net / максимальная просадка кривой эквити по ЗАКРЫТЫМ кругам.
    Нет просадки (ни одного отката) — RF не определён, и это честнее, чем ∞.

ОГРАНИЧЕНИЕ, КОТОРОЕ ОБЯЗАН ЗНАТЬ ЧИТАТЕЛЬ: просадка здесь по закрытым кругам.
Стратегия, которая досиживает убыток до стопа, показывает мираж — win rate 0.93
при худшем итоге (реальный случай, 24.09.2026). Поэтому рядом с RF отдаётся
`open_tail` — сколько филлов висит в незакрытом круге на конце периода.
"""

from __future__ import annotations

import datetime
from typing import Any

MSK = datetime.timezone(datetime.timedelta(hours=3))
PERIODS = ("day", "week", "month", "all")
BUCKETS = ("day", "week", "month")
_SPAN_DAYS = {"day": 1, "week": 7, "month": 30}


def period_bounds(period: str, now: datetime.datetime | None = None) -> tuple[int, int]:
    """[от, до) в epoch-ms для скользящего окна МСК. "all" — от начала времён."""
    now = now or datetime.datetime.now(MSK)
    end = int(now.timestamp() * 1000)
    span = _SPAN_DAYS.get(period)
    if span is None:
        return 0, end
    start_day = (now.date() - datetime.timedelta(days=span - 1))
    start = datetime.datetime.combine(start_day, datetime.time(0, 0), tzinfo=MSK)
    return int(start.timestamp() * 1000), end


def cycles(fills: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Склеить филлы в круги по pos_after. Возвращает (круги, хвост незакрытых).

    Филлы обязаны прийти по возрастанию времени и принадлежать ОДНОМУ роботу:
    pos_after — это его позиция, и перемешав роботов, мы склеили бы чужие круги."""
    out: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for f in fills:
        cur.append(f)
        if int(f.get("pos_after") or 0) == 0:
            net = sum(float(x.get("pnl_net_rub") or 0) for x in cur)
            out.append({
                "start_ms": int(cur[0].get("ts_ms") or 0),
                "end_ms": int(f.get("ts_ms") or 0),
                "net_rub": round(net, 2),
                "fills": len(cur),
                "lots": max(abs(int(x.get("qty") or 0)) for x in cur),
                "side": str(cur[0].get("side") or ""),
            })
            cur = []
    return out, len(cur)


def drawdown(nets: list[float]) -> float:
    """Максимальная просадка кривой эквити по закрытым кругам, в рублях."""
    equity = peak = 0.0
    worst = 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def bucket_key(ts_ms: int, bucket: str) -> str:
    """МСК-метка корзины: день 2026-09-25, неделя 2026-W39, месяц 2026-09.

    Неделя по ISO — не «последние 7 дней»: на графике столбик обязан совпадать с
    календарной неделей, иначе соседние точки считают разные отрезки."""
    d = datetime.datetime.fromtimestamp(ts_ms / 1000, MSK).date()
    if bucket == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if bucket == "month":
        return d.strftime("%Y-%m")
    return d.isoformat()


def summarize(rows: list[dict[str, Any]], period: str = "all",
              bucket: str = "day") -> dict[str, Any]:
    """Метрики одного робота по его филлам за период.

    `bucket` задаёт шаг серии для графика: день, неделя (ISO) или месяц."""
    cyc, tail = cycles(rows)
    nets = [c["net_rub"] for c in cyc]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    net = round(sum(nets), 2)
    dd = round(drawdown(nets), 2)
    gross_win, gross_loss = sum(wins), -sum(losses)
    # Круг попадает в корзину по времени ВЫХОДА: деньги признаются закрытием,
    # а не входом, иначе круг, открытый в пятницу и закрытый в понедельник,
    # добавил бы прибыль в неделю, когда её ещё не было.
    buckets: dict[str, dict[str, Any]] = {}
    for c in cyc:
        key = bucket_key(c["end_ms"], bucket)
        b = buckets.setdefault(key, {"key": key, "net_rub": 0.0, "trades": 0, "wins": 0})
        b["net_rub"] = round(b["net_rub"] + c["net_rub"], 2)
        b["trades"] += 1
        b["wins"] += 1 if c["net_rub"] > 0 else 0
    equity = 0.0
    series = []
    for key in sorted(buckets):
        b = buckets[key]
        equity = round(equity + b["net_rub"], 2)
        series.append({**b, "equity_rub": equity,
                       "win_rate": round(b["wins"] / b["trades"], 4) if b["trades"] else None})
    return {
        "period": period,
        "trades": len(cyc),               # КРУГОВ, не филлов
        "fills": len(rows),
        "open_tail": tail,                # филлов в незакрытом круге на конце окна
        "wins": len(wins),
        "losses": len(losses),
        # Доля выигранных: None при нуле кругов — ноль процентов и «сделок не было»
        # это разные утверждения, и склеивать их нельзя.
        "win_rate": (round(len(wins) / len(cyc), 4) if cyc else None),
        "net_rub": net,
        "max_drawdown_rub": dd,
        # RF не определён без просадки: делить на ноль и рисовать ∞ — обман.
        "recovery_factor": (round(net / dd, 3) if dd > 0 else None),
        "profit_factor": (round(gross_win / gross_loss, 3) if gross_loss > 0 else None),
        "avg_win_rub": (round(sum(wins) / len(wins), 2) if wins else None),
        "avg_loss_rub": (round(sum(losses) / len(losses), 2) if losses else None),
        "best_rub": (round(max(nets), 2) if nets else None),
        "worst_rub": (round(min(nets), 2) if nets else None),
        "bucket": bucket,
        "series": series,
        # Прежнее имя серии: экран, выложенный до 25.09, читает by_day.
        "by_day": series,
        "first_ms": (cyc[0]["start_ms"] if cyc else None),
        "last_ms": (cyc[-1]["end_ms"] if cyc else None),
    }


def by_robot(fills: list[dict[str, Any]], period: str = "all",
             bucket: str = "day") -> list[dict[str, Any]]:
    """Метрики по каждому роботу. Филлы приходят одним списком из журнала."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    meta: dict[tuple[str, str], str] = {}
    for f in sorted(fills, key=lambda r: int(r.get("ts_ms") or 0)):
        key = (str(f.get("robot_id") or ""), str(f.get("mode") or ""))
        groups.setdefault(key, []).append(f)
        meta.setdefault(key, str(f.get("symbol") or ""))
    out = []
    for (rid, mode), rows in groups.items():
        stat = summarize(rows, period, bucket)
        stat.update({"robot_id": rid, "mode": mode, "symbol": meta[(rid, mode)]})
        out.append(stat)
    # Сортировка по качеству, а не по деньгам: ради этого экран и заводится.
    out.sort(key=lambda r: (r["recovery_factor"] is None, -(r["recovery_factor"] or 0),
                            -(r["win_rate"] or 0)))
    return out
