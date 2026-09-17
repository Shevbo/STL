"""Почта окна в живую сессию Claude: показ новых писем на SessionStart и UserPromptSubmit.

ТОЛЬКО ПОКАЗ. Хук никогда не отвечает, не подтверждает (ack) и не запускает Claude.
18.08.2026 автономную почту вычистили целиком (коммит 438479e): мосты окон отвечали
на подтверждения друг друга, за ночь 792 письма, каждое запускало claude -p, лимит
подписки выжжен к 03:40. Урок: два участника, каждый из которых отвечает на ответы
другого, — генератор, и глушится он только отсутствием автоответа. Поэтому отвечает
сама сессия с оператором, а в подсказке прямо сказано: на ответ/подтверждение без
новой просьбы не отвечать.

ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК. Хук прописан в .claude/settings.local.json окна, а не живёт в
памяти сессии, как крон CronCreate, который умирает вместе с ней.

ЦЕНА. ssh к хостеру ~2 с, поэтому на UserPromptSubmit ящик опрашивается не чаще раза в
FETCH_EVERY_S; на SessionStart всегда. Пустой ящик — хук молчит, 0 токенов. Новое
письмо печатается целиком один раз; показанное, но не подтверждённое — одной строкой не
чаще раза в REMIND_EVERY_S.

НЕ МОЛЧИТ ПРО СЛОМ. Хостер не ответил — одна строка «ящик не проверен» (не чаще раза в
REMIND_EVERY_S): пустой ящик и мёртвый транспорт обязаны выглядеть по-разному.

    echo '{"hook_event_name":"SessionStart"}' | python scripts/mail_hook.py backtests
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

FETCH_EVERY_S = 600
REMIND_EVERY_S = 1800
MAX_LETTERS = 5
MAX_BODY = 1500
STATE_DIR = os.path.join(os.path.expanduser("~"), ".stl-mailhook")

NO_LOOP = ("Не отвечай письмом на письмо, которое само является ответом или "
           "подтверждением без новой просьбы: такое только подтверждай ack. Ответ на "
           "ответ между окнами — генератор писем (инцидент 18.08.2026).")


def fetch(window: str) -> dict:
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "hoster",
           f"bash ~/apps/shectory-trader/scripts/devmsg.sh inbox-json {window}"]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=25)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    if "error" in data:
        raise RuntimeError(f"API {data['error']}")
    return data


def _due(state: dict, key: str, now: float) -> bool:
    """Пора ли снова: ни разу не было — пора (сравнение с 0 ломалось на малом now)."""
    last = state.get(key)
    return last is None or now - last >= REMIND_EVERY_S


def render(data: dict | None, state: dict, event: str, now: float, window: str,
           err: str | None = None) -> list[str]:
    """Чистая функция: что напечатать и как обновить state (меняется на месте)."""
    lines: list[str] = []
    if data is None:
        if err and _due(state, "last_err", now):
            state["last_err"] = now
            lines.append(f"почта окна {window}: ящик НЕ проверен, хостер не ответил ({err})")
        return lines

    unread = [m for m in data.get("messages") or [] if not m.get("read_ms")]
    shown = set(state.get("shown") or [])
    new = [m for m in unread if m["id"] not in shown]
    old = [m for m in unread if m["id"] in shown]

    if event == "SessionStart" and unread:
        new, old = unread, []                      # свежей сессии нужно всё непрочитанное
    if new:
        lines.append(f"=== ПОЧТА окна {window}: новых писем {len(new)} (хук только показывает) ===")
        lines.append(data.get("prompt") or "")
        lines.append(NO_LOOP)
        for m in new[:MAX_LETTERS]:
            age = f" · лежит {m['age_h']} ч" if m.get("age_h") else ""
            stale = " · ПРОСРОЧЕНО, скажи оператору" if m.get("stale") else ""
            lines.append(f"[id {m['id']}] от {m['from']} · {m.get('topic') or 'без темы'}{age}{stale}")
            lines.append((m.get("body") or "")[:MAX_BODY])
            lines.append("-" * 60)
        if len(new) > MAX_LETTERS:
            lines.append(f"... и ещё {len(new) - MAX_LETTERS}: devmsg.sh inbox {window}")
        shown.update(m["id"] for m in new)
        state["last_remind"] = now
    elif old and _due(state, "last_remind", now):
        lines.append(f"почта окна {window}: показано, но не подтверждено: "
                     + ", ".join(m["id"] for m in old))
        state["last_remind"] = now

    stuck = [s for s in data.get("board") or [] if s.get("stale") and s.get("agent") != window]
    if stuck and (new or _due(state, "last_board", now)):
        state["last_board"] = now
        for s in stuck:
            lines.append(f"! окно «{s['agent']}» не забирает почту: {s.get('unread')} непрочитанных, "
                         f"самое старое {s.get('oldest_age_h')} ч — скажи оператору")

    if event == "SessionStart":
        lines.append(f"почта окна {window}: хук проверяет ящик на старте и не чаще раза в "
                     f"{FETCH_EVERY_S // 60} мин на ввод; при простое сессии крон нужно поставить "
                     f"заново (CronCreate живёт только в сессии). {NO_LOOP}")
    state["shown"] = sorted(i for i in shown if i in {m["id"] for m in unread})
    return lines


def main() -> int:
    window = sys.argv[1] if len(sys.argv) > 1 else "backtests"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        event = json.loads(sys.stdin.read() or "{}").get("hook_event_name", "UserPromptSubmit")
    except ValueError:
        event = "UserPromptSubmit"
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{window}.json")
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}

    now = time.time()
    data = err = None
    if event == "SessionStart" or now - state.get("last_fetch", 0) >= FETCH_EVERY_S:
        try:
            data = fetch(window)
            state["last_fetch"] = now
        except Exception as exc:                  # noqa: BLE001 — хук не имеет права падать
            err = f"{type(exc).__name__}: {str(exc)[:120]}"
    lines = render(data, state, event, now, window, err)
    if lines:
        print("\n".join(lines))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                             # noqa: BLE001 — сломанный хук не должен ломать ввод
        sys.exit(0)
