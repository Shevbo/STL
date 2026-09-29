"""Парсер архива новостей Интерфакса - на сохранённом фрагменте, без сети."""
from datetime import date

from scripts.ifx_news_fetch import msk_to_unix, parse_page

# Фрагмент реальной страницы https://www.interfax.ru/news/2026/09/25/page_1
# (сохранён 29.09.2026), с одной обычной новостью и одной с поддомена sport-interfax.ru
PAGE_FRAGMENT = """
<div class="an">
    <div data-id="1118589">
        <span>23:58</span>
        <a href="/world/1118589">
            <h3>Пример новости с &quot;кавычками&quot; и амперсандом A&amp;B</h3>
        </a>
    </div>
    <div data-id="1118557">
        <span>19:37</span>
        <a href="https://www.sport-interfax.ru/1118557">
            <h3>Азалия Аминева выиграла золото ЧЕ по боксу в Софии</h3>
        </a>
    </div>
</div>
"""


def test_parse_page_extracts_all_items():
    items = parse_page(PAGE_FRAGMENT, date(2026, 9, 25))
    assert len(items) == 2


def test_parse_page_relative_url():
    items = parse_page(PAGE_FRAGMENT, date(2026, 9, 25))
    item = next(i for i in items if i["url"].endswith("/world/1118589"))
    assert item["title"] == 'Пример новости с "кавычками" и амперсандом A&B'
    assert item["url"] == "https://www.interfax.ru/world/1118589"
    assert item["section"] == "world"
    assert item["precision"] == "minute"
    assert item["ts"] == "2026-09-25T23:58:00+03:00"


def test_parse_page_absolute_subdomain_url():
    items = parse_page(PAGE_FRAGMENT, date(2026, 9, 25))
    item = next(i for i in items if "sport-interfax" in i["url"])
    assert item["title"] == "Азалия Аминева выиграла золото ЧЕ по боксу в Софии"
    assert item["url"] == "https://www.sport-interfax.ru/1118557"
    assert item["section"] == "sport"
    assert item["ts"] == "2026-09-25T19:37:00+03:00"


def test_msk_to_unix():
    # 2026-09-29 15:40 МСК (UTC+3) = 12:40 UTC
    ts, ts_unix = msk_to_unix(date(2026, 9, 29), "15:40")
    assert ts == "2026-09-29T15:40:00+03:00"
    import datetime as dt

    expected = dt.datetime(2026, 9, 29, 12, 40, tzinfo=dt.timezone.utc).timestamp()
    assert ts_unix == int(expected)
