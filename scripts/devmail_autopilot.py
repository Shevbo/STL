#!/usr/bin/env python3
"""Автопилот почты: письмо ЗАСТРЯЛО — поднимаем под него НОВУЮ сессию.

ЗАЧЕМ. Всё, что было построено до этого, доставляет письмо в РАБОТАЮЩУЮ сессию.
Но сессия Claude выполняет что-либо только когда её пользователь печатает: если
за окном никого нет, письмо лежит, сколько ни улучшай доставку. Проверено
перекличкой 13.08.2026 — три окна, ноль ответов за 3.5 минуты, при исправном
транспорте. Разбудить чужую сессию нельзя.

Отсюда единственный работающий приём (его же посоветовал klod-access): НЕ будить
старую сессию, а ПОРОЖДАТЬ новую под задачу. `claude -p` в клоне репозитория,
с личностью того окна, чьё письмо застряло.

КОГДА СРАБАТЫВАЕТ. Только по ПРОСРОЧЕННОМУ ящику (порог службы, 4 часа). Это
важно: просрочка means окно доказуемо не работает, поэтому гонки с живой сессией
не будет. Пока окно живо и читает почту само — автопилот молчит.

ЧТО ДЕЛАЕТ ПОРОЖДЁННАЯ СЕССИЯ. Ей передаётся её зона и правило: работу в СВОЕЙ
зоне сделать, в чужой — передать письмом владельцу, затем ответить отправителю
и подтвердить письмо. То есть ровно то, что сделало бы само окно.

ГРАНИЦЫ, НАМЕРЕННО УЗКИЕ:
  • один прогон за раз (файл-замок): две сессии в одном дереве подрались бы;
  • одно окно за прогон — самое старое застрявшее;
  • таймаут, после него сессия убивается;
  • реальную торговлю не трогаем: арминг, публикация релизов и рестарты
    shectory-trader остаются человеку, это записано в самом задании сессии.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("STL_REPO", Path.home() / "stl"))
LOCK = Path(os.environ.get("STL_AUTOPILOT_LOCK", Path.home() / ".stl-autopilot.lock"))
LOG = Path(os.environ.get("STL_AUTOPILOT_LOG", Path.home() / ".stl-autopilot.log"))
TIMEOUT_S = int(os.environ.get("STL_AUTOPILOT_TIMEOUT", "1800"))
LOCK_STALE_S = TIMEOUT_S + 300
SSH_HOST = os.environ.get("STL_DEVMAIL_SSH", "hoster")
DEVMSG = "bash ~/apps/shectory-trader/scripts/devmsg.sh"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def remote(cmd: str, timeout: int = 60) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                        SSH_HOST, cmd],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"rc={r.returncode} {(r.stderr or '')[:200]}")
    return r.stdout


MIN_AGE_H = float(os.environ.get("STL_AUTOPILOT_MIN_AGE_H", "4"))
# Кого автопилот НЕ обслуживает: оператор — человек, подхват сам себя не чинит.
SKIP = ("operator", "stl-dev-spare")


def stuck_window() -> tuple[str, dict] | None:
    """Самое старое застрявшее окно и его ящик. None — всё в порядке.

    Порог берём из окружения, а не из флага stale сервера: сервер судит по
    своим 4 часам, а автопилоту иногда нужно быстрее (и на проверке — сразу).
    """
    board = json.loads(remote(f"{DEVMSG} board-json"))
    stale = [b for b in board.get("board", [])
             if b.get("unread") and b["agent"] not in SKIP
             and (b.get("oldest_age_h") or 0) >= MIN_AGE_H]
    if not stale:
        return None
    worst = max(stale, key=lambda b: b.get("oldest_age_h") or 0)
    inbox = json.loads(remote(f"{DEVMSG} inbox-json {worst['agent']}"))
    return worst["agent"], inbox


def build_task(window: str, inbox: dict) -> str:
    """Задание для порождённой сессии. Оно ЗАМЕНЯЕТ собой контекст окна, поэтому
    несёт и личность, и правила, и сами письма — сессия свежая, она не помнит
    ничего."""
    zone = inbox.get("zone") or "?"
    letters = []
    for m in (inbox.get("messages") or [])[:5]:
        letters.append(
            f"--- письмо id {m.get('id')} от {m.get('from')} "
            f"(лежит {m.get('age_h')} ч)\n"
            f"тема: {m.get('topic') or 'без темы'}\n{m.get('body') or ''}")
    return f"""Ты окно «{window}» проекта STL, поднятое АВТОМАТИЧЕСКИ, потому что твоя
