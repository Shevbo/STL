"""Download + cache ~6 months of 1m bars for the team-46 top-20 FORTS symbols.

Resumable: skips a symbol whose cache file already exists. Month contracts use
the continuous front-roll loader (base code, e.g. Si/GD); perpetual *F futures
(CNYRUBF, IMOEXF, ...) use a direct single-SECID fetch. Cache is a pickle dict
{secid, key, rows:[(time,open,high,low,close,volume), ...]} per key under
data/ai46_bt/. Run repeatedly until all 20 are cached (one run may hit a wall
clock limit; the next resumes).

    python scripts/dl_ai46_bars.py
"""
from __future__ import annotations

import asyncio
import os
import pickle
import time
from datetime import date, datetime, timedelta, timezone

from trader.lab.iss_loader import (
    IssLoader,
    is_specific_contract,
    load_bars_iss,
    top_instruments,
)

OUT = os.path.join("data", "ai46_bt")
DAYS = 190  # ~6 months


def _key(secid: str) -> str:
    # month contract -> base code (SiU6 -> Si); perpetual -> secid as-is
    return secid[:-2] if is_specific_contract(secid) else secid


async def _fetch(secid: str):
    today = date.today()
    frm = today - timedelta(days=DAYS)
    if is_specific_contract(secid):
        return await load_bars_iss(_key(secid), frm, today, interval=1)  # continuous roll
    async with IssLoader() as ld:
        return await ld.fetch_contract_bars(secid, frm, today, 1)        # perpetual direct


async def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    syms = await top_instruments(20)
    # month contracts first (fast, reliable), perpetuals last (slow paginated)
    syms = sorted(syms, key=lambda s: (0 if is_specific_contract(s) else 1))
    print(f"symbols ({len(syms)}): {syms}")
    for secid in syms:
        key = _key(secid)
        path = os.path.join(OUT, key + ".pkl")
        if os.path.exists(path):
            print(f"skip {key} (cached)")
            continue
        t0 = time.time()
        try:
            bars = await _fetch(secid)
        except Exception as exc:  # noqa: BLE001
            print(f"ERR  {key:10} {type(exc).__name__}: {exc}")
            continue
        if not bars:
            print(f"NONE {key:10} (no bars)")
            continue
        rows = [(b.time, b.open, b.high, b.low, b.close, b.volume) for b in bars]
        with open(path, "wb") as f:
            pickle.dump({"secid": secid, "key": key, "rows": rows}, f)
        lo = datetime.fromtimestamp(rows[0][0], tz=timezone.utc).date()
        hi = datetime.fromtimestamp(rows[-1][0], tz=timezone.utc).date()
        print(f"OK   {key:10} bars={len(rows):>7}  {lo}..{hi}  {time.time() - t0:.0f}s")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
