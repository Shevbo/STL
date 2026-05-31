"""
DB cache layer for OHLCV bars (project_stl.ohlcv_bars table).
Python reads/writes via asyncpg; schema is managed separately.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from trader.lab.runtime import Bar

if TYPE_CHECKING:
    import asyncpg


async def ensure_ohlcv_table(pool: "asyncpg.Pool") -> None:
    """Create ohlcv_bars table if it doesn't exist (idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_bars (
                symbol       TEXT NOT NULL,
                interval_min INTEGER NOT NULL DEFAULT 1,
                ts           TIMESTAMPTZ NOT NULL,
                open         DOUBLE PRECISION NOT NULL,
                high         DOUBLE PRECISION NOT NULL,
                low          DOUBLE PRECISION NOT NULL,
                close        DOUBLE PRECISION NOT NULL,
                volume       BIGINT NOT NULL,
                PRIMARY KEY (symbol, interval_min, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_ohlcv_bars_symbol_ts
                ON ohlcv_bars(symbol, interval_min, ts);
        """)


async def upsert_bars(
    pool: "asyncpg.Pool",
    symbol: str,
    bars: list[Bar],
    interval_min: int = 1,
) -> int:
    """Insert/update bars. Returns count of new rows inserted."""
    if not bars:
        return 0
    rows = [
        (
            symbol,
            interval_min,
            datetime.fromtimestamp(b.time, tz=timezone.utc),
            b.open, b.high, b.low, b.close, b.volume,
        )
        for b in bars
    ]
    async with pool.acquire() as conn:
        result = await conn.executemany(
            """
            INSERT INTO ohlcv_bars (symbol, interval_min, ts, open, high, low, close, volume)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (symbol, interval_min, ts) DO UPDATE
              SET open=EXCLUDED.open, high=EXCLUDED.high,
                  low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume
            """,
            rows,
        )
    return len(rows)


async def get_bars(
    pool: "asyncpg.Pool",
    symbol: str,
    date_from: date,
    date_to: date,
    interval_min: int = 1,
) -> list[Bar]:
    """Return cached bars sorted by time."""
    from_ts = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    to_ts = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, open, high, low, close, volume
            FROM ohlcv_bars
            WHERE symbol=$1 AND interval_min=$2 AND ts BETWEEN $3 AND $4
            ORDER BY ts
            """,
            symbol, interval_min, from_ts, to_ts,
        )
    return [
        Bar(
            time=int(r["ts"].timestamp()),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=int(r["volume"]),
        )
        for r in rows
    ]


async def count_bars(
    pool: "asyncpg.Pool",
    symbol: str,
    date_from: date,
    date_to: date,
    interval_min: int = 1,
) -> int:
    from_ts = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    to_ts = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM ohlcv_bars WHERE symbol=$1 AND interval_min=$2 AND ts BETWEEN $3 AND $4",
            symbol, interval_min, from_ts, to_ts,
        )


async def get_coverage(
    pool: "asyncpg.Pool",
    symbol: str,
    interval_min: int = 1,
) -> dict | None:
    """Return {min_ts, max_ts, count} for a symbol, or None if no data."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts, COUNT(*) AS cnt FROM ohlcv_bars WHERE symbol=$1 AND interval_min=$2",
            symbol, interval_min,
        )
    if not row or not row["cnt"]:
        return None
    return {
        "min_date": row["min_ts"].date().isoformat(),
        "max_date": row["max_ts"].date().isoformat(),
        "count": int(row["cnt"]),
    }


# ── instrument metadata mirror (DB cache of Finam params) ─────────────────────

async def ensure_instrument_meta_table(pool: "asyncpg.Pool") -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS instrument_meta (
                symbol           TEXT PRIMARY KEY,
                ticker           TEXT,
                name             TEXT,
                lot              DOUBLE PRECISION,
                price_step       DOUBLE PRECISION,
                price_step_value DOUBLE PRECISION,
                point_value      DOUBLE PRECISION,
                initial_margin   DOUBLE PRECISION,
                raw              JSONB,
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        # add column if table pre-existed without it
        await conn.execute(
            "ALTER TABLE instrument_meta ADD COLUMN IF NOT EXISTS point_value DOUBLE PRECISION;"
        )


async def refresh_instrument_spec(pool: "asyncpg.Pool", symbol: str) -> dict | None:
    """Fetch live spec from MOEX ISS and upsert into instrument_meta. Returns the meta."""
    from trader.lab.iss_loader import fetch_contract_spec
    spec = await fetch_contract_spec(symbol)
    if not spec:
        return await get_instrument_meta(pool, symbol)
    meta = {
        "symbol": symbol,
        "ticker": spec.get("ticker"),
        "name": spec.get("name"),
        "lot": spec.get("lot"),
        "price_step": spec.get("min_step"),
        "price_step_value": spec.get("step_price"),
        "point_value": spec.get("point_value"),
        "initial_margin": spec.get("initial_margin"),
        "raw": spec.get("raw", {}),
    }
    await upsert_instrument_meta(pool, meta)
    return await get_instrument_meta(pool, symbol)


async def get_instrument_meta(pool: "asyncpg.Pool", symbol: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM instrument_meta WHERE symbol=$1", symbol)
    if not row:
        return None
    d = dict(row)
    if d.get("updated_at") is not None:
        d["updated_at"] = d["updated_at"].isoformat()
    return d


async def upsert_instrument_meta(pool: "asyncpg.Pool", meta: dict) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO instrument_meta
                (symbol, ticker, name, lot, price_step, price_step_value, point_value, initial_margin, raw, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now())
            ON CONFLICT (symbol) DO UPDATE SET
                ticker=EXCLUDED.ticker, name=EXCLUDED.name, lot=EXCLUDED.lot,
                price_step=EXCLUDED.price_step, price_step_value=EXCLUDED.price_step_value,
                point_value=EXCLUDED.point_value,
                initial_margin=EXCLUDED.initial_margin, raw=EXCLUDED.raw, updated_at=now()
            """,
            meta.get("symbol"), meta.get("ticker"), meta.get("name"),
            meta.get("lot"), meta.get("price_step"), meta.get("price_step_value"),
            meta.get("point_value"), meta.get("initial_margin"), meta.get("raw", {}),
        )
