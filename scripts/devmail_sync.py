#!/usr/bin/env python3
"""Фоновая доставка почты окна в локальный слепок. Только stdlib.

ЗАПУСКАТЬ НА МАШИНЕ ОКОН, а не на хостере. Это единственное место, которое
ходит в сеть; хук после этого читает готовый файл и не знает про ssh.

    python3 scripts/devmail_sync.py --window real-trade --once
    python3 scripts/devmail_sync.py --window real-trade --loop 45

ПОЧЕМУ PULL, А НЕ PUSH. Push доставлял бы в HTTP-сервер, а у машины окон его
нет и снаружи она недоступна. Pull раз в 45 с даёт задержку меньше минуты —
против семи и тридцати часов, которые письма лежали до этой службы.

ПОЧЕМУ 45 с. Оператор вводит команду раз в минуты, поэтому окно почти всегда
увидит письмо на первом же вводе после его прихода. Чаще ходить незачем: ssh
стоит ~1.8 с, и это время фону, а не оператору.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devmail_spool as spool

# Пустое значение = хостер и есть эта машина, идём локально без ssh.
SSH_HOST = os.environ.get("STL_DEVMAIL_SSH", "hoster")
REMOTE_CMD = os.environ.get(
    "STL_DEVMAIL_CMD", "bash ~/apps/shectory-trader/scripts/devmsg.sh inbox-json")


def fetch(window: str, timeout: int = 25) -> dict:
    """Забрать ящик окна. Ошибку не глотаем — молчаливый синк хуже мёртвого."""
    tail = f"{REMOTE_CMD} {window}"
    cmd = (["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SSH_HOST, tail]
           if SSH_HOST else ["bash", "-lc", tail])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"rc={r.returncode} {(r.stderr or '')[:200]}")
    return json.loads(r.stdout)


def sync_once(window: str) -> int:
    try:
        spool.save_state(window, fetch(window))
        return 0
    except Exception as e:
        print(f"[devmail-sync] {window}: {e}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", required=True)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, default=0, metavar="SEC")
    a = ap.parse_args()
    if a.once or not a.loop:
        return sync_once(a.window)
    while True:
        sync_once(a.window)
        time.sleep(max(15, a.loop))


if __name__ == "__main__":
    sys.exit(main())
