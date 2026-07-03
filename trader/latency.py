"""Exchange-latency monitor for the LIVE-robots screen.

Probes the Finam API gateway on a fixed 5s cadence with the lightweight, side-effect-free
``GET /v1/assets/clock`` (server-time) call, over the SAME HTTP/2 transport the LIVE robots
use to place orders — so the measured round-trip reflects the real order path, not some
unrelated channel.

Three timestamps per probe (the trader-standard decomposition):

    T1 = local wall-clock right BEFORE the request is sent
    T2 = server timestamp carried in the response body (the server's own clock when it
         generated the reply — the "half-way" point)
    T3 = local wall-clock right AFTER the response is received

From them:

    rtt_ms      = T3 - T1   round-trip. Clock-offset-free -> the OBJECTIVE ping.
    outbound_ms = T2 - T1   send -> server.   } each carries the local-vs-server clock
    inbound_ms  = T3 - T2   server -> receive. } offset theta; their SUM (rtt) cancels it.

Because the outbound/inbound split is biased by the unknown clock offset theta (only exact
when both clocks are NTP-synced), :func:`summarize` also derives a rolling theta estimate so
the split can be shown offset-corrected (both >= 0) without assuming synced clocks. Raw
values are stored untouched; correction is a presentation concern.

Samples live in an in-memory ring (fast recent reads for the live chart) AND are persisted
to Postgres ``latency_samples`` for the historical view (survives a restart).
"""

import asyncio
import time
from collections import deque
from datetime import datetime

import httpx
import structlog

log = structlog.get_logger()

CLOCK_PATH = "/v1/assets/clock"
SAMPLE_INTERVAL_S = 5.0
RING_MAX = 20_000          # ~28h at 5s: covers a full session for the live chart
PROBE_TIMEOUT_S = 10.0


def _parse_server_ts(body: dict) -> float | None:
    """Server clock as epoch seconds (float) from a ClockResponse JSON body.

    grpc-gateway encodes a protobuf Timestamp either as an RFC3339 string or as
    ``{"seconds": ..., "nanos": ...}`` — accept both.
    """
    ts = body.get("timestamp") if isinstance(body, dict) else None
    if not ts:
        return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    if isinstance(ts, dict):
        return float(ts.get("seconds", 0) or 0) + float(ts.get("nanos", 0) or 0) / 1e9
    return None


