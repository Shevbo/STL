"""Backfill REAL robots' pre-ledger fill history into algo_trades.

The QUIK trades table holds only the current session, so the ONLY source of a
real robot's history from before the ledger went live is the runner's persisted
fills tail (RobotStatusReport.recent_fills, 200 max), which the mirror exposes.
The tail is replayed from flat (the runner resets it on the paper->real arming
flip); if the replay does not land exactly on the position the ledger seeded
with, the tail is incomplete and that robot is SKIPPED — no guessed history.

Run ON the hoster (needs ~/.shectory_trade.env in the environment):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    $(poetry env info --path)/bin/python scripts/backfill_algo_ledger.py [--dry-run]

Idempotent: rows carry bf:-prefixed dedup keys, re-runs insert nothing new.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.auth.portal import make_session_token  # noqa: E402
from trader.quik.algo_ledger import backfill_real_tail, drop_already_ledgered  # noqa: E402

API = os.environ.get("STL_API_LOCAL", "http://localhost:8000")
_COLS = ("robot_id", "mode", "ts_ms", "trade_num", "order_num", "symbol", "side",
         "qty", "price", "order_kind", "point_value", "pnl_gross_rub",
         "commission_rub", "pnl_net_rub", "pos_after", "avg_after", "dedup_key")


def _fetch_mirror() -> dict:
    tok = make_session_token("bshevelev75@gmail.com",
                             os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    req = urllib.request.Request(f"{API}/api/v1/quik/robots-mirror",
                                 headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req, timeout=30))


async def main() -> None:
    dry = "--dry-run" in sys.argv
    import asyncpg
    dsn = (os.environ.get("LAB_DB_URL") or "").replace("postgresql+asyncpg", "postgresql")
    if not dsn:
        sys.exit("LAB_DB_URL not set (source ~/.shectory_trade.env)")
    mirror = _fetch_mirror()
    robots = [r for r in (mirror.get("robots") or []) if not r.get("paper")]
    if not robots:
        sys.exit("mirror has no REAL robots (agent offline or empty mirror)")

    conn = await asyncpg.connect(dsn)
    try:
        pv_by_symbol = {r["symbol"]: r["point_value"] for r in await conn.fetch(
            "SELECT DISTINCT ON (symbol) symbol, point_value FROM algo_trades "
            "ORDER BY symbol, seq DESC")}
        for r in robots:
            rid = r.get("robot_id") or ""
            st = await conn.fetchrow(
                "SELECT position, seeded_at_ms FROM algo_ledger_state WHERE robot_id=$1", rid)
            if st is None:
                print(f"{rid}: no ledger state (never seeded) — skip")
                continue
            # The state position advances with every ingested fill; rewind the
            # ledgered deltas to recover the position AT SEED TIME.
            delta = await conn.fetchval(
                "SELECT coalesce(sum(CASE WHEN side='buy' THEN qty ELSE -qty END),0) "
                "FROM algo_trades WHERE robot_id=$1 AND dedup_key NOT LIKE 'bf:%'", rid)
            seed_pos = int(st["position"]) - int(delta)
            symbol = r.get("symbol") or ""
            pv = pv_by_symbol.get(symbol)
            if not pv:
                print(f"{rid}: no point_value for {symbol!r} in ledger — skip")
                continue
            rows = backfill_real_tail(rid, symbol, r.get("recent_fills") or [],
                                      int(st["seeded_at_ms"]), seed_pos, float(pv))
            if rows is None:
                print(f"{rid}: tail does not replay to seeded pos {seed_pos} — "
                      f"incomplete (200-cut?), skip")
                continue
            if not rows:
                print(f"{rid}: no pre-seed real fills in tail — nothing to do")
                continue
            # The OTHER backfill (scripts/backfill_from_logs.py, `lg:` keys) may
            # already hold these fills under a different dedup_key — the UNIQUE
            # index cannot see that, and a double insert corrupts the replay
            # (2026-07-24: one boundary fill per real robot, OPEN drawn as AVR).
            have = [dict(x) for x in await conn.fetch(
                "SELECT ts_ms, side, qty, price FROM algo_trades WHERE robot_id=$1", rid)]
            before = len(rows)
            rows = drop_already_ledgered(have, rows)
            if before != len(rows):
                print(f"{rid}: {before - len(rows)} fills already in the ledger "
                      f"(other backfill) — skipped")
            ins = 0
            if not dry:
                for row in rows:
                    got = await conn.fetchrow(
                        f"""INSERT INTO algo_trades ({','.join(_COLS)})
                            VALUES ({','.join(f'${i + 1}' for i in range(len(_COLS)))})
                            ON CONFLICT (dedup_key) DO NOTHING RETURNING seq""",
                        *[row[c] for c in _COLS])
                    ins += 1 if got is not None else 0
            net = sum(row["pnl_net_rub"] for row in rows)
            print(f"{rid}: {len(rows)} pre-seed fills, net {net:+.2f} ₽ -> "
                  f"{'DRY-RUN' if dry else f'{ins} inserted'}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
