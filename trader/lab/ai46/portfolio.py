"""Портфельный учёт team-46 (AI46) — стратегия НЕ контрактная.

Почему отдельный модуль, а не общий рублёвый пересчёт витрины:

AI46 держит до `max_positions` позиций в РАЗНЫХ инструментах, размер каждой —
ДОЛЯ ПОРТФЕЛЯ (`size_pct` ~ 1-3%), выход только по времени (primary_hold 15 мин /
reversal_hold 30 мин), а усреднений у неё нет ПО КОНСТРУКЦИИ: RiskManager
запрещает вторую позицию в том же тикере. Контрактная витрина (рубли, ГО, TP/SL,
усреднение) к ней неприменима трижды:
  * 1 контракт RI и 1 контракт ED — разные деньги, складывать их нельзя;
  * ГО «текущего контракта × пик контрактов» для портфеля из 5 инструментов
    считает не тот капитал, поэтому все проценты доходности были от балды;
  * «AVG» у стратегии не бывает вовсе — ярлык брался из пересчёта позиции.

Единица правды здесь — ДОХОДНОСТЬ: доходность инструмента за сделку (%) и, когда
известен вес, вклад сделки в портфель (%). Рубли не выдумываем: у бумажного
портфеля нет заданного капитала, любая рублёвая цифра была бы вымыслом.

Метаданные филла упакованы в `live_trades.order_id`:
    ai46:<seq>:<kind>:<вес в сотых долях процента>
Старые записи (до 31.07.2026) несут константу 'ai46' — вес неизвестен, роль филла
восстанавливается чередованием вход/выход внутри инструмента.
"""
from __future__ import annotations

_PREFIX = "ai46"


def parse_meta(order_id) -> dict | None:
    """'ai46:12:close_soft:150' -> {'kind': 'close_soft', 'size_pct': 0.015}.

    None — филл не наш; {'kind': None, ...} — наш, но без метаданных (старый формат).
    """
    s = str(order_id or "")
    if s != _PREFIX and not s.startswith(_PREFIX + ":"):
        return None
    parts = s.split(":")
    kind = parts[2] if len(parts) > 2 and parts[2] else None
    size = None
    if len(parts) > 3 and parts[3].lstrip("-").isdigit():
        size = int(parts[3]) / 10000.0
    return {"kind": kind, "size_pct": size}


def is_portfolio_fill(order_id) -> bool:
    return parse_meta(order_id) is not None


