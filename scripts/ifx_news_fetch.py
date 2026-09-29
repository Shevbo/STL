"""Архив ленты новостей Интерфакса по дням (минутная точность меток времени).

Зачем: сопоставить сильные ценовые импульсы на RI/BR/GD/Si с новостной лентой.
Если лента тихая, а рынок двигается, - подозрение на алготрейдера или манипулятора.
Это гипотеза, а не готовый вывод: тишина в ленте Интерфакса НЕ означает отсутствие
информации на рынке - источник один из многих, а не полный срез новостного фона.

Источник: https://www.interfax.ru/news/YYYY/MM/DD/page_N - HTML-страницы ленты дня,
постранично (первая страница тоже открывается через page_1, без редиректа).
RSS (interfax.ru/rss.asp) даёт только последние 25 записей и для истории не годится,
но подтверждает точность источника: pubDate в RSS - с секундами :00, то есть сама
лента размечена по минутам, секунды не публикуются никогда.

Время на странице - московское, часовой пояс без перехода на летнее/зимнее (UTC+3
круглый год, так и хранится у Интерфакса). precision в каждой записи всегда "minute".

robots.txt сайта запрещает только /errors, /search, /Infinite, /aas - архив по датам
не запрещён. Ранее была подозрение на защиту от ботов (пустой ответ на первой
странице без page_N): это оказался обычный HTTP 301 на URL с завершающим слэшем,
не бот-блок; хватает браузерного User-Agent и постраничных URL с page_N (включая
page_1), редирект не нужен.

Запуск:
    python scripts/ifx_news_fetch.py --from 2026-09-01 --to 2026-09-29

Формат вывода - ~/news-archive/interfax-YYYY-MM-DD.jsonl, одна строка на новость:
    {"ts": "2026-09-29T15:40:00+03:00", "ts_unix": 1758981600,
     "title": "...", "url": "https://www.interfax.ru/business/1119214",
     "section": "business", "precision": "minute"}
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

MSK = timezone(timedelta(hours=3))
BASE = "https://www.interfax.ru/news"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}
MAX_PAGES = 60  # защита от бесконечного цикла при неожиданном ответе сервера

# Новость: <div data-id="123"><span>18:40</span><a href="/business/123"><h3>...</h3>
# href бывает и абсолютным - у разделов на отдельных поддоменах (sport-interfax.ru и т.п.)
ITEM_RE = re.compile(
    r'<div data-id="\d+">\s*'
    r"<span>(\d{2}:\d{2})</span>\s*"
    r'<a href="(https?://[^"]+|/[a-zA-Z0-9_-]+/\d+)">\s*'
    r"<h3>(.*?)</h3>",
    re.DOTALL,
)


def derive_section(href: str) -> tuple[str, str]:
    """Секция и абсолютный URL из ссылки новости (относительной или с поддомена)."""
    if href.startswith("/"):
        section = href.strip("/").split("/")[0]
        return section, "https://www.interfax.ru" + href
    netloc = urlparse(href).netloc  # напр. www.sport-interfax.ru
    host = netloc.split(".")[-2] if netloc.count(".") >= 1 else netloc
    section = host[: -len("-interfax")] if host.endswith("-interfax") else host
    if host == "interfax":
        section = "news"
    return section, href


def msk_to_unix(d: date, hh_mm: str) -> tuple[str, int]:
    """Московское время дня -> (ISO с оффсетом +03:00, unix-время в UTC)."""
    hour, minute = (int(x) for x in hh_mm.split(":"))
    dt_msk = datetime(d.year, d.month, d.day, hour, minute, tzinfo=MSK)
    return dt_msk.isoformat(), int(dt_msk.astimezone(timezone.utc).timestamp())


def parse_page(page_html: str, d: date) -> list[dict]:
    """Разобрать одну HTML-страницу ленты в список записей новостей."""
    items = []
    for hh_mm, href, title_raw in ITEM_RE.findall(page_html):
        section, url = derive_section(href)
        ts, ts_unix = msk_to_unix(d, hh_mm)
        items.append(
            {
                "ts": ts,
                "ts_unix": ts_unix,
                "title": html.unescape(title_raw).strip(),
                "url": url,
                "section": section,
                "precision": "minute",
            }
        )
    return items


def fetch_page(d: date, page: int, delay: float) -> str | None:
    """Скачать одну страницу ленты дня. None - страницы дальше нет (404)."""
    url = f"{BASE}/{d.year:04d}/{d.month:02d}/{d.day:02d}/page_{page}"
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            print(f"  {url}: HTTP {exc.code}", file=sys.stderr)
            return None
        except urllib.error.URLError as exc:
            if attempt == 0:
                time.sleep(delay)
                continue
            print(f"  {url}: сетевая ошибка {exc}", file=sys.stderr)
            return None
    return None


def fetch_day(d: date, delay: float) -> list[dict]:
    """Пройти все страницы ленты дня, вернуть новости в хронологическом порядке."""
    items: list[dict] = []
    pages_with_content = 0
    for page in range(1, MAX_PAGES + 1):
        page_html = fetch_page(d, page, delay)
        if not page_html:
            break
        page_items = parse_page(page_html, d)
        if not page_items:
            break
        pages_with_content = page
        items.extend(page_items)
        time.sleep(delay)
    items.sort(key=lambda x: x["ts_unix"])
    print(f"{d.isoformat()}: страниц={pages_with_content}, новостей={len(items)}")
    return items


def daterange(d_from: date, d_to: date):
    d = d_from
    while d <= d_to:
        yield d
        d += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default="~/news-archive", help="каталог для .jsonl")
    ap.add_argument("--delay", type=float, default=1.5, help="пауза между запросами, сек")
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    d_from = date.fromisoformat(args.date_from)
    d_to = date.fromisoformat(args.date_to)
    today_msk = datetime.now(MSK).date()

    for d in daterange(d_from, d_to):
        out_file = out_dir / f"interfax-{d.isoformat()}.jsonl"
        if out_file.exists() and d < today_msk:
            print(f"{d.isoformat()}: уже есть, пропуск")
            continue
        items = fetch_day(d, args.delay)
        with out_file.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
