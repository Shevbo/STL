"""Избранные наборы бэктестов оператора.

«Избранное» = именованный набор {стратегия, инструмент, параметры, окно дат}
плюс ссылка на СОХРАНЁННЫЙ результат (run_id в backtest_results): открытие из
избранного не пересчитывает прогон, а поднимает готовые сделки из БД; пересчёт
остаётся кнопкой в самой карточке. Хранение — в agent_control (key/value,
`btfav:<имя>`), как robot-name overlay: без новой таблицы и миграций.
"""
from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth

router = APIRouter(prefix="/api/v1/lab", tags=["lab-favorites"])

_PREFIX = "btfav:"
_NAME_RE = re.compile(r"^[\w\-\. а-яА-ЯёЁ]{1,48}$")


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="БД недоступна")
    return pool


class FavoriteBody(BaseModel):
    name: str
    strategy_id: str = ""
    symbol: str
    params: dict
    date_from: str = ""
    date_to: str = ""
    run_id: str = ""          # результат в backtest_results (быстрое открытие)
    net_profit: float | None = None


@router.get("/favorites")
async def list_favorites(request: Request):
    """Все избранные наборы, свежие сверху."""
    _auth(request)
    pool = _pool(request)
    rows = await pool.fetch(
        "SELECT key, value FROM agent_control WHERE key LIKE $1", _PREFIX + "%")
    out = []
    for r in rows:
        try:
            v = json.loads(r["value"])
        except ValueError:
            continue
        v["name"] = r["key"][len(_PREFIX):]
        out.append(v)
    out.sort(key=lambda x: x.get("saved_ms") or 0, reverse=True)
    return {"favorites": out}


@router.post("/favorites")
async def save_favorite(body: FavoriteBody, request: Request):
    """Сохранить/перезаписать набор под именем. Имя — ключ: то же имя = обновление."""
    _auth(request)
    pool = _pool(request)
    name = body.name.strip()
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=422, detail=(
            "Имя: до 48 символов, буквы/цифры/пробел/точка/дефис."))
    if not body.symbol or not isinstance(body.params, dict):
        raise HTTPException(status_code=422, detail="Нужны symbol и params.")
    value = {
        "strategy_id": body.strategy_id, "symbol": body.symbol,
        "params": body.params, "date_from": body.date_from, "date_to": body.date_to,
        "run_id": body.run_id, "net_profit": body.net_profit,
        "saved_ms": int(time.time() * 1000),
    }
    await pool.execute(
        "INSERT INTO agent_control(key,value) VALUES($1,$2) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        _PREFIX + name, json.dumps(value, ensure_ascii=False))
    return {"ok": True, "name": name}


@router.delete("/favorites/{name}")
async def delete_favorite(name: str, request: Request):
    _auth(request)
    pool = _pool(request)
    await pool.execute("DELETE FROM agent_control WHERE key=$1", _PREFIX + name.strip())
    return {"ok": True, "name": name}
