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
        print(f"[{mark}id {m['id']}] от {m['from']} · {m.get('topic') or 'без темы'}")
        print(m["body"])
        print("-" * 70)
    if not d["messages"]:
        print("(пусто)")
    return 0


def cmd_send(to: str, topic: str, body: str, sender: str) -> int:
    with _client() as c:
        r = c.post("/api/v1/dev/msg",
                   json={"to": to, "topic": topic, "body": body, "sender": sender})
    print(r.status_code, r.text[:300])
    return 0 if r.status_code == 200 else 1


def cmd_ack(msg_id: str) -> int:
    with _client() as c:
        r = c.post(f"/api/v1/dev/msg/{msg_id}/ack")
    print(r.status_code, r.text[:200])
    return 0 if r.status_code == 200 else 1


def cmd_board() -> int:
    """Вся доска разом: непрочитанное по каждому окну. Нужна будильнику —
    хук сессии не знает, КАКОЕ из трёх окон стартует, и печатает всё."""
    out = {}
    with _client() as c:
        for a in ("real-trade", "backtests", "ui-ux", "operator"):
            r = c.get(f"/api/v1/dev/inbox/{a}")
            if r.status_code == 200:
                out[a] = r.json()["messages"]
    total = sum(len(v) for v in out.values())
    if not total:
        return 0                     # тишина = ничего не печатаем, хук молчит
    print("=== ПОЧТА ОКОН STL: %d непрочитанных ===" % total)
    for a, msgs in out.items():
        for m in msgs:
            print(f"  [{a}] id {m['id']} от {m['from']}: {m.get('topic') or 'без темы'}")
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
