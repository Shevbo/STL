#!/usr/bin/env python3
"""Почта окон STL через inbox-пул Lineman. По спеке lineman-curator (13.08.2026).

Серверной части нет и не нужно: Lineman уже держит `~/.federation-inbox/<agent>/
inbox.jsonl` и два endpoint'а. Здесь только тонкий клиент.

    SEND  POST /api/agent/{to}/message?from={me}   body {"message": "..."}
    RECV  GET  /api/agent/{me}/inbox?since=<id>&limit=100

Курсор `last_id` держит клиент, пусто — спим и повторяем. Никакого крона и
никаких сервисов: это требование спеки, а не вкус.

    python3 scripts/fedmail.py send stl-backtests '{"topic":"ping"}' --me stl-real-trade
    python3 scripts/fedmail.py poll --me stl-real-trade --since 0
    python3 scripts/fedmail.py loop --me stl-real-trade --topics trade.fill,build

Идентификаторы окон названы по их ролям в проекте (спека это оставляет на
усмотрение: «На твоё усмотрение, например…»), иначе в переписке пришлось бы
держать в голове перевод w1/w2/w3/w4 в реальные зоны.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("LINEMAN_URL", "http://10.66.0.1:9090").rstrip("/")
MAX_BODY = 65536                      # лимит спеки; больше — файл плюс ссылка
IDLE_SLEEP_S = 3                      # спека: пусто -> sleep 2-5 c -> повтор

WINDOWS = ("stl-real-trade", "stl-backtests", "stl-ui-ux", "stl-dev-spare")
# Прокси в этой сети только мешает: адрес внутренний, WireGuard.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def send(to: str, message: str, me: str, timeout: int = 20) -> dict:
    """Положить письмо в ящик окна `to`. message — текст или JSON-строка."""
    body = json.dumps({"message": message}, ensure_ascii=False).encode()
    if len(body) > MAX_BODY:
        raise ValueError(f"тело {len(body)} байт > {MAX_BODY}: сохрани в файл и пришли ссылку")
    req = urllib.request.Request(
        f"{BASE}/api/agent/{to}/message?from={me}", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(_OPENER.open(req, timeout=timeout).read())


def poll(me: str, since: int = 0, limit: int = 100, timeout: int = 20) -> list[dict]:
    """Письма с id > since. FIFO по id, at-least-once — дедуп по id у клиента."""
    req = urllib.request.Request(
        f"{BASE}/api/agent/{me}/inbox?since={int(since)}&limit={int(limit)}")
    data = json.loads(_OPENER.open(req, timeout=timeout).read())
    return data.get("messages") or []


def topic_of(m: dict) -> str:
    """Топик лежит В ТЕЛЕ письма (спека: фильтр на клиенте, не на сервере)."""
    try:
        return (json.loads(m.get("message") or "{}") or {}).get("topic") or ""
    except (TypeError, ValueError):
        return ""


async def run_loop(me: str, on_message, since: int = 0,
                   topics: set[str] | None = None) -> None:
    """Опрос ящика. Курсор двигаем ТОЛЬКО вперёд и только по обработанным id:
    иначе при ошибке обработчика письмо потеряется молча."""
    seen: set[int] = set()
    while True:
        try:
            msgs = await asyncio.to_thread(poll, me, since)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[fedmail] опрос не удался: {e}", file=sys.stderr, flush=True)
            await asyncio.sleep(IDLE_SLEEP_S)
            continue
        if not msgs:
            await asyncio.sleep(IDLE_SLEEP_S)
            continue
        for m in msgs:
            mid = int(m.get("id") or 0)
            if mid in seen:
                continue               # at-least-once: повтор гасим здесь
            seen.add(mid)
            if topics and topic_of(m) not in topics:
                since = max(since, mid)
                continue
            try:
                res = on_message(m)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:      # noqa: BLE001 — цикл не падает от письма
                print(f"[fedmail] письмо {mid} не обработано: {e}",
                      file=sys.stderr, flush=True)
            since = max(since, mid)


def _print(m: dict) -> None:
    print(json.dumps(m, ensure_ascii=False), flush=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="почта окон STL через Lineman")
    ap.add_argument("cmd", choices=("send", "poll", "loop", "windows"))
    ap.add_argument("to", nargs="?")
    ap.add_argument("message", nargs="?")
    ap.add_argument("--me", default=os.environ.get("STL_FED_ID", ""))
    ap.add_argument("--since", type=int, default=0)
    ap.add_argument("--topics", default="")
    a = ap.parse_args(argv[1:])

    if a.cmd == "windows":
        print("\n".join(WINDOWS))
        return 0
    if not a.me:
        print("не задано своё окно: --me или STL_FED_ID", file=sys.stderr)
        return 2
    if a.cmd == "send":
        if not a.to or a.message is None:
            print("send <кому> <текст|json>", file=sys.stderr)
            return 2
        print(json.dumps(send(a.to, a.message, a.me), ensure_ascii=False))
        return 0
    if a.cmd == "poll":
        for m in poll(a.me, a.since):
            _print(m)
        return 0
    topics = {t for t in a.topics.split(",") if t} or None
    asyncio.run(run_loop(a.me, _print, a.since, topics))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
