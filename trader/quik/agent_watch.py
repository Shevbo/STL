"""Сторож молчания агента: торговая сессия идёт, а агента нет.

ЗАЧЕМ. 20.08.2026 агент отвалился в 02:04 и вернулся в 09:09 — семь часов, из
них два с лишним после открытия сессии в 07:00. Роботы живут ВНУТРИ агента,
значит всё это время не торговал никто, а узнал об этом человек. Тревог за то
утро не ушло ни одной: сторож рекона следит за РАСХОЖДЕНИЕМ книг роботов с
фактом QUIK и по построению молчит, когда книг нет вовсе. Пропажа самого агента
не проверялась нигде.

ПОЧЕМУ ГЕЙТ ПО СЕССИИ. Ночью и на выходном закрытии агент молчит штатно, и
будить оператора незачем. Оракул сессии (`market_session`) уже знает, торгует ли
FORTS ПРЯМО СЕЙЧАС, и его же ответ «неизвестно» трактуется защитно — тревожим,
потому что неизвестность на стороне ISS не повод считать рынок закрытым.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger()

SILENT_SEC = 300           # 5 минут тишины при открытом рынке = агента нет
POLL_SEC = 60
CODE_DOWN = "AGENT_SILENT"
CODE_UP = "AGENT_SILENT_RECOVERED"


def _now_ms() -> int:
    return int(time.time() * 1000)


def silence_verdict(agents: list[dict[str, Any]], market_open: bool | None,
                    silent_sec: int = SILENT_SEC, now_ms: int | None = None) -> tuple[bool, int]:
    """(тревожить?, возраст самого свежего сигнала в секундах).

    Рынок закрыт — молчание штатно. Открыт или НЕИЗВЕСТЕН — судим по возрасту.
    Агентов нет вовсе (ни одного зарегистрированного) — это тоже тишина."""
    if market_open is False:
        return False, 0
    now = now_ms or _now_ms()
    if not agents:
        return True, silent_sec
    freshest = max(int(a.get("last_seen_ms") or 0) for a in agents)
    age = int((now - freshest) / 1000) if freshest else silent_sec
    return age >= silent_sec, age


async def watch(state) -> None:
    """Фоновая задача: раз в минуту сверяет тишину агента с состоянием рынка."""
    raised = False
    while True:
        try:
            store = getattr(state, "quik_store", None)
            alerts = getattr(state, "quik_alerts", None)
            if store is not None and alerts is not None:
                ms = getattr(state, "market_session", None) or {}
                bad, age = silence_verdict(store.agent_status(), ms.get("open"))
                if bad and not raised:
                    raised = True
                    await alerts.forward({
                        "severity": 3, "code": CODE_DOWN,
                        "message": (f"Агент не выходит на связь {age} с, а биржа торгует. "
                                    "Роботы живут внутри агента: сейчас не торгует никто. "
                                    "Проверь агента на QUIK-машине."),
                        "raised_at_unix_ms": _now_ms()}, "9618")
                elif not bad and raised:
                    raised = False
                    await alerts.forward({
                        "severity": 1, "code": CODE_UP,
                        "message": f"Агент снова на связи, тишина длилась {age} с.",
                        "raised_at_unix_ms": _now_ms()}, "9618")
        except Exception as exc:  # noqa: BLE001 — сторож не имеет права падать
            log.warning("quik.agent_watch.failed", error=str(exc))
        await asyncio.sleep(POLL_SEC)