class LatencyMonitor:
    """Periodic Finam-gateway latency probe + in-memory ring + optional DB persistence."""

    def __init__(self, base_url: str, get_token, db_pool=None, link: str = "finam") -> None:
        self._get_token = get_token
        self._db = db_pool
        self._link = link
        self._http = httpx.AsyncClient(http2=True, base_url=base_url, timeout=PROBE_TIMEOUT_S)
        self._ring: deque = deque(maxlen=RING_MAX)
        self._last: dict | None = None

    async def ensure_table(self) -> None:
        if not self._db:
            return
        try:
            await self._db.execute(
                """CREATE TABLE IF NOT EXISTS latency_samples (
                       id      BIGSERIAL PRIMARY KEY,
                       link    TEXT NOT NULL DEFAULT 'finam',
                       ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
                       out_ms  DOUBLE PRECISION,
                       in_ms   DOUBLE PRECISION,
                       rtt_ms  DOUBLE PRECISION,
                       ok      BOOLEAN NOT NULL DEFAULT true
                   )""",
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_latency_link_ts ON latency_samples (link, ts)",
            )
        except Exception as exc:  # never block startup on the diagnostic table
            log.warning("latency.table_init_failed", error=str(exc))

    async def probe_once(self) -> dict:
        """One clock probe. Always records a sample (ok=false on failure)."""
        t1 = time.time()
        out_ms: float | None = None
        in_ms: float | None = None
        rtt_ms: float | None = None
        ok = True
        try:
            token = await self._get_token()
            resp = await self._http.get(CLOCK_PATH, headers={"Authorization": f"Bearer {token}"})
            t3 = time.time()
            resp.raise_for_status()
            rtt_ms = (t3 - t1) * 1000.0
            t2 = _parse_server_ts(resp.json())
            if t2 is not None:
                out_ms = (t2 - t1) * 1000.0
                in_ms = (t3 - t2) * 1000.0
        except Exception as exc:
            ok = False
            log.warning("latency.probe_failed", link=self._link, error=str(exc))

        sample = {"t": t1, "out_ms": out_ms, "in_ms": in_ms, "rtt_ms": rtt_ms, "ok": ok}
        self._ring.append(sample)
        self._last = sample
        if self._db:
            try:
                await self._db.execute(
                    "INSERT INTO latency_samples (link, ts, out_ms, in_ms, rtt_ms, ok) "
                    "VALUES ($1, to_timestamp($2), $3, $4, $5, $6)",
                    self._link, t1, out_ms, in_ms, rtt_ms, ok,
                )
            except Exception as exc:
                log.warning("latency.persist_failed", error=str(exc))
        return sample

    def recent(self, since_epoch: float | None = None) -> list[dict]:
        if since_epoch is None:
            return list(self._ring)
        return [s for s in self._ring if s["t"] >= since_epoch]

    async def history(self, since_epoch: float) -> list[dict]:
        """Samples since ``since_epoch`` from Postgres (authoritative, survives restart);
        falls back to the in-memory ring if the DB is unavailable."""
        if not self._db:
            return self.recent(since_epoch)
        try:
            rows = await self._db.fetch(
                "SELECT extract(epoch FROM ts) AS t, out_ms, in_ms, rtt_ms, ok "
                "FROM latency_samples WHERE link=$1 AND ts >= to_timestamp($2) ORDER BY ts",
                self._link, since_epoch,
            )
            return [
                {"t": float(r["t"]), "out_ms": r["out_ms"], "in_ms": r["in_ms"],
                 "rtt_ms": r["rtt_ms"], "ok": r["ok"]}
                for r in rows
            ]
        except Exception as exc:
            log.warning("latency.history_failed", error=str(exc))
            return self.recent(since_epoch)

    @property
    def last(self) -> dict | None:
        return self._last

    async def run(self) -> None:
        await self.ensure_table()
        while True:
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the loop alive no matter what
                log.warning("latency.sampler_error", error=str(exc))
            await asyncio.sleep(SAMPLE_INTERVAL_S)

    async def aclose(self) -> None:
        try:
            await self._http.aclose()
        except Exception:
            pass


def summarize(samples: list[dict]) -> dict:
    """Roll up a sample window: RTT percentiles + a median clock-offset estimate (theta).

    theta ~ median((outbound - inbound) / 2): if the network floor is roughly symmetric,
    this is the local-vs-server clock offset, and (outbound - theta, inbound + theta) is
    the offset-corrected, physically sensible split.
    """
    oks = [s for s in samples if s.get("ok") and s.get("rtt_ms") is not None]
    rtts = sorted(s["rtt_ms"] for s in oks)
    pairs = [(s["out_ms"], s["in_ms"]) for s in oks
             if s.get("out_ms") is not None and s.get("in_ms") is not None]
    theta = 0.0
    if pairs:
        diffs = sorted((o - i) / 2.0 for o, i in pairs)
        theta = diffs[len(diffs) // 2]

    def pct(p: float) -> float | None:
        if not rtts:
            return None
        k = min(len(rtts) - 1, int(p / 100.0 * len(rtts)))
        return round(rtts[k], 2)

    return {
        "count": len(samples),
        "ok_count": len(oks),
        "theta_ms": round(theta, 2),
        "last_rtt_ms": round(oks[-1]["rtt_ms"], 2) if oks else None,
        "rtt_min_ms": round(rtts[0], 2) if rtts else None,
        "rtt_p50_ms": pct(50),
        "rtt_p95_ms": pct(95),
    }
