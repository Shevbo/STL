import httpx
import pytest

from trader.latency import LatencyMonitor, _parse_server_ts, summarize


def test_parse_server_ts_rfc3339_and_proto():
    # RFC3339 string (grpc-gateway default) -> 2026-07-03T10:00:00Z
    t = _parse_server_ts({"timestamp": "2026-07-03T10:00:00Z"})
    assert t == pytest.approx(1783072800.0, abs=1)
    # proto-JSON {seconds, nanos}
    t2 = _parse_server_ts({"timestamp": {"seconds": 1783072800, "nanos": 500_000_000}})
    assert t2 == pytest.approx(1783072800.5, abs=0.001)
    # missing / malformed -> None (probe still records rtt, just no split)
    assert _parse_server_ts({}) is None
    assert _parse_server_ts({"timestamp": "not-a-date"}) is None


def test_summarize_theta_and_percentiles():
    # Symmetric floor with a +40ms server clock offset: outbound inflated, inbound
    # deflated by ~40ms, but rtt = out+in is offset-free.
    samples = []
    for rtt in (10, 12, 14, 16, 100):  # one outlier for p95
        out = rtt / 2 + 40
        inb = rtt / 2 - 40
        samples.append({"t": 0, "out_ms": out, "in_ms": inb, "rtt_ms": rtt, "ok": True})
    s = summarize(samples)
    assert s["ok_count"] == 5
    assert s["theta_ms"] == pytest.approx(40.0, abs=0.5)   # recovered clock offset
    assert s["rtt_min_ms"] == 10
    assert s["rtt_p50_ms"] == 14
    assert s["last_rtt_ms"] == 100


def test_summarize_ignores_failed_samples():
    samples = [
        {"t": 0, "out_ms": None, "in_ms": None, "rtt_ms": None, "ok": False},
        {"t": 1, "out_ms": 5, "in_ms": 5, "rtt_ms": 10, "ok": True},
    ]
    s = summarize(samples)
    assert s["count"] == 2 and s["ok_count"] == 1
    assert s["rtt_min_ms"] == 10


@pytest.mark.asyncio
async def test_probe_once_records_split(monkeypatch):
    mon = LatencyMonitor(base_url="https://api.finam.ru", get_token=_fake_token, db_pool=None)

    async def handler(request):
        return httpx.Response(200, json={"timestamp": "2026-07-03T10:00:00Z"})

    mon._http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                  base_url="https://api.finam.ru")
    sample = await mon.probe_once()
    assert sample["ok"] is True
    assert sample["rtt_ms"] is not None and sample["rtt_ms"] >= 0
    # out + in == rtt by construction (T2 cancels)
    assert sample["out_ms"] + sample["in_ms"] == pytest.approx(sample["rtt_ms"], abs=1e-6)
    assert mon.recent()[-1] is sample
    await mon.aclose()


@pytest.mark.asyncio
async def test_probe_once_failure_is_recorded(monkeypatch):
    mon = LatencyMonitor(base_url="https://api.finam.ru", get_token=_fake_token, db_pool=None)

    async def handler(request):
        return httpx.Response(500, text="boom")

    mon._http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                  base_url="https://api.finam.ru")
    sample = await mon.probe_once()
    assert sample["ok"] is False
    assert sample["out_ms"] is None and sample["in_ms"] is None
    await mon.aclose()


async def _fake_token() -> str:
    return "test-token"
