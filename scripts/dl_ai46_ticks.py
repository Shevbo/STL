"""Download one trading day of real trades (ticks) from MOEX ISS for a few
team-46 front contracts, for faithful OFI over a short period.

ISS serves only the latest session(s) of /trades. Each tick has BUYSELL (B/S),
the real aggressor side, so OFI is exact (not a proxy). Times are MSK wall-clock;
we store them treating MSK as UTC, matching the 1m bar convention in iss_loader.

Saves data/ai46_bt/ticks_<key>.pkl = {secid, key, point_value, date,
rows:[(unix, price, qty, side_enum)]}  side 1=buy, 2=sell.

    PYTHONPATH=. python scripts/dl_ai46_ticks.py
"""
from __future__ import annotations

import asyncio
import os
import pickle
from datetime import datetime, timezone

import httpx

from trader.lab.iss_loader import fetch_contract_spec

OUT = os.path.join("data", "ai46_bt")
# key (matches 1m bar cache) -> current front contract SECID
FRONTS = {"Si": "SiU6", "GD": "GDM6", "RI": "RIU6", "BR": "BRN6", "SR": "SRM6"}
_PAGE = 5000


def _unix(date_s: str, time_s: str) -> float:
    # MSK wall-clock stored as UTC (same quirk as iss_loader bar.time alignment).
    dt = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _download(secid: str) -> list:
    rows: list = []
    start = 0
    with httpx.Client(timeout=30, headers={"User-Agent": "STL/1.0"}) as c:
        while True:
            url = (f"https://iss.moex.com/iss/engines/futures/markets/forts/securities/"
                   f"{secid}/trades.json?iss.meta=off&start={start}")
            tr = c.get(url).json().get("trades", {})
            cols, data = tr.get("columns", []), tr.get("data", [])
            if not data:
                break
            for r in data:
                d = dict(zip(cols, r))
                side = 1 if d.get("BUYSELL") == "B" else 2
                rows.append((_unix(d["TRADEDATE"], d["TRADETIME"]),
                             float(d["PRICE"]), float(d["QUANTITY"]), side))
            start += len(data)
            if len(data) < _PAGE:
                break
    return rows


async def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for key, secid in FRONTS.items():
        path = os.path.join(OUT, f"ticks_{key}.pkl")
        if os.path.exists(path):
            print(f"skip {key} (cached)")
            continue
        rows = _download(secid)
        if not rows:
            print(f"NONE {key} ({secid})")
            continue
        spec = await fetch_contract_spec(secid)
        pv = (spec or {}).get("point_value") or 1.0
        date = datetime.utcfromtimestamp(rows[-1][0]).strftime("%Y-%m-%d")
        with open(path, "wb") as f:
            pickle.dump({"secid": secid, "key": key, "point_value": pv,
                         "date": date, "rows": rows}, f)
        buys = sum(1 for r in rows if r[3] == 1)
        print(f"OK {key:4} {secid:8} ticks={len(rows):>7} buys={buys} pv={pv} {date}")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
