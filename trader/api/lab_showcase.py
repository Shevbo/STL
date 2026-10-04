"""Витрина кампаний бэктеста: чтение готовых файлов сборщика.

Заказ оператора 04.10.2026, разделение работы с окном backtests: они ведут реестр
(`docs/campaigns/registry.json`) и сборщик (`scripts/campaign_showcase_build.py`),
этот модуль ТОЛЬКО ЧИТАЕТ то, что сборщик положил в `data/campaign_showcase/`:

  index.json        — массив карточек для витрины;
  <slug>.json       — подробный отчёт одной кампании.

Почему файлы, а не таблица: данные пишет сборщик целиком и сразу, построчных
правок нет, а файл читается без миграций и без новой связности между окнами. Кэш
по mtime — как у `data/quik_limits.json`: правка сборщика видна сразу, а чтение не
тащит файл с диска на каждый запрос.

ЧЕСТНОСТЬ ОТВЕТА. Витрина, которой нечего показать, не имеет права выглядеть как
витрина с нулевыми карточками. Нет `index.json` — отвечаем `available: false` и
говорим ПРИЧИНУ; битый файл НЕ применяем и тоже называем причину. Содержимое
карточек не правим и не дополняем: пустое поле остаётся пустым, `thumb: null`
остаётся `null` (нет кривой — это не нулевая линия).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

from trader.auth.guard import require_auth

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/lab/showcase", tags=["lab-showcase"])

DIR = Path(os.environ.get("STL_SHOWCASE_DIR", "data/campaign_showcase"))

# Slug — это ИМЯ ФАЙЛА, пришедшее из URL. Без жёсткой проверки `..%2f..%2fetc` стал
# бы путём чтения. Допускаем ровно то, что обещает спека: латиница, цифры, дефис.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _read(path: Path) -> tuple[Any, float]:
    """JSON-файл и его mtime; перечитывается только когда mtime изменился.

    Бросает FileNotFoundError / ValueError — вызывающий превращает их в честную
    причину, а не в 500."""
    key = str(path)
    mtime = path.stat().st_mtime
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1], mtime
    data = json.loads(path.read_text(encoding="utf-8"))
    with _lock:
        _cache[key] = (mtime, data)
    return data, mtime


def read_index() -> dict[str, Any]:
    """Ответ витрины: карточки либо причина, по которой их нет."""
    path = DIR / "index.json"
    try:
        data, mtime = _read(path)
    except FileNotFoundError:
        return {"available": False, "campaigns": [], "built_at_ms": None,
                "reason": "витрина ещё не собрана: сборщик не оставил index.json"}
    except (OSError, ValueError) as exc:
        # Битый файл НЕ применяем: полуразобранный список выглядел бы полной
        # витриной, в которой часть кампаний просто исчезла.
        log.warning("showcase.index_unreadable", error=str(exc), path=str(path))
        return {"available": False, "campaigns": [], "built_at_ms": None,
                "reason": f"index.json не читается: {exc}"}
    if not isinstance(data, list):
        return {"available": False, "campaigns": [], "built_at_ms": None,
                "reason": "index.json не массив карточек: формат сборщика изменился"}
    return {"available": True, "campaigns": data,
            "built_at_ms": int(mtime * 1000), "reason": ""}


@router.get("/campaigns")
async def campaigns(request: Request):
    _auth(request)
    return read_index()


def resolve_slug(slug: str) -> str:
    """Старый slug → новый по `slug_redirects.json` сборщика.

    04.10.2026 backtests перерезали кампании на карточки «логика + инструмент», и
    slug СМЕНИЛИСЬ, а спека обещала, что ссылка живёт вечно. Сборщик кладёт таблицу
    {старый: новый}, редирект делаем здесь. Файла нет или он битый — slug остаётся
    как есть (отсутствие таблицы не должно ронять открытие живых карточек).

    Идём не больше трёх шагов и не возвращаемся в уже пройденный slug: таблица,
    которую правят руками, рано или поздно получит цикл."""
    try:
        table, _ = _read(DIR / "slug_redirects.json")
    except (OSError, ValueError):
        return slug
    if not isinstance(table, dict):
        return slug
    seen = {slug}
    cur = slug
    for _ in range(3):
        nxt = table.get(cur)
        if not isinstance(nxt, str) or not _SLUG_RE.match(nxt) or nxt in seen:
            break
        seen.add(nxt)
        cur = nxt
    return cur


@router.get("/campaigns/{slug}/leaders/{rank}")
async def leader_curve(slug: str, rank: int, request: Request):
    """Кривая лидера вне топ-10: сборщик кладёт её отдельным файлом
    `<slug>.leader-<rank>.json` ({rank, curve, buyhold_curve}), чтобы отчёт на сто
    строк не тащил сто кривых, из которых откроют одну."""
    _auth(request)
    if not _SLUG_RE.match(slug) or not 1 <= rank <= 9999:
        raise HTTPException(status_code=404, detail="Нет такой кампании.")
    try:
        data, _ = _read(DIR / f"{slug}.leader-{rank}.json")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Кривой этого лидера нет.") from None
    except (OSError, ValueError) as exc:
        log.warning("showcase.leader_unreadable", slug=slug, rank=rank, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Кривая лидера не читается: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Кривая лидера не объект: формат изменился.")
    return data


@router.get("/campaigns/{slug}")
async def campaign(slug: str, request: Request):
    _auth(request)
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=404, detail="Нет такой кампании.")
    asked = slug
    path = DIR / f"{slug}.json"
    if not path.exists():
        # Файла нет — может быть, slug старый (см. resolve_slug).
        slug = resolve_slug(slug)
        path = DIR / f"{slug}.json"
    try:
        data, mtime = _read(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Нет такой кампании.") from None
    except (OSError, ValueError) as exc:
        log.warning("showcase.report_unreadable", slug=slug, error=str(exc))
        raise HTTPException(
            status_code=502, detail=f"Отчёт кампании не читается: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502,
                            detail="Отчёт кампании не объект: формат сборщика изменился.")
    out = {**data, "built_at_ms": int(mtime * 1000)}
    # Страница сверяет slug отчёта с адресом и сама переписывает URL на новый.
    if slug != asked:
        out["redirected_from"] = asked
    return out
