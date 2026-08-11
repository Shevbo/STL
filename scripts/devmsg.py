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

import json
import os
import sys
import time
import urllib.request

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


LINEMAN = os.environ.get("LINEMAN_URL", "http://10.66.0.1:9090")
BORIS_TG = "36910539"
_ALERT_MARK = os.path.expanduser("~/.stl-devmsg-alert")
_RENOTIFY_S = 6 * 3600           # не долбить чаще раза в 6 часов, как infra-guard


def cmd_alert() -> int:
    """Эскалация ПРОСРОЧКИ НАРУЖУ контура, кроном на хостере.

    Почему не в окна. 11.08.2026 письма лежали 7 и 30 часов, и печатать тревогу
    внутрь окон было бы бесполезно: не читали ВСЕ ТРИ сразу. Эскалация обязана
    уходить из застрявшего контура, а не в него — иначе она застревает вместе с
    ним. Единственный, кто может расшевелить окна, снаружи: оператор.

    Почему не хук на каждый ввод (обсуждалось и отвергнуто): цена там не в
    секундах, а в ТОКЕНАХ на каждый выстрел — ящик, печатаемый в контекст при
    каждом сообщении, за длинную сессию вытесняет ровно то правило, ради
    которого его вешали. Живая сессия получает доску один раз на SessionStart,
    а сторожем работает этот крон.
    """
    with _client() as c:
        r = c.get("/api/v1/dev/board")
    if r.status_code != 200:
        print("board", r.status_code, r.text[:200])
        return 1
    stuck = (r.json() or {}).get("stale") or []
    if not stuck:
        if os.path.exists(_ALERT_MARK):
            os.remove(_ALERT_MARK)   # разгребли — забываем, следующий раз алертим сразу
        return 0
    try:
        last = float(open(_ALERT_MARK).read().strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    if time.time() - last < _RENOTIFY_S:
        return 0
    lines = [f"[{m['to']}] от {m['from']}: {m['topic'] or 'без темы'} — {m['age_h']} ч"
             for m in stuck[:8]]
    text = ("STL: почта между окнами стоит\n"
            + "\n".join(lines)
            + f"\n\nВсего просроченных: {len(stuck)}. Окно не забирает письма — "
              "открой его и скажи прочитать ящик:\n"
              "ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh inbox <окно>'")
    body = json.dumps({"account": "default", "chat_id": BORIS_TG,
                       "text": text[:3900]}).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(f"{LINEMAN}/api/tg/send", data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        ok = json.loads(opener.open(req, timeout=15).read()).get("ok")
    except Exception as e:                       # noqa: BLE001 — сторож не падает
        print("tg fail:", e)
        return 1
    if ok:
        open(_ALERT_MARK, "w").write(str(time.time()))
        print(f"алерт отправлен, просроченных {len(stuck)}")
    return 0 if ok else 1


def cmd_board_brief() -> int:
    """Одна строка для будильника на КАЖДОЕ сообщение оператора.

    Длинную доску туда слать нельзя: она поедет в контекст при каждом вводе.
    Здесь только «кому сколько и насколько старое» — увидевшее окно само
    поймёт, его это ящик или соседа."""
    with _client() as c:
        r = c.get("/api/v1/dev/board")
    if r.status_code != 200:
        return 1
    d = r.json()
    busy = [s for s in d["board"] if s["unread"]]
    if not busy:
        return 0                     # тишина: пустая почта не занимает контекст
    parts = [f"{s['agent']} {s['unread']}" + (f" ({s['oldest_age_h']}ч!)" if s["stale"]
             else f" ({s['oldest_age_h']}ч)") for s in busy]
    print("ПОЧТА ОКОН: " + " · ".join(parts)
          + " — свой ящик: devmsg.sh inbox <окно>; «!» = просрочка, скажи оператору")
    return 0


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
        return cmd_board_brief() if "--brief" in argv else cmd_board()
    if cmd == "alert":
        return cmd_alert()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