def enrich(trades: list[dict]) -> dict:
    """Размечает филлы ролями и доходностью, возвращает сводку портфеля.

    Проходит КАЖДЫЙ инструмент отдельно (позиции независимы). На закрывающем филле
    считает доходность инструмента за сделку и, если известен вес, вклад в портфель.
    Филлы мутируются на месте: добавляются kind / size_pct / ret_pct / port_pct.

    `orphans` — филлы, у которых нет пары: рестарт сервиса теряет открытые бумажные
    позиции (PaperExecutor держит их в памяти), поэтому в истории есть подряд идущие
    входы в один тикер. Это не сделки, в доходность они не идут.
    """
    open_pos: dict[str, dict] = {}
    by_sym: dict[str, dict] = {}
    closes = wins = weighted = orphans = 0
    ret_sum = port_sum = 0.0

    for t in sorted(trades, key=lambda x: (x.get("time") or 0)):
        meta = parse_meta(t.get("order_id")) or {}
        sym = t.get("symbol") or ""
        cur = open_pos.get(sym)
        # Роль филла: из метаданных, иначе по чередованию (старый формат).
        kind = meta.get("kind") or ("open" if cur is None else "close")
        t["kind"] = kind
        t["size_pct"] = meta.get("size_pct")
        t["ret_pct"] = None
        t["port_pct"] = None

        if kind == "open":
            if cur is not None:
                orphans += 1              # прошлый вход потерян рестартом
            open_pos[sym] = t
            continue
        if cur is None:
            orphans += 1                  # выход без входа — тоже потерянная пара
            continue

        entry = float(cur.get("price") or 0)
        open_pos.pop(sym, None)
        if entry <= 0:
            orphans += 1
            continue
        dirmul = 1.0 if cur.get("side") == "buy" else -1.0
        ret = (float(t.get("price") or 0) - entry) / entry * dirmul
        t["ret_pct"] = ret * 100.0
        closes += 1
        ret_sum += ret
        if ret > 0:
            wins += 1
        size = cur.get("size_pct")
        if size:
            t["port_pct"] = ret * size * 100.0
            port_sum += ret * size
            weighted += 1

        s = by_sym.setdefault(sym, {"symbol": sym, "closes": 0, "wins": 0, "ret_sum_pct": 0.0})
        s["closes"] += 1
        s["wins"] += 1 if ret > 0 else 0
        s["ret_sum_pct"] += ret * 100.0

    times = [t["time"] for t in trades if t.get("time") is not None]
    days = ((max(times) - min(times)) / 86400.0) if len(times) > 1 else 0.0
    return {
        "mode": "portfolio",
        "instruments": len({t.get("symbol") for t in trades if t.get("symbol")}),
        "fills": len(trades),
        "closes": closes,
        "wins": wins,
        "win_rate": (wins / closes * 100.0) if closes else 0.0,
        # Равновзвешенная сумма доходностей сделок: «сколько дала стратегия, если бы
        # каждая сделка шла одинаковым размером». Не зависит от веса — считается по
        # всей истории, включая старые записи без веса.
        "ret_sum_pct": ret_sum * 100.0,
        "ret_avg_pct": (ret_sum / closes * 100.0) if closes else 0.0,
        # С учётом реального веса позиции — только по филлам, где вес записан.
        "port_pct": (port_sum * 100.0) if weighted else None,
        "weighted_closes": weighted,
        "ann_pct": (port_sum * 100.0 * 365.0 / days) if (weighted and days >= 3) else None,
        "orphans": orphans,
        "days": days,
        "open_now": [
            {"symbol": s, "side": p.get("side"), "price": float(p.get("price") or 0),
             "iso": p.get("iso"), "size_pct": p.get("size_pct")}
            for s, p in sorted(open_pos.items())
        ],
        "by_symbol": sorted(by_sym.values(), key=lambda x: x["ret_sum_pct"], reverse=True),
    }


def demo() -> None:
    """Самопроверка: вход/выход по метаданным, старый формат по чередованию, сирота."""
    assert parse_meta("ai46:7:close_soft:150") == {"kind": "close_soft", "size_pct": 0.015}
    assert parse_meta("ai46") == {"kind": None, "size_pct": None}
    assert parse_meta("rr:bot:1:abcdef") is None

    trades = [
        # SiU6: шорт 80000 -> откуп 79200 = +1% инструмента, вес 1.5% -> +0.015% портфеля
        {"time": 10, "symbol": "SiU6", "side": "sell", "price": 80000, "order_id": "ai46:0:open:150"},
        {"time": 20, "symbol": "SiU6", "side": "buy", "price": 79200, "order_id": "ai46:1:close_soft:150"},
        # старый формат: лонг 100 -> 101 = +1%, вес неизвестен
        {"time": 30, "symbol": "EDU6", "side": "buy", "price": 100, "order_id": "ai46"},
        {"time": 40, "symbol": "EDU6", "side": "sell", "price": 101, "order_id": "ai46"},
        # сирота: вход без выхода (потерян рестартом)
        {"time": 50, "symbol": "GDU6", "side": "buy", "price": 4000, "order_id": "ai46:2:open:300"},
        {"time": 60, "symbol": "GDU6", "side": "buy", "price": 4010, "order_id": "ai46:3:open:300"},
    ]
    s = enrich(trades)
    assert trades[1]["kind"] == "close_soft" and abs(trades[1]["ret_pct"] - 1.0) < 1e-9
    assert abs(trades[1]["port_pct"] - 0.015) < 1e-9
    assert trades[3]["kind"] == "close" and abs(trades[3]["ret_pct"] - 1.0) < 1e-9
    assert trades[3]["port_pct"] is None          # вес неизвестен — вклад не выдумываем
    assert s["closes"] == 2 and s["wins"] == 2 and s["orphans"] == 1
    assert abs(s["ret_sum_pct"] - 2.0) < 1e-9
    assert abs(s["port_pct"] - 0.015) < 1e-9      # взвешенный итог — только по SiU6
    assert s["weighted_closes"] == 1 and s["instruments"] == 3
    assert [o["symbol"] for o in s["open_now"]] == ["GDU6"]
    print("ai46.portfolio demo ok")


if __name__ == "__main__":
    demo()
