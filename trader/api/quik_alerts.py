"""Тревоги агента QUIK для экранов STL: книга, подтверждение и флэш компаньона.

Исполнительный модуль (docs/design/execution-module.md, разделы 12 и 15). Агент шлёт
кадры ``Alert``; до сих пор STL только пересылал их в Telegram (AlertForwarder), а
хранилище держало ПОСЛЕДНЮЮ тревогу агента. Экрану нужна история и отметка «принято
оператором», компаньону — список неснятых тревог, по которым мигать шапкой.

Книга живёт в ``app.state.quik_alert_book``. Наполняет её ``RecordingForwarder`` —
тот же форвардер, что создаётся в app.py, но сначала записывающий тревогу. Точка
выбрана намеренно: через ``forward`` проходит КАЖДАЯ тревога (и при ненастроенном
Telegram тоже), а приём кадра и хранилище агента — зона real-trade, трогать их ради
экрана не нужно.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth
from trader.quik.alerts import SEVERITY_CRITICAL, SEVERITY_WARN, AlertForwarder

router = APIRouter(prefix="/api/v1/quik/alerts", tags=["quik-alerts"])

# Раздел 12: тревоги, по которым компаньон мигает. Остальные видны в списке, но не
# мигают — мигание на всё подряд перестают замечать через день.
FLASH_CODES = frozenset({
    "EXIT_NOT_FILLED", "EXEC_STALL", "STOP_CHILD_REJECTED_GO", "STOP_ORDER_WITHDRAWN",
    "STOP_NO_CHILD", "GO_EXCEEDED", "TX_QUEUE_STALL",
})

_SEVERITY_RU = {SEVERITY_WARN: "WARN", SEVERITY_CRITICAL: "CRITICAL"}


class AlertBook:
    """Ограниченная память тревог (новые в конце) с отметкой подтверждения.

    Ключ тревоги — (code, raised_at): так же её подтверждает экран
    (``POST .../alerts/ack {code, raised_at}``). В памяти процесса: рестарт STL
    обнуляет книгу, и это честно — агент повторит то, что всё ещё горит.
    """

    def __init__(self, keep: int = 500) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=max(1, keep))
        self._lock = threading.Lock()

    def add(self, alert: dict[str, Any], agent_id: str) -> None:
        item = {
            "agent_id": agent_id or "",
            "severity": int(alert.get("severity", 0) or 0),
            "code": str(alert.get("code", "") or ""),
            "message": str(alert.get("message", "") or ""),
            "raised_at": int(alert.get("raised_at_unix_ms", 0) or 0),
            "acked_ms": 0,
        }
        item["severity_label"] = _SEVERITY_RU.get(item["severity"], "INFO")
        with self._lock:
            self._items.append(item)

    def items(self, limit: int = 100) -> list[dict[str, Any]]:
        """Свежие сверху."""
        with self._lock:
            return [dict(x) for x in reversed(self._items)][: max(0, limit)]

    def ack(self, code: str, raised_at: int, now_ms: int) -> int:
        """Подтвердить тревогу. Возвращает число снятых записей (0 = такой нет)."""
        n = 0
        with self._lock:
            for x in self._items:
                if x["code"] == code and x["raised_at"] == int(raised_at) and not x["acked_ms"]:
                    x["acked_ms"] = now_ms
                    n += 1
        return n

    def flash(self) -> list[dict[str, Any]]:
        """Неснятые тревоги из флэш-списка, свежие сверху. По WARN не мигаем (раздел 15)."""
        return [
            {"code": x["code"], "text": x["message"] or x["code"], "ts_ms": x["raised_at"]}
            for x in self.items(limit=len(self._items))
            if not x["acked_ms"] and x["code"] in FLASH_CODES and x["severity"] != SEVERITY_WARN
        ]


class RecordingForwarder(AlertForwarder):
    """AlertForwarder, который сначала записывает тревогу в книгу экрана."""

    def __init__(self, book: AlertBook, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.book = book

    async def forward(self, alert: dict, agent_host: str) -> None:
        try:
            self.book.add(alert, agent_host)
        except Exception:  # noqa: BLE001 — книга экрана не имеет права сломать доставку
            pass
        await super().forward(alert, agent_host)


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _book(request: Request) -> AlertBook:
    book = getattr(request.app.state, "quik_alert_book", None)
    if book is None:
        raise HTTPException(status_code=503, detail="QUIK агент не запущен: тревог нет.")
    return book


class AckBody(BaseModel):
    code: str
    raised_at: int


@router.get("")
async def list_alerts(request: Request, limit: int = 100):
    _auth(request)
    book = _book(request)
    return {"items": book.items(limit=limit), "flash": book.flash()}


@router.post("/ack")
async def ack_alert(body: AckBody, request: Request):
    import time as _time
    _auth(request)
    n = _book(request).ack(body.code, body.raised_at, int(_time.time() * 1000))
    if not n:
        raise HTTPException(status_code=404, detail="Такой неснятой тревоги нет.")
    return {"ok": True, "acked": n}
