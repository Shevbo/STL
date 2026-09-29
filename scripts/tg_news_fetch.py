"""Архив новостных каналов Telegram для сопоставления с импульсами цены.

ЗАЧЕМ. Гипотеза оператора 29.09.2026: если новостной фон тих, а на рынке
активное движение, доминирует алготрейдер или манипулятор, и искать
закономерности надо в таких импульсах. Для этого нужна не быстрая лента, а
АРХИВ с секундными метками: сопоставляются ПРОШЛЫЕ импульсы. Telegram API
единственный бесплатный источник, у которого есть и то, и другое; веб-превью
t.me/s непригодно (метка только ЧЧ:ММ, без даты и секунд — проверено 29.09).

Вход в Telegram делает оператор один раз (scripts/tg_login_v1.py), дальше этот
скрипт качает историю без кодов. Инкрементально: помнит последний id по каналу
и докачивает только новое, поэтому его можно вызывать хоть каждый час.

    source ~/tgvenv/bin/activate
    set -a; . ~/.shectory_trade.env; set +a
    python scripts/tg_news_fetch.py                       # каналы по умолчанию
    python scripts/tg_news_fetch.py --channels markettwits,interfaxonline

Хранилище: ~/news-archive/telegram-<канал>.jsonl, одна строка = одно сообщение:
    {"id": 386869, "ts": "2026-09-29T15:15:03+00:00", "ts_unix": ..., "text": "..."}
Время — UTC до секунды (МСК = UTC+3, без перехода).

ОГОВОРКА, которую нельзя терять при чтении: тишина в этих каналах не равна
отсутствию информации на рынке (иностранные провода, слухи, блочные сделки).
Метка «без новостей» — прокси, а не факт.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_CHANNELS = ("markettwits", "interfaxonline", "tass_agency", "centralbank_russia")
STORE = Path(os.environ.get("STL_NEWS_ARCHIVE", str(Path.home() / "news-archive")))


def last_id(path: Path) -> int:
    """Последний сохранённый id канала: докачиваем только новее его."""
    if not path.exists():
        return 0
    best = 0
    with path.open(encoding="utf-8") as f:
        for ln in f:
            try:
                best = max(best, int(json.loads(ln)["id"]))
            except Exception:  # noqa: BLE001 — битая строка не повод терять архив
                continue
    return best


def fetch(client, channel: str, since_id: int, limit: int | None) -> int:
    """Качает сообщения новее since_id, старые-первыми в файл. Возвращает число."""
    path = STORE / f"telegram-{channel}.jsonl"
    rows = []
    # iter_messages идёт от новых к старым; min_id останавливает на уже известном.
    for m in client.iter_messages(channel, min_id=since_id, limit=limit):
        if not m.date:
            continue
        rows.append({"id": m.id, "ts": m.date.isoformat(),
                     "ts_unix": int(m.date.timestamp()),
                     "text": (m.message or "").strip()})
    rows.sort(key=lambda r: r["id"])
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default=",".join(DEFAULT_CHANNELS))
    ap.add_argument("--limit", type=int, default=None,
                    help="потолок сообщений за вызов на канал (по умолчанию вся история)")
    a = ap.parse_args()

    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("нет TG_API_ID / TG_API_HASH в окружении")
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        sys.exit("нет telethon: pip install telethon (в ~/tgvenv)")
    session = Path.home() / "tg_stl"
    if not session.with_suffix(".session").exists():
        sys.exit("нет файла сессии ~/tg_stl.session: сначала оператор запускает "
                 "scripts/tg_login_v1.py")

    STORE.mkdir(parents=True, exist_ok=True)
    with TelegramClient(str(session), int(api_id), api_hash) as client:
        for ch in [c.strip() for c in a.channels.split(",") if c.strip()]:
            path = STORE / f"telegram-{ch}.jsonl"
            since = last_id(path)
            n = fetch(client, ch, since, a.limit)
            total = last_id(path)
            print(f"{ch:20} +{n:>6} сообщений (последний id {total})")


if __name__ == "__main__":
    main()
