#!/usr/bin/env python3
"""Вызов локального API STL с сессионным токеном — для работы с хостера.

    python scripts/stl_call.py GET  /api/v1/quik/manual/pnl?period=day
    python scripts/stl_call.py POST /api/v1/quik/robots/<id>/deploy-agent body.json

Зачем: ручки STL закрыты сессионной авторизацией портала, и с консоли хостера до
них иначе не дотянуться — приходилось либо лезть в UI, либо оставлять ручку без
проверки на живом. Токен собирается из настроек приложения тем же кодом, что и
вход оператора, НИКУДА не печатается и нигде не сохраняется.

Только для localhost: адрес зашит, наружу этот скрипт не ходит.
"""
from __future__ import annotations

import json
import os
import sys

import httpx

from trader.auth.portal import make_session_token
from trader.config import Settings

BASE = "http://127.0.0.1:8000"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    method, path = sys.argv[1].upper(), sys.argv[2]
    body = None
    if len(sys.argv) > 3:
        with open(sys.argv[3], encoding="utf-8") as fh:
            body = json.load(fh)

    secret = Settings().shectory_auth_bridge_secret
    if not secret:
        print("нет SHECTORY_AUTH_BRIDGE_SECRET в окружении — запускать на хостере")
        return 2
    who = os.getenv("STL_API_USER", "bshevelev75@gmail.com")
    headers = {"Authorization": "Bearer " + make_session_token(who, secret)}

    r = httpx.request(method, BASE + path, headers=headers, json=body, timeout=30.0)
    print(r.status_code)
    try:
        print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:8000])
    except ValueError:
        print(r.text[:4000])
    return 0 if r.status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
