"""Сторож архива рынка: биржа торгует, а данные не пишутся.

ЗАЧЕМ. 25.09.2026 сбор умер в 16:29 — на VDS остановился Lua-скрипт, — и никто
этого не заметил до одиннадцати вечера. За день в архив легло 39 тысяч записей
стакана вместо обычных 310 тысяч: восемь торговых часов потеряны безвозвратно.
Стакан существует ТОЛЬКО в момент снимка: ни биржа, ни ISS не отдают его задним
числом, восстановить провал нечем.

Сторож ленты (agent_watch) на это молчал по построению: агент был жив и на связи,
молчал именно сбор. Здесь сторожится другое — РАСТЁТ ЛИ АРХИВ, когда рынок открыт.

ГЕЙТ ПО СЕССИИ. Ночью и на закрытии тишина штатна. Оракул `market_session` знает,
торгует ли FORTS прямо сейчас; его «неизвестно» трактуется защитно — тревожим,
потому что неизвестность на стороне ISS не повод считать архив исправным.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

SILENT_SEC = 300        # 5 минут без единой записи при открытом рынке = сбор встал
POLL_SEC = 60
CODE_DOWN = "ARCHIVE_SILENT"
CODE_UP = "ARCHIVE_SILENT_RECOVERED"


def verdict(written: int, last_written: int, last_change_ms: int, market_open: bool | None,
            now_ms: int, silent_sec: int = SILENT_SEC) -> tuple[bool, int, int]:
    """(тревожить?, возраст тишины в секундах, новая метка изменения).

    Судим по СЧЁТЧИКУ записанных кадров, а не по файлам на диске: файл растёт
    рывками (буфер, flush, сжатие при ротации), и «размер не изменился» ещё не
    значит, что данные не идут."""
    if written != last_written:
        return False, 0, now_ms
    if market_open is False:
        return False, 0, last_change_ms or now_ms
    since = last_change_ms or now_ms
    age = int((now_ms - since) / 1000)
    return age >= silent_sec, age, since


def _recorder(state) -> Any:
    """Архиватор живёт внутри gRPC-сервиса, а не на app.state: ищем его там, где
    он есть, чтобы сторож не зависел от порядка проводки в чужом lifespan."""
    rec = getattr(state, "quik_recorder", None)
    if rec is not None:
        return rec
    srv = getattr(state, "quik_server", None)
    for attr in ("recorder",):
        if srv is not None and getattr(srv, attr, None) is not None:
            return getattr(srv, attr)
    servicer = getattr(srv, "servicer", None) if srv is not None else None
    return getattr(servicer, "recorder", None)


async def watch(state) -> None:
    """Фоновая задача: раз в минуту сверяет рост архива с состоянием рынка."""
    raised = False
    last_written = -1
    last_change_ms = int(time.time() * 1000)
    while True:
        try:
            rec = _recorder(state)
            alerts = getattr(state, "quik_alerts", None)
            if rec is None or not getattr(rec, "enabled", False):
                pass                      # архив выключен настройкой — сторожить нечего
            else:
                ms = getattr(state, "market_session", None) or {}
                now_ms = int(time.time() * 1000)
                written = int((rec.stats or {}).get("written") or 0)
                bad, age, last_change_ms = verdict(
                    written, last_written, last_change_ms, ms.get("open"), now_ms)
                last_written = written
                if bad and not raised and alerts is not None:
                    raised = True
                    await alerts.forward({
                        "severity": 2, "code": CODE_DOWN,
                        "message": (f"Архив рынка не пополняется {age} с, а биржа торгует. "
                                    "Стакан пишется только в момент снимка и задним "
                                    "числом не восстанавливается — проверь Lua-скрипт "
                                    "в QUIK и связь агента."),
                        "raised_at_unix_ms": now_ms}, "9618")
                elif not bad and raised:
                    raised = False
                    if alerts is not None:
                        await alerts.forward({
                            "severity": 1, "code": CODE_UP,
                            "message": "Архив рынка снова пополняется.",
                            "raised_at_unix_ms": now_ms}, "9618")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — сторож не имеет права падать
            log.warning("quik.archive_watch.failed", error=str(exc))
        await asyncio.sleep(POLL_SEC)
