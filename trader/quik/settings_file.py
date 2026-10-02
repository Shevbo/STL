"""ОДИН файл настроек живой торговли, в известном месте: `data/quik_limits.json`.

Распоряжение оператора 02.10.2026: «сделай все настройки в одном файле в известном
месте, который также правится в настройках вотчера в веб STL».

ЗАЧЕМ. До этого файла предел жил в переменной окружения `~/.shectory_trade.env` на
хостере, читался ОДИН раз при старте и менялся только рестартом боевого процесса.
В тот же день это стоило простоя: сетка на GZZ6 уткнулась в `max_working_contracts`
(в работе 85 при пределе 70), и чтобы поднять число, пришлось править env и
перезапускать STL посреди торгов. Настройка, ради которой нужен рестарт торговли,
— не настройка.

ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ. Здесь операционные пределы STL: белый список, объём на
заявку, объём в работе, дневной кап заявок, коллар, мастер-флаг. Здесь НЕТ
агентского `agent_config.json` на VDS и НЕТ `shectory_trade_config.lua` — они
живут на машине QUIK, и это намеренно: агентские пределы это БЭКСТОП, пуш из STL
умеет только ужесточать. Один файл на всё означал бы, что предохранитель
расширяется оттуда же, откуда торгуют.

ПРАВИЛА ЧТЕНИЯ. Файл перечитывается по mtime на каждом обращении, поэтому правка
действует СРАЗУ, без рестарта. Файла нет — он создаётся из окружения при первом
чтении: молча переехать на новые значения нельзя, у оператора уже настроено.
Битый файл НЕ применяется: берём окружение и кричим в лог, потому что пустые
пределы это разрешить всё.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import structlog

log = structlog.get_logger(__name__)

PATH = "data/quik_limits.json"

# Поля и их типы. Перечисление ЯВНОЕ: чужой ключ в файле не должен становиться
# пределом, а забытый — исчезать молча.
FIELDS: dict[str, type] = {
    "trading_enabled": bool,
    "max_contracts_per_order": int,
    "max_working_contracts": int,
    "price_collar_frac": float,
    "daily_order_cap": int,
}
LIST_FIELD = "instrument_whitelist"

# RLock, а не Lock: load() держит замок и зовёт save(), когда файла ещё нет —
# с обычным замком это намертво вешает ПЕРВУЮ же проверку лимитов, то есть
# торговлю целиком. Поймано тестами до боя.
_lock = threading.RLock()
_cache: dict[str, Any] | None = None
_cache_mtime: float = -1.0


def _from_env(settings) -> dict[str, Any]:
    wl = [s.strip() for s in (settings.quik_instrument_whitelist or "").split(",")
          if s.strip()]
    return {
        "trading_enabled": bool(settings.quik_trading_enabled),
        "max_contracts_per_order": int(settings.quik_max_contracts_per_order),
        "max_working_contracts": int(settings.quik_max_working_contracts),
        "price_collar_frac": float(settings.quik_price_collar_frac),
        "daily_order_cap": int(settings.quik_daily_order_cap),
        LIST_FIELD: wl,
    }


def _coerce(raw: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Привести значения к типам, недостающее взять из base. Чужие ключи отбросить."""
    out = dict(base)
    for key, typ in FIELDS.items():
        if key in raw:
            try:
                out[key] = typ(raw[key])
            except (TypeError, ValueError):
                log.warning("quik.limits.bad_value", field=key, value=raw.get(key))
    wl = raw.get(LIST_FIELD)
    if isinstance(wl, list):
        out[LIST_FIELD] = [str(c).strip() for c in wl if str(c).strip()]
    elif isinstance(wl, str):
        out[LIST_FIELD] = [c.strip() for c in wl.split(",") if c.strip()]
    return out


def load(settings, path: str | None = None) -> dict[str, Any]:
    """Текущие настройки. Перечитывает файл при изменении mtime.

    `path` разрешается В МОМЕНТ ВЫЗОВА, а не в сигнатуре: значение по умолчанию
    вычисляется один раз при импорте, и подменить PATH (тесты, другой разворот)
    было бы невозможно — молча читался бы старый файл."""
    global _cache, _cache_mtime
    path = path or PATH
    base = _from_env(settings)
    with _lock:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # ФАЙЛА НЕТ — ЖИВЁМ НА ОКРУЖЕНИИ И НИЧЕГО НЕ ПИШЕМ.
            #
            # Сначала я создавал файл прямо здесь, «чтобы оператор увидел свои
            # значения». Чтение, которое пишет на диск, тут же сломало четыре
            # чужих теста: они задают пределы своей настройкой, а получали файл,
            # оставшийся от соседнего прогона. Проверка лимитов происходит на
            # КАЖДУЮ заявку — у неё не должно быть побочных действий вообще.
            # Файл создаёт запись настроек (save) и явный ensure() при старте.
            _cache, _cache_mtime = base, -1.0
            return dict(base)
        if _cache is not None and mtime == _cache_mtime:
            return dict(_cache)
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError("не объект")
            merged = _coerce(raw, base)
        except Exception as exc:  # noqa: BLE001
            # БИТЫЙ ФАЙЛ НЕ ПРИМЕНЯЕМ. Пустые пределы это «разрешить всё», а это
            # опаснее любого отказа: лучше вернуться к окружению и кричать.
            log.error("quik.limits.unreadable", path=path, error=str(exc))
            _cache, _cache_mtime = base, mtime
            return dict(base)
        _cache, _cache_mtime = merged, mtime
        return dict(merged)


def save(values: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    """Записать настройки целиком. Пишем через временный файл: оборванная запись
    оставила бы боевые пределы наполовину написанными."""
    global _cache, _cache_mtime
    path = path or PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(values, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
    with _lock:
        _cache, _cache_mtime = dict(values), os.path.getmtime(path)
    return dict(values)


def ensure(settings, path: str | None = None) -> dict[str, Any]:
    """Создать файл из окружения, если его ещё нет. Зовётся ОДИН раз при старте.

    Отдельно от load(), потому что чтение пределов не имеет права писать на диск:
    оно происходит на каждую заявку.
    """
    path = path or PATH
    if os.path.exists(path):
        return load(settings, path)
    values = _from_env(settings)
    try:
        save(values, path)
        log.info("quik.limits.seeded_from_env", path=path, values=values)
    except Exception as exc:  # noqa: BLE001 — не смогли создать, живём на окружении
        log.warning("quik.limits.seed_failed", path=path, error=str(exc))
    return values
