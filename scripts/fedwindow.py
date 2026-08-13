#!/usr/bin/env python3
"""Почта окна: один цикл и один хук. Заменяет всю прежнюю машинерию.

БЫЛО (вычищено 13.08.2026): фоновый синк по крону, спул-слой, отдельный хук,
автопилот с замком, три задания планировщика и две строки crontab. Всё это
существовало, чтобы обойти медленный транспорт (ssh 1.8 с, слепок раз в минуту)
и мёртвые окна. Транспорт Lineman ходит за 390 мс — обходить больше нечего.

СТАЛО, ровно две части:
    loop   держит опрос ящика окна и ДОПИСЫВАЕТ письма в локальный файл;
    show   печатает то, что ещё не показывал (вешается на хук окна).

Почему всё же файл посередине: сессия Claude не может держать свой asyncio-таск,
она выполняется только на ввод. Цикл живёт рядом процессом, хук читает готовое —
и стоит миллисекунды, потому что в сеть не ходит.

    python3 scripts/fedwindow.py loop           фоном, один на окно
    python3 scripts/fedwindow.py show           из хука
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fedmail

REPO = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("STL_FED_STATE", Path.home() / ".stl-fedmail"))


def window_id() -> str:
    """Кто я. Порядок опознания тот же, что в CLAUDE.md: переменная, затем
    личность папки. Не опознались — молчим, а не гадаем."""
    env = os.environ.get("STL_FED_ID") or os.environ.get("STL_WINDOW")
    if env:
        return env if env.startswith("stl-") else f"stl-{env}"
    local = REPO / "CLAUDE.local.md"
    if local.exists():
        m = re.search(r"^# ТЫ ОКНО «([^»]+)»", local.read_text(encoding="utf-8"), re.M)
        if m:
            name = m.group(1)
            return name if name.startswith("stl-") else f"stl-{name}"
    return ""


def _files(win: str) -> tuple[Path, Path]:
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE / f"{win}.jsonl", STATE / f"{win}.cursor"


def cmd_loop(win: str) -> int:
    box, cur = _files(win)
    since = int(cur.read_text().strip() or 0) if cur.exists() else 0

    def store(m: dict) -> None:
        with box.open("a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
        cur.write_text(str(int(m.get("id") or 0)))

    print(f"[fedwindow] {win}: цикл запущен, курсор {since}", flush=True)
    asyncio.run(fedmail.run_loop(win, store, since))
    return 0


def cmd_show(win: str) -> int:
    """Печатает НЕПОКАЗАННОЕ и двигает метку. Пусто — молчит: хук висит на
    каждом вводе, и болтливость здесь оплачивается контекстом окна."""
    box, _ = _files(win)
    seen_f = STATE / f"{win}.shown"
    if not box.exists():
        return 0
    seen = int(seen_f.read_text().strip() or 0) if seen_f.exists() else 0
    fresh, top = [], seen
    for line in box.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        mid = int(m.get("id") or 0)
        if mid > seen:
            fresh.append(m)
            top = max(top, mid)
    if not fresh:
        return 0
    out = [f"## Почта окна «{win}»: {len(fresh)} новых"]
    for m in fresh[-5:]:
        body = str(m.get("message") or "")
        if len(body) > 700:
            body = body[:700] + "…"
        out.append(f"[id {m.get('id')}] от {m.get('from')}")
        out.extend(f"> {ln}" for ln in body.splitlines())
    out.append(f"Ответить: python3 scripts/fedmail.py send <кому> '<json>' --me {win}")
    seen_f.write_text(str(top))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "\n".join(out)}}, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "show"
    win = window_id()
    if not win:
        return 0                       # окно не опознано — не мешаем
    return cmd_loop(win) if cmd == "loop" else cmd_show(win)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
