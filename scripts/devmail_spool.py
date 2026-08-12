#!/usr/bin/env python3
"""Локальный слепок почтового ящика окна. Только stdlib, только чтение с диска.

ЗАЧЕМ ОТДЕЛЬНЫЙ СЛОЙ. Ящик живёт на хостере, а окна Claude — на другой машине.
Пока за письмами ходил сам хук, каждый показ почты стоил ssh-рукопожатие (у нас
замерено 1.8 с). Хук, который дёргается на КАЖДЫЙ ввод оператора, не имеет права
ходить в сеть вообще. Поэтому транспорт и чтение разделены:

    devmail_sync.py   (фон, раз в 45 с) — ходит по ssh, пишет этот слепок
    devmail_hook.py   (на каждый ввод)  — только читает слепок, ~40 мс

ПОЧЕМУ ЭТО НЕ ВТОРАЯ ПОЧТА. Источник правды остаётся один — agent_control на
хостере. Слепок не хранит состояние, которого нет на сервере: ack по-прежнему
делается через POST /api/v1/dev/msg/<id>/ack, а «показано» — чисто локальная
пометка, чтобы одно и то же письмо не печаталось в контекст дважды.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(os.environ.get("STL_DEVMAIL_ROOT", Path.home() / ".stl-devmail"))
# Слепок старше этого возраста означает, что фоновый синк умер. Молча показывать
# устаревший ящик нельзя: окно решит, что писем нет, а они есть.
STALE_SYNC_S = int(os.environ.get("STL_DEVMAIL_STALE_SYNC_S", "300"))


def state_file(window: str) -> Path:
    return ROOT / f"{window}.json"


def shown_file(window: str) -> Path:
    return ROOT / f"{window}.shown.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def save_state(window: str, inbox: dict) -> None:
    """Записать свежий слепок. Вызывает ТОЛЬКО фоновый синк."""
    _write_atomic(state_file(window), {
        "window": window,
        "updated_ms": int(time.time() * 1000),
        "inbox": inbox,
    })


def load_state(window: str) -> dict | None:
    return _read_json(state_file(window), None)


def sync_age_s(state: dict | None, now: float | None = None) -> float:
    if not state:
        return float("inf")
    now = time.time() if now is None else now
    return max(0.0, now - (state.get("updated_ms", 0) / 1000.0))


def shown_ids(window: str) -> set[str]:
    return set(_read_json(shown_file(window), []) or [])


def mark_shown(window: str, ids) -> None:
    """Пометить письма как уже напечатанные в контекст.

    Держим только последние 200 id: файл не должен расти вечно, а письмо,
    вытесненное из этого списка, в худшем случае напечатается ещё раз — это
    безопасная сторона ошибки (лучше повтор, чем пропажа).
    """
    cur = list(_read_json(shown_file(window), []) or [])
    cur.extend(i for i in ids if i not in cur)
    _write_atomic(shown_file(window), cur[-200:])


def split_unread(state: dict, shown: set[str]) -> tuple[list, list]:
    """Разложить непрочитанное на «ещё не показывали» и «висит, ждёт ack».

    Это и есть ответ на возражение «хук на каждый ввод сожрёт контекст»: полный
    текст печатается ОДИН раз, дальше от письма остаётся одна строка-напоминание,
    а при пустом ящике хук не печатает ничего и стоит ноль токенов.
    """
    msgs = ((state or {}).get("inbox") or {}).get("messages") or []
    fresh = [m for m in msgs if str(m.get("id")) not in shown]
    pending = [m for m in msgs if str(m.get("id")) in shown]
    return fresh, pending


def foreign_stale(state: dict, window: str) -> list:
    """Чужие залипшие ящики. Окно, которое работает, обязано увидеть, что сосед
    свою почту не забирает, и сказать оператору — сам себя Claude не запустит."""
    board = ((state or {}).get("inbox") or {}).get("board") or []
    return [s for s in board if s.get("stale") and s.get("agent") != window]
