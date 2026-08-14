#!/usr/bin/env python3
"""Автоответчик окна: письмо получает ПРОЦЕСС, а не сессия.

ЗАЧЕМ. Сессия Claude выполняется только когда её оператор печатает; разбудить
её снаружи нечем. Замер 13.08: транспорт доставляет письмо в окно за 1.5-2 с, а
ответ не приходит часами, потому что ждёт человека. Значит отвечать должен
процесс рядом: `claude -p` в ПАПКЕ окна, откуда он сам подхватывает личность
(CLAUDE.local.md + CLAUDE.md) — переписывать личность в промпт не нужно.

ЧТО МОЖЕТ. Только чтение: Read/Grep/Glob. Автоответчик отвечает на вопросы и
берёт работу на заметку, но не правит код, не деплоит и не трогает торговлю —
живое окно для этого никто не отменял, а бесконтрольный агент в репозитории
живой торговли стоит дороже удобства.

ПОЧЕМУ НЕ ЗАЦИКЛИТСЯ. Свой ответ помечается "auto": true, и письма с этой
меткой не обслуживаются. Без метки два автоответчика играли бы в пинг-понг
вечно и сожгли бы лимит за ночь.

    python3 scripts/fedbot.py            личность из папки
    python3 scripts/fedbot.py stl-ui-ux  явно
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fedmail
from fedwindow import window_id

REPO = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("STL_FED_STATE", Path.home() / ".stl-fedmail"))
ANSWER_TIMEOUT_S = int(os.environ.get("STL_FEDBOT_TIMEOUT", "600"))
IDLE_S = 5
CLAUDE = os.environ.get("STL_CLAUDE_BIN", "claude")
READ_ONLY_TOOLS = "Read,Grep,Glob"


def _payload(m: dict) -> tuple[str, str, bool]:
    """(тема, текст, это_ответ_автомата). Тело письма по спеке — строка, внутри
    обычно JSON; кривой JSON не повод потерять письмо, отдаём как есть."""
    raw = str(m.get("message") or "")
    try:
        d = json.loads(raw)
    except ValueError:
        return "", raw, False
    if not isinstance(d, dict):
        return "", raw, False
    body = d.get("payload")
    if not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False)
    return str(d.get("topic") or ""), body, bool(d.get("auto"))


def should_answer(m: dict, win: str) -> bool:
    """Обслуживаем ли письмо. Вынесено отдельно, потому что здесь живёт защита
    от вечного пинг-понга двух автоответчиков — её нужно проверять тестом, а не
    надеяться на неё."""
    _, body, auto = _payload(m)
    return bool(body.strip()) and not auto and str(m.get("from") or "") != win


def answer(win: str, sender: str, topic: str, body: str) -> str:
    """Ответ пишет claude -p из папки окна. Личность берётся из папки."""
    prompt = (
        f"Тебе письмо от окна «{sender}»"
        + (f", тема «{topic}»" if topic else "")
        + ".\n\n" + body
        + "\n\n---\nТы автоответчик своего окна: сессия оператора сейчас закрыта. "
          "Ответь по существу и КОРОТКО, тем же языком. Инструменты у тебя только "
          "на чтение: код не правь, ничего не запускай и не деплой. Если письмо "
          "требует работы в твоей зоне — скажи это прямо и назови, что именно "
          "должно быть сделано, когда окно откроют. Ответ уйдёт отправителю целиком."
    )
    r = subprocess.run(
        [CLAUDE, "-p", prompt, "--allowedTools", READ_ONLY_TOOLS],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=ANSWER_TIMEOUT_S)
    out = (r.stdout or "").strip()
    if not out:
        out = f"[автоответчик {win}] claude вернул пусто, код {r.returncode}: " \
              + (r.stderr or "")[:300]
    return out


def serve_once(win: str) -> int:
    """Обслужить всё новое. Возвращает число обработанных писем."""
    STATE.mkdir(parents=True, exist_ok=True)
    cur = STATE / f"{win}.bot-cursor"
    since = int(cur.read_text().strip() or 0) if cur.exists() else 0
    try:
        fresh = fedmail.poll(win, since)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[fedbot] ящик недоступен: {e}", file=sys.stderr, flush=True)
        return 0
    done = 0
    for m in fresh:
        mid = int(m.get("id") or 0)
        sender = str(m.get("from") or "")
        topic, body, auto = _payload(m)
        # Курсор двигаем СРАЗУ и для пропущенных тоже: письмо, на котором мы
        # споткнулись, не должно обслуживаться в каждом круге заново.
        cur.write_text(str(mid))
        if not should_answer(m, win):
            continue
        print(f"[fedbot] {win} <- {sender} id {mid}: отвечаю", flush=True)
        t0 = time.time()
        try:
            text = answer(win, sender, topic, body)
        except subprocess.TimeoutExpired:
            text = f"[автоответчик {win}] не уложился в {ANSWER_TIMEOUT_S} с, письмо ждёт живого окна"
        except OSError as e:
            print(f"[fedbot] claude не запустился: {e}", file=sys.stderr, flush=True)
            continue
        try:
            fedmail.send(sender, json.dumps(
                {"topic": f"re.{topic}" if topic else "re", "auto": True,
                 "payload": text}, ensure_ascii=False), win)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[fedbot] ответ не ушёл: {e}", file=sys.stderr, flush=True)
            continue
        print(f"[fedbot] ответ отправлен за {time.time() - t0:.0f} с", flush=True)
        done += 1
    return done


LEGACY_EVERY = 12                     # циклов по IDLE_S между заходами в старый ящик


def serve_legacy(win: str) -> int:
    """Старый ящик на хостере (`scripts/devmsg.sh`) — он ЖИВ и им пользуются.

    14.08 два письма от stl-dev-spare пролежали 11.9 и 6.7 часа: они пришли в
    devmsg, а окно смотрело только в Lineman. Две почты без моста — это не две
    почты, а одна работающая и одна невидимая.

    Письмо ПЕРЕКЛАДЫВАЕТСЯ в ящик Lineman (дальше его увидят и хук, и
    автоответчик — путь один) и подтверждается отправителю. `ack` НЕ ставим
    намеренно: он означает «сделано», а сделать работу автоответчик не может.
    Пусть висит непрочитанным, пока за него не возьмётся живое окно."""
    short = win[4:] if win.startswith("stl-") else win
    seen_f = STATE / f"{win}.legacy-seen"
    seen = set(seen_f.read_text().split()) if seen_f.exists() else set()
    try:
        raw = subprocess.run(
            ["ssh", "hoster",
             f"bash ~/apps/shectory-trader/scripts/devmsg.sh inbox-json {short}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        box = json.loads(raw.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"[fedbot] старый ящик недоступен: {e}", file=sys.stderr, flush=True)
        return 0
    moved = 0
    for m in box.get("messages") or []:
        mid = str(m.get("id") or "")
        if not mid or mid in seen or m.get("read_ms"):
            continue
        seen.add(mid)
        text = (f"[из старого ящика devmsg, id {mid}, лежит {m.get('age_h')} ч]\n"
                f"тема: {m.get('topic') or 'без темы'}\n\n{m.get('body') or ''}")
        try:
            fedmail.send(win, json.dumps({"topic": "legacy.forward",
                                          "payload": text}, ensure_ascii=False),
                         str(m.get("from") or "legacy"))
            subprocess.run(
                ["ssh", "hoster",
                 "bash ~/apps/shectory-trader/scripts/devmsg.sh send "
                 f"{m.get('from')} 'получено автоответчиком' "
                 f"'Письмо {mid} принято автоответчиком окна {short}: доставлено в ящик "
                 f"окна, ack не ставлю — работу делает живая сессия. Пиши в Lineman "
                 f"({win}), там ответ приходит за секунды.' {short}"],
                capture_output=True, timeout=60)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            print(f"[fedbot] мост не сработал на {mid}: {e}", file=sys.stderr, flush=True)
            seen.discard(mid)
            continue
        moved += 1
    seen_f.write_text("\n".join(sorted(seen)))
    return moved


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "loop"]
    once = "--once" in args
    args = [a for a in args if a != "--once"]
    win = args[0] if args else window_id()
    if not win:
        print("окно не опознано: нужен STL_WINDOW или CLAUDE.local.md", file=sys.stderr)
        return 2
    win = win if win.startswith("stl-") else f"stl-{win}"
    if once:
        return 0 if serve_once(win) >= 0 else 1
    print(f"[fedbot] {win}: автоответчик запущен", flush=True)
    tick = 0
    while True:
        serve_once(win)
        if tick % LEGACY_EVERY == 0:      # старый ящик ходит по ssh, ему хватит минуты
            serve_legacy(win)
        tick += 1
        time.sleep(IDLE_S)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
