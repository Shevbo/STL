"""Warm agent_bars/<contract>.json for every deployed robot's CURRENT contract.

The robot-window chart serves bars from agent_bars/<symbol>.json (fast path, ~0.1s).
Old contracts (e.g. RIM6) are in ohlcv_bars (fast DB path, ~0.3s), but the live U6
contracts are in neither, so market/bars falls back to MOEX ISS (~60s) → slow open.
This script pre-fetches each live robot's current contract from ISS and writes the
cache file, so EVERY robot's chart opens instantly. A daily cron keeps the tail fresh.

Run on the hoster from the app dir (so agent_bars/ resolves), with the env sourced:
    cd ~/apps/shectory-trader && poetry run python scripts/warm_robot_bars.py

Safe to re-run: writes are atomic (tmp + os.replace), empty fetches never overwrite a
good file. Old/expired contracts are skipped (they are served fine from ohlcv_bars).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta

# Repo root on sys.path so `import trader` works under cron (poetry install --no-root
# does not install the project package, and scripts/ alone is on the default path).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings  # noqa: E402
from trader.lab.iss_loader import is_specific_contract, load_bars_iss  # noqa: E402

OUT_DIR = "agent_bars"
LOOKBACK_DAYS = 75   # covers a live contract's traded life with margin to pan back


async def deployed_current_contracts(dsn: str) -> list[str]:
    """Distinct CURRENT contract per deployed robot = symbol of its latest fill."""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            """SELECT DISTINCT (
                   SELECT lt.symbol FROM live_trades lt
                   WHERE lt.robot_id = r.id ORDER BY lt.timestamp DESC LIMIT 1
               ) AS sym
               FROM robots r WHERE r.deployed"""
        )
    finally:
        await pool.close()
    # Only specific contracts (RIU6, GDU6, ...) — base codes are continuous series we
    # don't chart per-robot; None/empty (no fills yet) are skipped.
    return sorted({r["sym"] for r in rows if r["sym"] and is_specific_contract(r["sym"])})


async def warm_one(symbol: str, lo: date, hi: date) -> int:
    bars = await load_bars_iss(symbol, lo, hi, interval=1)
    if not bars:
        return 0
    rows = [[b.time, b.open, b.high, b.low, b.close, b.volume] for b in bars]
    path = os.path.join(OUT_DIR, f"{symbol}.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"key": symbol, "rows": rows}, f)
    os.replace(tmp, path)   # atomic: a concurrent reader never sees a half file
    return len(rows)


async def main() -> None:
    settings = Settings()
    if not settings.lab_db_url:
        raise SystemExit("LAB_DB_URL not set")
    os.makedirs(OUT_DIR, exist_ok=True)

    contracts = await deployed_current_contracts(settings.lab_db_url)
    hi = date.today()
    lo = hi - timedelta(days=LOOKBACK_DAYS)
    print(f"warming {len(contracts)} current contracts {lo}..{hi}: {contracts}")

    ok = 0
    for sym in contracts:
        t0 = time.time()
        try:
            n = await warm_one(sym, lo, hi)
            if n:
                ok += 1
                print(f"  {sym}: {n} bars in {time.time() - t0:.1f}s")
            else:
                print(f"  {sym}: no bars from ISS (left existing cache untouched)")
        except Exception as exc:  # noqa: BLE001 — one bad contract must not abort the rest
            print(f"  {sym}: FAILED {exc}")
        await asyncio.sleep(1)   # be polite to ISS between contracts
    print(f"done: {ok}/{len(contracts)} contracts cached")


if __name__ == "__main__":
    asyncio.run(main())
