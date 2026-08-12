#!/usr/bin/env python3
"""Хук почты окна. Вешается на SessionStart И на UserPromptSubmit.

ЧЕМ ЭТОТ ХУК ОТЛИЧАЕТСЯ ОТ ПРЕДЫДУЩЕГО. Старый ходил по ssh за доской и стоил
1.8 с на вызов, поэтому висеть на каждом вводе не мог — и письмо, пришедшее в
СЕРЕДИНЕ сессии, окно не видело часами. Этот читает локальный слепок (~40 мс) и
поэтому может дёргаться на каждый ввод.

ЦЕНА В ТОКЕНАХ, а не в секундах — вот настоящее ограничение хука на каждый ввод.
Поэтому здесь три режима вывода:
  ящик пуст                  -> НЕ ПЕЧАТАЕТ НИЧЕГО (ноль токенов, обычный случай)
  пришло новое               -> полный текст, но не больше MAX_FULL писем
  показывали, ack не сделан  -> ОДНА строка-напоминание
Так контекст не вытесняется тем самым правилом, ради которого хук и вешали.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devmail_spool as spool

MAX_FULL = 5            # писем печатаем целиком за один показ
MAX_CHARS = 700         # обрезка тела письма
NAG_EVERY_S = 1800      # напоминание про непод-ackнутое — не чаще раза в 30 мин


def _nag_mark(window: str) -> Path:
    return spool.ROOT / f"{window}.nag"


def _nag_due(window: str) -> bool:
    """Напоминание не на каждый ввод, а раз в полчаса: иначе оно само станет
    шумом, который перестают читать."""
    try:
        last = float(_nag_mark(window).read_text().strip() or 0)
    except Exception:
        last = 0.0
    return time.time() - last >= NAG_EVERY_S


def _nag_stamp(window: str) -> None:
    """Метку ставим ФАКТОМ ПЕЧАТИ, а не фактом проверки. Иначе показ на
    SessionStart (он проходит без проверки таймера) не сдвигал бы окно, и
    следующий же ввод оператора получил бы то же напоминание снова."""
    try:
        m = _nag_mark(window)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(str(time.time()))
    except Exception:
        pass


def build_output(window: str, state, event: str) -> str:
    parts: list[str] = []
    age = spool.sync_age_s(state)
    if age > spool.STALE_SYNC_S:
        # Молчать нельзя: окно решит, что писем нет, а это умер фоновый синк.
        if event == "SessionStart" or _nag_due(window):
            how = f"python3 scripts/devmail_sync.py --window {window} --loop 45"
            parts.append(f"ПОЧТА НЕ СИНХРОНИЗИРУЕТСЯ (слепок протух). Подними фон: {how}")
        return "\n".join(parts)

    shown = spool.shown_ids(window)
    fresh, pending = spool.split_unread(state, shown)
    inbox = (state or {}).get("inbox") or {}

    if fresh:
        parts.append(f"## Почта окна «{window}»: {len(fresh)} новых")
        prompt = inbox.get("prompt")
        if prompt and event == "SessionStart":
            parts.append(prompt)
        for m in fresh[:MAX_FULL]:
            body = str(m.get("body") or "")
            if len(body) > MAX_CHARS:
                body = body[:MAX_CHARS] + "…"
            head = (f"[id {m.get('id')}] от {m.get('from')} · "
                    f"{m.get('topic') or 'без темы'}")
            if m.get("stale"):
                head += f" · ПРОСРОЧЕНО ({m.get('age_h')} ч)"
            parts.append(head)
            parts.extend(f"> {line}" for line in body.splitlines())
        if len(fresh) > MAX_FULL:
            parts.append(f"(ещё {len(fresh) - MAX_FULL} — GET /api/v1/dev/inbox/{window})")
        parts.append("Разобрал письмо -> POST /api/v1/dev/msg/<id>/ack, иначе оно висит.")
        spool.mark_shown(window, [str(m.get("id")) for m in fresh])
        # Только что показали письмо целиком — напоминать о нём на СЛЕДУЮЩЕМ же
        # вводе незачем, это и есть шум. Отсчёт получаса начинаем отсюда.
        _nag_stamp(window)
    elif pending and (event == "SessionStart" or _nag_due(window)):
        ids = ", ".join(str(m.get("id")) for m in pending[:5])
        parts.append(f"Почта «{window}»: {len(pending)} письмо(писем) ждут ack: {ids}")
        _nag_stamp(window)

    foreign = spool.foreign_stale(state, window)
    if foreign and (event == "SessionStart" or _nag_due(window)):
        for s in foreign:
            parts.append(f"! окно «{s['agent']}» не забирает почту: {s['unread']} "
                         f"непрочитанных, самое старое {s['oldest_age_h']} ч — "
                         "скажи оператору, сам себя Claude не запустит")
        _nag_stamp(window)
    return "\n".join(parts)


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        event = json.loads(raw or "{}").get("hook_event_name") or "UserPromptSubmit"
    except Exception:
        event = "UserPromptSubmit"

    window = os.environ.get("STL_WINDOW", "").strip()
    if not window:
        return 0                      # окно не объявлено — хук молчит, не мешает
    out = build_output(window, spool.load_state(window), event)
    if not out:
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": out}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