почта лежит непрочитанной дольше порога, а живой сессии за этим окном сейчас нет.

Ты не «новый агент», ты ЭТО окно. Действуй строго в его функции и не шире.

Твоя зона: {zone}

ПЕРВЫМ ДЕЛОМ ПРОЧИТАЙ, иначе ты не то окно, за которое себя выдаёшь:
  CLAUDE.md                       операционный канон проекта, писан по живым
                                  инцидентам; раздел «Three parallel Claude
                                  windows» — про зоны и почему их не нарушают
  .claude/shared/memory/MEMORY.md указатель на память окна: чем закончились
                                  прошлые инциденты и какие правила оператор
                                  требовал соблюдать. Читай те файлы из него,
                                  что относятся к письмам ниже.

ЧТО СДЕЛАТЬ по каждому письму ниже:
  1. Если работа в ТВОЕЙ зоне — сделай её.
  2. Если в чужой — НЕ делай, передай письмом владельцу
     (ssh {SSH_HOST} "{DEVMSG} send <окно> 'тема' 'текст' {window}").
  3. Ответь отправителю письмом о том, что сделано.
  4. Подтверди письмо: ssh {SSH_HOST} "{DEVMSG} ack <id>".

ЧЕГО НЕ ДЕЛАТЬ НИКОГДА, это решения человека:
  • не переводить роботов на реальные деньги и не менять их параметры;
  • не публиковать релизы агента и не перезапускать shectory-trader;
  • не трогать белые списки и лимиты живой торговли.
Если письмо просит что-то из этого — ответь отправителю, что нужен оператор,
и подтверди письмо.

ПИСЬМА:

{chr(10).join(letters)}
"""


def acquire_lock() -> bool:
    """Один прогон за раз. Замок старше собственного таймаута считаем брошенным:
    иначе упавшая сессия остановила бы автопилот навсегда."""
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < LOCK_STALE_S:
            return False
        LOCK.write_text(str(os.getpid()))
        return True
    except OSError:
        return False


def main() -> int:
    if not acquire_lock():
        log("замок занят — прогон пропущен")
        return 0
    try:
        found = stuck_window()
        if not found:
            return 0
        window, inbox = found
        n = len(inbox.get("messages") or [])
        log(f"застряло окно «{window}», писем {n} — поднимаю сессию")
        # ИЗОЛЯЦИЯ WORKTREE — приём klod-foreman, и он тут обязателен: оператор
        # может работать в основном дереве с ноутбука, а две правки в одном
        # каталоге подерутся молча. Своё дерево на прогон, после — удаляем.
        wt = Path(f"/tmp/stl-autopilot-{window}-{int(time.time())}")
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach",
                        str(wt), "HEAD"], capture_output=True, text=True, timeout=120)
        if not wt.exists():
            log(f"не удалось создать worktree для «{window}»")
            return 1
        cmd = ["claude", "-p", build_task(window, inbox),
               "--output-format", "json",
               "--permission-mode", "acceptEdits",
               "--allowedTools", "Read Edit Write Bash Grep Glob",
               "--model", os.environ.get("STL_AUTOPILOT_MODEL", "sonnet")]
        # Доступ к модели — ТОЛЬКО через Lineman (правило федерации): голый CLI
        # на smain отвечает 403, ключей провайдера у нас нет и не должно быть.
        env = {**os.environ, "STL_WINDOW": window,
               "ANTHROPIC_BASE_URL": os.environ.get(
                   "STL_AUTOPILOT_BASE", "http://10.66.0.1:9090/proxy/anthropic"),
               "ANTHROPIC_AUTH_TOKEN": os.environ.get("STL_AUTOPILOT_TOKEN", "stl"),
               "ANTHROPIC_CUSTOM_HEADERS": "X-Agent-Name: stl"}
        try:
            r = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True,
                               timeout=TIMEOUT_S, encoding="utf-8",
                               errors="replace", env=env)
        except subprocess.TimeoutExpired:
            log(f"сессия «{window}» превысила {TIMEOUT_S} с — убита")
            return 1
        finally:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove",
                            "--force", str(wt)], capture_output=True, timeout=120)
        tail = (r.stdout or r.stderr or "")[-300:]
        log(f"сессия «{window}» завершена rc={r.returncode}: {tail}")
        return 0 if r.returncode == 0 else 1
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
