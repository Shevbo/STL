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

# ПРОСРОЧКА. Служба построена на допущении «адресат скоро откроется», и до
# 11.08.2026 это допущение НИГДЕ не измерялось: POST /msg отвечал {ok, id},
# отправитель уходил с уверенностью «задача передана», а письмо лежало
# непрочитанным семь часов, потому что то окно просто не запускали. Узнал об
# этом человек, руками заглянув в ЧУЖОЙ ящик. Ни один софт не может открыть
# окно Claude — поэтому чинится не доставка, а СЛЕПОТА: письмо старше порога
# объявляется просроченным, и это видно и отправителю, и любому, кто сейчас
# активен, чтобы он позвал оператора (единственного, кто окно откроет).
_STALE_MS = 4 * 3600 * 1000

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


async def _all_msgs(pool) -> list[dict]:
    """Все письма из хранилища. Битое письмо пропускаем — оно не должно
    ронять весь ящик."""
    rows = await pool.fetch(
        "SELECT key, value FROM agent_control WHERE key LIKE $1", _PREFIX + "%")
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["value"]))
        except (TypeError, ValueError):
            continue
    return out


def _check_agent(name: str, field: str, allow_broadcast: bool = False) -> str:
    n = (name or "").strip().lower()
    if allow_broadcast and n == _BROADCAST:
        return n
    if n not in AGENTS:
        raise HTTPException(status_code=422, detail=(
            f"{field}: неизвестное окно «{name}». Допустимо: "
            + ", ".join(sorted(AGENTS)) + (f", {_BROADCAST}" if allow_broadcast else "")))
    return n


def box_state(rows: list[dict], agent: str, now_ms: int) -> dict:
    """Состояние ящика: сколько непрочитанного и НАСКОЛЬКО ОНО СТАРОЕ.

    Возраст — единственное, что отличает «письмо только что положили» от
    «адресата нет уже полдня». Без него доска показывала и то и другое
    одинаково. Чистая функция: проверяется тестом без БД.
    """
    unread = filter_inbox(rows, agent, unread_only=True)
    oldest = min((m.get("created_ms") or now_ms) for m in unread) if unread else 0
    age_ms = (now_ms - oldest) if oldest else 0
    return {"agent": agent, "unread": len(unread),
            "oldest_age_h": round(age_ms / 3600000, 1) if oldest else 0.0,
            "stale": bool(oldest and age_ms >= _STALE_MS)}


def stale_note(state: dict) -> str:
    """Строка тревоги для просроченного ящика. Одна формулировка на всю службу,
    чтобы отправитель, доска и микропромпт говорили об этом одинаково."""
    if not state.get("stale"):
        return ""
    return (f"ПРОСРОЧКА: у окна «{state['agent']}» {state['unread']} непрочитанных, "
            f"самое старое {state['oldest_age_h']} ч. Окно не забирает почту — "
            "СКАЖИ ОБ ЭТОМ ОПЕРАТОРУ, он единственный, кто его откроет. "
            "Не считай переданную туда работу сделанной и не делай её молча "
            "за него: зоны на то и заведены.")


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
    # ОБРАТНАЯ СВЯЗЬ ОТПРАВИТЕЛЮ. Раньше ответом было {ok, id} — и отправитель
    # уходил считать задачу переданной, не имея НИ ОДНОГО способа узнать, что
    # адресат её не забирает. Теперь письмо сразу возвращает состояние того
    # ящика: если он уже завален и просрочен, это видно в момент отправки, а не
    # через полдня и не по чужой наводке.
    targets = [a for a in AGENTS if a != frm] if to == _BROADCAST else [to]
    rows = await _all_msgs(pool)
    states = [box_state(rows, a, now) for a in targets]
    worst = max(states, key=lambda s: s["oldest_age_h"]) if states else {}
    return {"ok": True, "id": payload["id"], "to": to, "from": frm,
            "recipient": worst, "warning": stale_note(worst)}


@router.get("/inbox/{agent}")
async def inbox(agent: str, request: Request, all: int = 0):
    """Письма окна. По умолчанию только непрочитанные."""
    _auth(request)
    a = _check_agent(agent, "agent")
    msgs = await _all_msgs(_pool(request))
    now = int(time.time() * 1000)
    picked = filter_inbox(msgs, a, unread_only=not all)
    # Возраст едет с КАЖДЫМ письмом: без него семичасовой блокер в списке
    # выглядит ровно как записка, положенную минуту назад.
    for m in picked:
        age = now - int(m.get("created_ms") or now)
        m["age_h"] = round(age / 3600000, 1)
        m["stale"] = bool(not m.get("read_ms") and age >= _STALE_MS)
    return {"agent": a, "zone": AGENTS[a], "count": len(picked),
            "messages": picked, "prompt": inbox_prompt(a),
            "board": [box_state(msgs, x, now) for x in AGENTS]}


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


@router.get("/board")
async def board(request: Request):
    """Вся доска разом: по каждому окну сколько непрочитанного и насколько
    старое, плюс поимённый список ПРОСРОЧЕННЫХ писем.

    Считается на сервере, а не четырьмя вызовами из клиента: доску дёргает
    хук старта сессии, и она должна отвечать одним запросом. Просроченные
    показываются ЛЮБОМУ окну, а не только адресату — окно Claude само себя
    не запустит, поэтому чужой залипший ящик обязан увидеть тот, кто сейчас
    работает, и позвать оператора.
    """
    _auth(request)
    msgs = await _all_msgs(_pool(request))
    now = int(time.time() * 1000)
    states = [box_state(msgs, a, now) for a in AGENTS]
    stuck = []
    for a in AGENTS:
        for m in filter_inbox(msgs, a, unread_only=True):
            age = now - int(m.get("created_ms") or now)
            if age >= _STALE_MS:
                stuck.append({"to": a, "id": m.get("id"), "from": m.get("from"),
                              "topic": m.get("topic") or "", "age_h": round(age / 3600000, 1)})
    return {"board": states, "stale": sorted(stuck, key=lambda s: -s["age_h"]),
            "stale_after_h": _STALE_MS / 3600000,
            "note": ("Просроченное письмо = служба НЕ доставила работу. Скажи оператору, "
                     "какое окно не забирает почту." if stuck else "")}


@router.get("/agents")
async def agents(request: Request):
    """Кто есть кто: список окон и их зон (плюс микропромпт для новичка)."""
    _auth(request)
    return {"agents": AGENTS, "broadcast": _BROADCAST,
            "how": "POST /api/v1/dev/msg  {to, topic, body, sender}; "
                   "GET /api/v1/dev/inbox/<окно>; POST /api/v1/dev/msg/<id>/ack"}
