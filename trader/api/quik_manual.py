"""API ручной торговли оператора: журнал событий и доходность за период.

Два вопроса, на которые до 24.09.2026 ответа в STL не было:

  «что происходило с моей заявкой и по чьей воле» — GET …/manual/journal:
      единая лента событий заявок (trader/quik/so_journal) и ФАКТИЧЕСКИХ сделок
      (журнал trader/quik/truth), у каждой строки время и ИСТОЧНИК: оператор,
      сторож STL, терминал QUIK;

  «сколько я на этом заработал» — GET …/manual/pnl?period=day|week|month:
      сведение кругов по средней цене, открытая позиция отдельной строкой,
      комиссия оценкой, плюс честные границы данных (coverage_from/partial).

Только чтение: ни одна ручка здесь ничего не ставит и не снимает.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from trader.auth.guard import require_auth
from trader.quik import manual_pnl, so_journal
from trader.quik.algo_ledger import point_values
from trader.quik.truth import SMART_TAG, load_robot_ids

router = APIRouter(prefix="/api/v1/quik/manual", tags=["quik-manual"])


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _store(request: Request):
    return getattr(request.app.state, "quik_store", None)


def _robot_ids(store) -> set[str]:
    """Реестр роботов: накопленный на диске плюс те, кто прямо сейчас в зеркале.

    Без него незнакомый непустой brokerref пришлось бы звать роботом, и сделки из
    приложения брокера (теги вида "}S…XдD") не попали бы в ручную торговлю вовсе."""
    ids = load_robot_ids()
    status = (store.agent_status(None) or {}) if store is not None else {}
    ids |= {str(r.get("id")) for r in status.get("robots") or [] if r.get("id")}
    return ids


def _prices(store) -> tuple[dict[str, float], dict[str, float]]:
    """(₽ за пункт, последняя цена) по инструментам — из зеркала агента."""
    if store is None:
        return {}, {}
    pv = point_values(store.params(None) or {})
    status = store.agent_status(None) or {}
    last = {}
    for f in (status.get("health") or {}).get("feed") or []:
        code, px = str(f.get("code") or ""), float(f.get("last") or 0)
        if code and px > 0:
            last[code] = px
    return pv, last


@router.get("/pnl")
async def pnl(request: Request, period: str = "day"):
    """Итог ручной торговли за день/неделю/месяц."""
    _auth(request)
    if period not in manual_pnl.PERIODS:
        raise HTTPException(status_code=422,
                            detail=f"period должен быть одним из {manual_pnl.PERIODS}")
    store = _store(request)
    pv, last = _prices(store)
    return manual_pnl.report(period, pv, last, robot_ids=_robot_ids(store))


@router.get("/journal")
async def journal(request: Request, period: str = "day", so_id: str = "",
                  limit: int = 500):
    """Лента событий и сделок ручной торговли, новые сверху.

    События и сделки живут в РАЗНЫХ журналах (намерение и факт — разные вещи, и
    сведение их в один файл потеряло бы это различие), но читать их оператору
    удобнее вместе, по одной оси времени."""
    _auth(request)
    if period not in manual_pnl.PERIODS:
        raise HTTPException(status_code=422,
                            detail=f"period должен быть одним из {manual_pnl.PERIODS}")
    today = datetime.datetime.now(manual_pnl.MSK).date()
    days = manual_pnl.period_days(period, today)

    rows: list[dict[str, Any]] = []
    for e in so_journal.read_days(days):
        if so_id and e.get("so_id") != so_id:
            continue
        rows.append({"ts_ms": e.get("ts_ms"), "type": "event", "event": e.get("event"),
                     # События есть только у умных заявок: терминал QUIK и приложение
                     # брокера своих намерений STL не рассказывают, от них видны
                     # только сделки.
                     "channel": "smart",
                     "source": e.get("source"), "so_id": e.get("so_id"),
                     "code": e.get("code"), "side": e.get("side"), "qty": e.get("qty"),
                     "kind": e.get("kind"), "parent_id": e.get("parent_id"),
                     "detail": e.get("detail")})
    for t in manual_pnl.read_trades(days, robot_ids=_robot_ids(_store(request))):
        tag = str(t.get("tag") or "")
        sid = tag[len(SMART_TAG):] if tag.startswith(SMART_TAG) else ""
        if so_id and sid != so_id:
            continue
        rows.append({"ts_ms": t.get("ts_ms"), "type": "trade",
                     "event": "сделка", "so_id": sid,
                     "channel": t.get("channel"), "tag": tag,
                     "source": (f"умная заявка {sid}" if sid else
                                f"{so_journal.TERMINAL} (рука)" if not tag else
                                "приложение брокера"),
                     "code": t.get("sec"), "side": t.get("side"), "qty": t.get("qty"),
                     "price": t.get("price"), "order_num": t.get("order_num"),
                     "detail": f"{t.get('qty')} по {t.get('price')}"})

    rows.sort(key=lambda r: int(r.get("ts_ms") or 0), reverse=True)
    return {"period": period, "from": days[0], "to": days[-1],
            "events_from": so_journal.coverage(),
            "trades_from": manual_pnl.coverage_from(),
            "count": len(rows), "rows": rows[:max(1, min(int(limit), 5000))]}
