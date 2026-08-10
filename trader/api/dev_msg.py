"""Почта между окнами разработки STL (real-trade / backtests / ui-ux).

ЗАЧЕМ. Репозиторий и хостер ведут ТРИ параллельных сессии Claude, у каждой своя
зона (см. CLAUDE.md, «Three parallel Claude windows»). Правка в чужой зоне не
делается, а ПЕРЕДАЁТСЯ владельцу — но передавать её было нечем: оператор носил
текст между окнами руками. Это почта: положил задачу, владелец забрал.

ПОЧЕМУ БЕЗ ТАБЛИЦЫ. Хранение — `agent_control` (key/value), как у lab_favorites:
переписка идёт единицами сообщений в день, ради неё заводить таблицу и миграцию
незачем. Ключ `devmsg:<ms>:<rand>` сортируется по времени сам.

МИКРОПРОМПТ. Ответ инбокса НЕСЁТ В СЕБЕ инструкцию, что с ним делать (поле
`prompt`). Свежая сессия, дёрнувшая свой ящик, узнаёт протокол из самого ответа
и не обязана сперва прочитать документацию — иначе почта работала бы только у
того, кто и так про неё знает.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth

router = APIRouter(prefix="/api/v1/dev", tags=["dev-msg"])

_PREFIX = "devmsg:"
_KEEP = 200                      # сколько сообщений храним, старые подрезаем

# Окна и их зоны. Список ЗАКРЫТ: опечатка в адресате означала бы письмо,
# которое никто никогда не прочитает, а отправитель считал бы задачу переданной.
AGENTS: dict[str, str] = {
    "real-trade": "proto/, quik_agent/, robot_runner/, ~/quik_build, QUIK VDS, "
                  "релизы агента, умные заявки, арминг, рестарты shectory-trader",
    "backtests": "trader/lab/, scripts/, i9, кампании перебора",
    "ui-ux": "frontend/, trader/api/, companion/, все экраны STL",
    "operator": "человек (Boris): решения по деньгам, VDS, доступам",
}
_BROADCAST = "all"


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="БД недоступна")
    return pool


def _check_agent(name: str, field: str, allow_broadcast: bool = False) -> str:
    n = (name or "").strip().lower()
    if allow_broadcast and n == _BROADCAST:
        return n
    if n not in AGENTS:
        raise HTTPException(status_code=422, detail=(
            f"{field}: неизвестное окно «{name}». Допустимо: "
            + ", ".join(sorted(AGENTS)) + (f", {_BROADCAST}" if allow_broadcast else "")))
    return n


def inbox_prompt(agent: str) -> str:
    """Микропромпт: что окну делать с прочитанным. Едет ВНУТРИ ответа."""
    return (
        f"Ты окно «{agent}». Твоя зона: {AGENTS.get(agent, '?')}. "
        "Это письма от соседних окон STL. Правило: работу в СВОЕЙ зоне делаешь сам, "
        "работу в чужой — передаёшь письмом её владельцу, а не делаешь молча. "
        "По каждому письму: выполни и ответь отправителю "
        "(POST /api/v1/dev/msg {\"to\": <отправитель>, \"topic\": ..., \"body\": ...}), "
        "затем POST /api/v1/dev/msg/{id}/ack — пока не подтверждено, письмо висит "
        "непрочитанным и его увидит следующая сессия. Если письмо не про твою зону — "
        "перешли владельцу и тоже подтверди. Ничего не выдумывай за отправителя: "
        "непонятное уточни ответным письмом."
    )


def filter_inbox(rows: list[dict], agent: str, unread_only: bool) -> list[dict]:
    """Письма для окна: адресованные лично ему и широковещательные.

    Свои же отправления в инбокс НЕ попадают (иначе окно вечно отвечало бы само
    себе на бродкаст). Чистая функция — вся выборка проверяется тестом без БД.
    """
    out = []
    for m in rows:
        to = (m.get("to") or "").lower()
        frm = (m.get("from") or "").lower()
        if to != agent and not (to == _BROADCAST and frm != agent):
            continue
        if unread_only and m.get("read_ms"):
            continue
        out.append(m)
    return sorted(out, key=lambda m: m.get("created_ms") or 0)


class MsgBody(BaseModel):
    to: str
    topic: str = ""
    body: str
    sender: str = ""          # кто пишет; пустое = «operator» (человек через curl)


@router.post("/msg")
async def send_msg(body: MsgBody, request: Request):
    """Положить письмо в ящик другого окна."""
    _auth(request)
    to = _check_agent(body.to, "to", allow_broadcast=True)
    frm = _check_agent(body.sender or "operator", "sender")
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="body: пустое письмо")
    now = int(time.time() * 1000)
    key = f"{_PREFIX}{now}:{uuid.uuid4().hex[:6]}"
    payload = {"id": key[len(_PREFIX):], "from": frm, "to": to,
               "topic": (body.topic or "").strip()[:120], "body": text[:8000],
               "created_ms": now, "read_ms": 0}
    pool = _pool(request)
    await pool.execute(
        "INSERT INTO agent_control(key, value) VALUES($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, json.dumps(payload, ensure_ascii=False))
    # Подрезаем хвост: ящик — не архив, старое читать некому.
    old = await pool.fetch(
        "SELECT key FROM agent_control WHERE key LIKE $1 ORDER BY key DESC OFFSET $2",
        _PREFIX + "%", _KEEP)
    for r in old:
        await pool.execute("DELETE FROM agent_control WHERE key = $1", r["key"])
    return {"ok": True, "id": payload["id"], "to": to, "from": frm}


@router.get("/inbox/{agent}")
async def inbox(agent: str, request: Request, all: int = 0):
    """Письма окна. По умолчанию только непрочитанные."""
    _auth(request)
    a = _check_agent(agent, "agent")
    rows = await _pool(request).fetch(
        "SELECT key, value FROM agent_control WHERE key LIKE $1", _PREFIX + "%")
    msgs = []
    for r in rows:
        try:
            msgs.append(json.loads(r["value"]))
        except (TypeError, ValueError):
            continue          # битое письмо не должно ронять весь ящик
    picked = filter_inbox(msgs, a, unread_only=not all)
    return {"agent": a, "zone": AGENTS[a], "count": len(picked),
            "messages": picked, "prompt": inbox_prompt(a)}


@router.post("/msg/{msg_id}/ack")
async def ack(msg_id: str, request: Request):
    """Пометить письмо прочитанным."""
    _auth(request)
    if not re.fullmatch(r"[0-9]+:[0-9a-f]{6}", msg_id or ""):
        raise HTTPException(status_code=422, detail="id: неверный формат")
    pool = _pool(request)
    key = _PREFIX + msg_id
    row = await pool.fetchrow("SELECT value FROM agent_control WHERE key = $1", key)
    if row is None:
        raise HTTPException(status_code=404, detail="письмо не найдено")
    m = json.loads(row["value"])
    m["read_ms"] = int(time.time() * 1000)
    await pool.execute("UPDATE agent_control SET value = $2 WHERE key = $1",
                       key, json.dumps(m, ensure_ascii=False))
    return {"ok": True, "id": msg_id}


@router.get("/agents")
async def agents(request: Request):
    """Кто есть кто: список окон и их зон (плюс микропромпт для новичка)."""
    _auth(request)
    return {"agents": AGENTS, "broadcast": _BROADCAST,
            "how": "POST /api/v1/dev/msg  {to, topic, body, sender}; "
                   "GET /api/v1/dev/inbox/<окно>; POST /api/v1/dev/msg/<id>/ack"}
