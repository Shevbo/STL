"""Tests for the team-46 news ingestion (no network)."""
from trader.lab.ai46 import news as N

SAMPLE_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Feed</title>
  <item><title>Сбербанк отчитался о рекордной прибыли</title>
        <description>рост чистой прибыли</description>
        <pubDate>Mon, 02 Jan 2026 15:04:05 +0300</pubDate></item>
  <item><title>Мировые рынки выросли на фоне макроданных</title>
        <description>общий обзор</description></item>
</channel></rss>"""


class FakeResp:
    def __init__(self, content):
        self.status_code = 200
        self.content = content


class FakeHTTP:
    def __init__(self, content):
        self._c = content

    async def get(self, url, headers=None):
        return FakeResp(self._c)


class FakeLLM:
    """Minimal KlodClient stand-in for classify_news."""
    available = True

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def ask(self, prompt, model_hint="deepseek-fast", max_tokens=1000):
        self.calls += 1
        return self.reply


# ── detect_ticker ─────────────────────────────────────────────────────────────

def test_detect_ticker():
    assert N.detect_ticker("Сбербанк отчитался") == "SBER"
    assert N.detect_ticker("Газпром нарастил экспорт") == "GAZP"
    assert N.detect_ticker("GAZP futures rally") == "GAZP"
    assert N.detect_ticker("Мировые рынки выросли") == ""


# ── parse_rss ─────────────────────────────────────────────────────────────────

def test_parse_rss():
    items = N.parse_rss(SAMPLE_RSS, "src")
    assert len(items) == 2
    assert items[0].title.startswith("Сбербанк")
    assert items[0].summary == "рост чистой прибыли"
    assert items[0].published_at is not None
    assert items[1].published_at is None  # no pubDate


def test_parse_rss_bad_xml():
    assert N.parse_rss("<not xml", "src") == []


# ── poll dedupe + tag ─────────────────────────────────────────────────────────

async def test_poll_dedupes_and_tags():
    rc = N.RSSCollector(sources=["http://x"])
    http = FakeHTTP(SAMPLE_RSS)
    first = await rc.poll(http)
    assert len(first) == 2
    tickers = {i.ticker for i in first}
    assert "SBER" in tickers and "" in tickers   # Sber tagged, macro untagged
    second = await rc.poll(http)                  # all titles seen
    assert second == []


# ── classify_severity cost guard ──────────────────────────────────────────────

async def test_classify_severity_skips_untagged():
    llm = FakeLLM('{"severity":9,"category":"macro","direction":"bearish","confidence":0.8}')
    item = N.NewsItem(title="макро", summary="", source="s", ticker="")  # untagged
    out = await N.classify_severity(llm, item)
    assert out.severity == 0 and llm.calls == 0   # cost guard: no LLM spend


async def test_classify_severity_tagged():
    llm = FakeLLM('{"severity":7,"category":"earnings","direction":"bullish","confidence":0.7}')
    item = N.NewsItem(title="Сбер прибыль", summary="", source="s", ticker="SBER")
    out = await N.classify_severity(llm, item)
    assert out.severity == 7 and llm.calls == 1
