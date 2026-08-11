#!/usr/bin/env python3
"""Почта между окнами разработки STL — из командной строки.

ЗАПУСКАТЬ НА ХОСТЕРЕ: токен минтуется тем же подписчиком, что у портала, и
секрет не покидает сервер (см. CLAUDE.md, «Portal-authed API from a shell»).

    ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh inbox real-trade'
    ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh send ui-ux "тема" "текст" real-trade'
    ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh ack 1786... :abc123'

Окна: real-trade, backtests, ui-ux, operator (+ all — всем сразу).
"""
from __future__ import annotations

import os
import sys

import httpx

API = os.environ.get("STL_API", "http://127.0.0.1:8000")


def _token() -> str:
    tok = os.environ.get("DEVMSG_TOKEN")
    if tok:
        return tok
    from trader.auth.portal import make_session_token
    secret = os.environ["SHECTORY_AUTH_BRIDGE_SECRET"]
    return make_session_token(os.environ.get("DEVMSG_USER", "ops@stl"), secret)


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30,
                        headers={"Authorization": "Bearer " + _token()})


def cmd_inbox(agent: str, show_all: bool = False) -> int:
    with _client() as c:
        r = c.get(f"/api/v1/dev/inbox/{agent}", params={"all": 1 if show_all else 0})
    if r.status_code != 200:
        print(r.status_code, r.text[:300])
        return 1
    d = r.json()
    print(f"=== ящик «{d['agent']}» — {d['count']} писем ===")
    print(d["prompt"], "\n")
    for m in d["messages"]:
        mark = "" if m.get("read_ms") else "НЕПРОЧИТАНО "
        age = f" · лежит {m['age_h']} ч" if m.get("age_h") else ""
        stale = " · ПРОСРОЧЕНО" if m.get("stale") else ""
        print(f"[{mark}id {m['id']}] от {m['from']} · {m.get('topic') or 'без темы'}{age}{stale}")
        print(m["body"])
        print("-" * 70)
    if not d["messages"]:
        print("(пусто)")
    # Чужие залипшие ящики печатаем ЗДЕСЬ же: окно, которое читает свою почту,
    # обязано увидеть, что сосед свою не забирает, и сказать оператору.
    for s in d.get("board") or []:
        if s.get("stale") and s["agent"] != d["agent"]:
            print(f"! окно «{s['agent']}» не забирает почту: {s['unread']} непрочитанных, "
                  f"самое старое {s['oldest_age_h']} ч — СКАЖИ ОПЕРАТОРУ")
    return 0


def cmd_send(to: str, topic: str, body: str, sender: str) -> int:
    with _client() as c:
        r = c.post("/api/v1/dev/msg",
                   json={"to": to, "topic": topic, "body": body, "sender": sender})
    if r.status_code != 200:
        print(r.status_code, r.text[:300])
        return 1
    d = r.json()
    rec = d.get("recipient") or {}
    print(f"отправлено · id {d['id']} · {d['from']} -> {d['to']}")
    if rec:
        print(f"в ящике «{rec['agent']}»: непрочитанных {rec['unread']}, "
              f"самое старое {rec['oldest_age_h']} ч")
    # Предупреждение печатаем ПОСЛЕДНИМ и целиком: отправитель должен узнать
    # о молчащем адресате в момент отправки, а не через полдня от человека.
    if d.get("warning"):
        print("\n" + d["warning"])
    return 0


def cmd_ack(msg_id: str) -> int:
    with _client() as c:
        r = c.post(f"/api/v1/dev/msg/{msg_id}/ack")
    print(r.status_code, r.text[:200])
    return 0 if r.status_code == 200 else 1


def cmd_board() -> int:
    """Вся доска разом: непрочитанное по каждому окну И его возраст. Нужна
    будильнику — хук сессии не знает, КАКОЕ из трёх окон стартует, и печатает
    всё. Один запрос к серверу вместо четырёх."""
    with _client() as c:
        r = c.get("/api/v1/dev/board")
    if r.status_code != 200:
        print(r.status_code, r.text[:200])
        return 1
    d = r.json()
    busy = [s for s in d["board"] if s["unread"]]
    if not busy:
        return 0                     # тишина = ничего не печатаем, хук молчит
    total = sum(s["unread"] for s in busy)
    print("=== ПОЧТА ОКОН STL: %d непрочитанных ===" % total)
    for s in busy:
        flag = "  <-- ПРОСРОЧКА" if s["stale"] else ""
        print(f"  {s['agent']}: {s['unread']} шт, самое старое {s['oldest_age_h']} ч{flag}")
    for m in d.get("stale") or []:
        print(f"  ! [{m['to']}] id {m['id']} от {m['from']}: "
              f"{m['topic'] or 'без темы'} — лежит {m['age_h']} ч")
    if d.get("note"):
        print("\n" + d["note"])
    print("Своё окно читает: scripts/devmsg.sh inbox <окно>; подтверждает: ... ack <id>")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "inbox":
        return cmd_inbox(argv[2], show_all="--all" in argv)
    if cmd == "send":
        if len(argv) < 5:
            print("send <кому> <тема> <текст> [от кого]")
            return 2
        return cmd_send(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else "operator")
    if cmd == "ack":
        return cmd_ack(argv[2])
    if cmd == "board":
        return cmd_board()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
