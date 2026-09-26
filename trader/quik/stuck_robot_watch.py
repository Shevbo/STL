"""Сторож застрявшего робота: агент отклоняет его заявки, а STL молчит.

ЗАЧЕМ. 26.09.2026 агент час отклонял КАЖДУЮ заявку обоих реальных роботов — они
пытались ЗАКРЫТЬ свои шорты, — с «blocked by kill-switch». STL всё это время
считал торговлю разрешённой: блокировка не публиковалась нигде, и заметил её
человек, случайно, в логе раннера на QUIK-машине. Час торговли потерян.

Сторож агента (agent_watch) на это молчит по построению: агент был жив и на
связи. Сторож архива — тоже: данные шли. Здесь сторожится третье: РОБОТ В
ПОЗИЦИИ, которому агент не даёт из неё выйти.

ТРЕВОГА ТОЛЬКО ПРИ СОВПАДЕНИИ ДВУХ ФАКТОВ: агент сообщает blocked И реальный
робот сидит в позиции без сделок дольше порога. По отдельности ни то ни другое
аварией не является: молчащий робот в позиции — норма (сигнала нет), а kill-switch
без открытых позиций не блокирует ничего срочного. Вместе это ровно 26.09.

«НЕ ЗНАЮ» ТРЕВОГОЙ НЕ СЧИТАЕТСЯ: blocked=None (агент старее релиза поля) не
повод будить оператора, но и не повод считать торговлю разрешённой — это видно
в снимке правды и в scripts/pos.py, а не здесь.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

SILENT_SEC = 600        # 10 минут без сделок при блокировке = робот не может выйти
POLL_SEC = 60
CODE_DOWN = "ROBOT_BLOCKED_STUCK"
CODE_UP = "ROBOT_BLOCKED_STUCK_RECOVERED"


def _last_fill_ms(robot: dict[str, Any]) -> int:
    return max((int(f.get("ts_unix_ms") or 0) for f in robot.get("recent_fills") or ()),
               default=0)


def stuck(robots: list[dict[str, Any]], agent_blocked: bool | None,
          market_open: bool | None, now_ms: int,
          silent_sec: int = SILENT_SEC) -> list[dict[str, Any]]:
    """Реальные роботы, которые сидят в позиции, а агент их заявки отклоняет.

    Рынок закрыт — тишина штатна. «Неизвестно» у оракула трактуется защитно, как
    в agent_watch: неизвестность на стороне ISS не повод считать рынок закрытым.
    Сделок у робота нет ВОВСЕ — считаем тишину уже достигшей порога: пустой список
    филлов при открытой позиции это не свежая работа, а её отсутствие."""
    if agent_blocked is not True or market_open is False:
        return []
    out = []
    for r in robots:
        if str(r.get("mode") or "") != "real" or r.get("paused"):
            continue
        pos = int(r.get("position") or 0)
        if not pos:
            continue
        last = _last_fill_ms(r)
        idle = int((now_ms - last) / 1000) if last else silent_sec
        if idle >= silent_sec:
            out.append({"id": str(r.get("id") or ""), "symbol": str(r.get("symbol") or ""),
                        "position": pos, "idle_sec": idle})
    return out


async def watch(state) -> None:
    """Фоновая задача: раз в минуту сверяет блокировку агента с роботами в позиции."""
    raised = False
    complained = False
    while True:
        try:
            store = getattr(state, "quik_store", None)
            alerts = getattr(state, "quik_alerts", None)
            if store is None or alerts is None:
                # Громко и ровно один раз: сторож без канала это не сторож.
                if not complained:
                    complained = True
                    log.error("quik.stuck_robot_watch.not_wired",
                              store=store is not None, alerts=alerts is not None)
            else:
                ms = getattr(state, "market_session", None) or {}
                status = store.agent_status(None) or {}
                limits = store.limits_state(None) or {}
                now_ms = int(time.time() * 1000)
                rows = stuck(status.get("robots") or [], limits.get("blocked"),
                             ms.get("open"), now_ms)
                if rows and not raised:
                    raised = True
                    who = "; ".join(f"{r['id']} {r['symbol']} {r['position']:+d} "
                                    f"молчит {r['idle_sec'] // 60} мин" for r in rows)
                    await alerts.forward({
                        "severity": 3, "code": CODE_DOWN,
                        "message": ("АГЕНТ БЛОКИРУЕТ ТОРГОВЛЮ: kill-switch отклоняет "
                                    "заявки, роботы в позиции не могут её закрыть — "
                                    f"{who}. Снимается только перезапуском агента."),
                        "raised_at_unix_ms": now_ms}, "9618")
                elif not rows and raised:
                    raised = False
                    await alerts.forward({
                        "severity": 1, "code": CODE_UP,
                        "message": "Роботы снова торгуют: блокировки агента нет.",
                        "raised_at_unix_ms": now_ms}, "9618")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — сторож не имеет права падать
            log.warning("quik.stuck_robot_watch.failed", error=str(exc))
        await asyncio.sleep(POLL_SEC)
