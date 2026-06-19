"""News ingestion for team-46 — port of go-bot/internal/data/rss.go + the
detector's news path (event/detector.go runNewsChecks).

RSS poll → dedupe by title → keyword ticker-tag → LLM severity classify
(cost-guard: skip untagged macro news) → detector emits when severity >= 6.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

RSS_SOURCES = [
    "https://www.interfax.ru/rss.asp",
    "https://1prime.ru/rss",
    "https://www.finam.ru/analysis/conews/rsspoint",
    "https://tass.ru/rss/v2.xml",
    "https://smart-lab.ru/rss/",
]
RSS_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) MOEX-bot RSS reader"

# detectTicker keyword -> ticker map (rss.go). Order matters (first hit wins);
# a candidate not in the map returns itself (matches the Go behaviour).
_CANDIDATES = [
    "СБЕР", "ГАЗПРОМ", "ЛУКОЙЛ", "РОСНЕФТЬ", "НОВАТЭК",
    "YANDEX", "ЯНДЕКС", "SBER", "GAZP", "LKOH", "ROSN", "NVTK",
    "GMKN", "НОРНИКЕЛЬ", "VTBR", "ВТБ", "PLZL", "ПОЛЮС",
    "YDEX", "NLMK", "НЛМК", "CHMF", "СЕВЕРСТАЛЬ",
    "ALRS", "АЛРОСА", "AFLT", "АЭРОФЛОТ", "MGNT", "МАГНИТ",
    "MOEX", "МОСБИРЖА", "MTSS", "МТС", "PIKK", "ПИК",
]
_TICKER_MAP = {
    "СБЕР": "SBER", "ГАЗПРОМ": "GAZP", "ЛУКОЙЛ": "LKOH",
    "РОСНЕФТЬ": "ROSN", "НОВАТЭК": "NVTK", "ЯНДЕКС": "YDEX",
    "НОРНИКЕЛЬ": "GMKN", "ВТБ": "VTBR", "ПОЛЮС": "PLZL",
    "НЛМК": "NLMK", "СЕВЕРСТАЛЬ": "CHMF", "АЛРОСА": "ALRS",
    "АЭРОФЛОТ": "AFLT", "МАГНИТ": "MGNT", "МОСБИРЖА": "MOEX",
    "МТС": "MTSS", "ПИК": "PIKK",
}


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    published_at: float | None = None   # unix seconds
    ticker: str = ""
    severity: int = 0                    # 0 = not yet classified


def detect_ticker(text: str) -> str:
    """rss.go::detectTicker — fast keyword search → ticker (or '' if none)."""
    up = text.upper()
    for kw in _CANDIDATES:
        if kw in up:
            return _TICKER_MAP.get(kw, kw)
    return ""


def parse_rss(xml_bytes: bytes | str, source: str) -> list[NewsItem]:
    """Parse RSS 2.0 channel/item into NewsItems. Tolerant of parse errors."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        ts = None
        if pub:
            try:
                ts = parsedate_to_datetime(pub).timestamp()
            except (TypeError, ValueError):
                ts = None
        if title:
            out.append(NewsItem(title=title, summary=desc, source=source, published_at=ts))
    return out


class RSSCollector:
    """Polls the RSS sources and yields NEW (unseen) ticker-tagged items."""

    def __init__(self, sources: list[str] | None = None) -> None:
        self._sources = sources or RSS_SOURCES
        self._seen: set[str] = set()

    async def _fetch(self, client, url: str) -> list[NewsItem]:
        try:
            r = await client.get(url, headers={
                "User-Agent": RSS_USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            })
            if r.status_code >= 400:
                return []
            return parse_rss(r.content, url)
        except Exception:
            return []

    async def poll(self, client) -> list[NewsItem]:
        """Fetch all sources concurrently; return new items (deduped by title,
        ticker tagged). `client` is an httpx.AsyncClient."""
        batches = await asyncio.gather(*[self._fetch(client, s) for s in self._sources])
        new: list[NewsItem] = []
        for items in batches:
            for it in items:
                if it.title in self._seen:
                    continue
                self._seen.add(it.title)
                it.ticker = detect_ticker(it.title + " " + it.summary)
                new.append(it)
        return new


async def classify_severity(llm_client, item: NewsItem) -> NewsItem:
    """Set item.severity via the LLM classifier. Cost guard: untagged (macro)
    news is skipped — the downstream gate only enters on tagged tickers anyway
    (detector.go runNewsChecks cost guard 22-05)."""
    if not item.ticker:
        return item            # cost guard: leave severity 0, no LLM spend
    from trader.lab.ai46.llm import classify_news
    res = await classify_news(llm_client, f"{item.title} {item.summary}", item.source, item.ticker)
    item.severity = int(res.severity)
    return item
