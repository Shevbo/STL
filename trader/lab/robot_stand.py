"""Единая запись робота для стенда — одна форма для агентского и бумажного.

Стенд робота ОДИН на бэктест, бумагу и реал (правило оператора 05.08.2026), поэтому
источник выбирается на сервере, а не ветвлением во фронтенде: ветвление и породило
два экрана, которые потом разъехались.

Форма — та же, что отдаёт зеркало агента (`/quik/robots-mirror`): под неё уже написан
и стенд, и LLM-напарник (`api/quik_robot_chat.py`). Здесь собирается ТОЛЬКО бумажный
робот STL, агентский берётся из зеркала как раньше.

Держать сборку здесь, а не в обработчике, обязательно: её зовут ДВА потребителя —
ручка стенда и напарник. Копия на каждого означала бы, что напарник рассказывает про
одну позицию, а экран показывает другую.
"""
from __future__ import annotations

import json
from typing import Any

# Статусы live_trades, которые РЕАЛЬНО двигали позицию (остальное — отказы/пропуски).
EXECUTED = {"paper", "filled", "executed", "submitted"}


def _params(raw: Any) -> dict:
    """params_json приходит то объектом, то строкой (jsonb-кодек + исторические слои)."""
    for _ in range(3):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return {}
        else:
            break
    return raw if isinstance(raw, dict) else {}


def walk_fills(trades: list[dict]) -> tuple[int, float, float]:
    """Знаковая проходка по филлам: позиция, средняя, реализованный P&L в ПУНКТАХ.

    Пункты, а не рубли: стенд и напарник умножают на ₽/пункт сами, как у раннера.
    Отдай рубли — умножат второй раз. Частичное сокращение СОХРАНЯЕТ среднюю (меньше
    контрактов, та же цена входа) — тот же инвариант, что в robot_runner/runtime.py
    и trader/lab/runtime.py; сброс средней на цену закрытия ломал бы каждый
    следующий расчёт.
    """
    pos, avg, realized = 0, 0.0, 0.0
    for t in trades:
        if t.get("status") not in EXECUTED:
            continue
        q = int(t.get("qty") or 0) * (1 if t.get("side") == "buy" else -1)
        px = float(t.get("price") or 0)
        if not q:
            continue
        if pos == 0 or (pos > 0) == (q > 0):
            avg = (avg * abs(pos) + px * abs(q)) / (abs(pos) + abs(q))
            pos += q
        else:
            take = min(abs(q), abs(pos))
            realized += (px - avg) * take * (1 if pos > 0 else -1)
            rest = abs(q) - take
            pos += q
            if rest:
                avg = px
    return pos, avg, realized


def paper_record(robot_id: str, live: dict) -> dict:
    """Бумажный робот STL в форме записи зеркала. `live` — ответ /robots/{id}/live."""
    rb = live.get("robot") or {}
    params = _params(rb.get("params_json"))
    trades = live.get("trades") or []
    pos, avg, realized = walk_fills(trades)
    return {
        "robot_id": robot_id,
        "display_name": rb.get("name") or robot_id,
        "symbol": live.get("chart_symbol") or live.get("symbol"),
        "strategy_id": (live.get("strategy") or {}).get("id"),
        "params_json": params,
        "paper": True,                      # робот STL всегда бумажный
        "running": bool(rb.get("deployed")),
        "paused": not bool(rb.get("deployed")),
        "position": pos,
        "avg_price": round(avg, 6),
        "realized_pnl": round(realized, 6),
        "max_position": int(params.get("avg_max") or params.get("qty") or 0),
        "schedule": rb.get("schedule"),
        # Филлы в АГЕНТСКОМ формате: стенд читает `ts_unix_ms` и сторону 'SIDE_SELL'.
        # Отдай `time`+`sell` — время станет нулевой эпохой, а каждый филл прочитается
        # как ПОКУПКА (сравнение идёт со строкой), позиция будет только расти, ни одна
        # сделка не закроется и P&L строки выродится в одну комиссию. Так и было
        # на живом стенде 05.08.2026.
        "recent_fills": [
            {"ts_unix_ms": int(t.get("time") or 0) * 1000,
             "symbol": t.get("symbol"),
             "side": "SIDE_SELL" if t.get("side") == "sell" else "SIDE_BUY",
             "qty": int(t.get("qty") or 0),
             "price": float(t.get("price") or 0),
             "order_id": t.get("order_id") or "",
             "status": t.get("status") or ""}
            for t in trades[-200:]
        ],
        "working_orders": live.get("open_orders") or [],
        "signal_json": None,                # интроспекции у STL-робота пока нет
        "bars_count": None,
        "heartbeat_unix_ms": None,
        "last_close": None,
    }


# Что на этом источнике вообще есть. Кадры, которых нет, стенд НЕ РИСУЕТ — пустой
# кадр читается как «сломалось», а не как «неприменимо».
CAPS_AGENT = {"quik": True, "chat": True, "commands": True, "signal": True}
CAPS_PAPER = {"quik": False, "chat": True, "commands": False, "signal": False}
# chat=True: напарник умеет и бумажного (api/quik_robot_chat собирает его запись
# этим же кодом). Без ₽/пункт от QLua, ГО и лимитов агента — их у STL-робота нет.
