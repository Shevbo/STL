"""Одноразовый вход в Telegram для архива новостей. ЗАПУСКАЕТ ОПЕРАТОР, один раз.

Зачем отдельный шаг: первый вход в Telegram API требует код подтверждения,
который приходит в приложение Telegram оператора. Этот код видит только он,
поэтому окно разработки этот шаг сделать не может. После входа остаётся файл
сессии, и дальше архив новостей качается без кодов.

    # на хостере, один раз:
    source ~/tgvenv/bin/activate
    set -a; . ~/.shectory_trade.env; set +a
    python scripts/tg_login_v1.py

Переменные окружения (значения в ~/.shectory_trade.env, сюда не попадают):
    TG_API_ID, TG_API_HASH      — с my.telegram.org, раздел API development tools
Файл сессии: ~/tg_stl.session — это тоже секрет, права 600, в git не попадает.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("нет TG_API_ID / TG_API_HASH в окружении: set -a; . ~/.shectory_trade.env")
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        sys.exit("нет telethon: pip install telethon (в ~/tgvenv)")

    session = Path.home() / "tg_stl"
    with TelegramClient(str(session), int(api_id), api_hash) as client:
        me = client.get_me()
        print(f"вход выполнен: {me.first_name or ''} (id {me.id})")
    sess_file = session.with_suffix(".session")
    if sess_file.exists():
        os.chmod(sess_file, 0o600)
        print(f"сессия сохранена: {sess_file} (права 600). Больше кодов не понадобится.")


if __name__ == "__main__":
    main()
