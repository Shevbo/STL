"""Backfill REAL-money fills into algo_trades from the runner's per-robot text logs.

The QUIK trades table holds only the current session and the runner keeps just a
200-fill tail, so the ledger never saw the early real-money history — including
robots that traded real, lost money, and were later switched back to paper (they
are not in the mirror as real, so the tail backfill skipped them entirely).

The runner's own per-robot log DOES have it: every fill with its price, the
resulting position/avg and the CUMULATIVE realized in points. This replays that
into algo_trades. Per-fill P&L is the delta of the runner's cumulative realized —
its own arithmetic, not a re-derivation.

Only fills OLDER than what the ledger already holds for a robot are inserted, so
running this never double-counts. Idempotent besides: rows carry `lg:` dedup keys.

Run ON the hoster (needs ~/.shectory_trade.env in the environment):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    $(poetry env info --path)/bin/python scripts/backfill_from_logs.py --dir ~/robot_logs [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.lab.commission import commission_for  # noqa: E402
from trader.quik.algo_ledger import drop_already_ledgered, parse_runner_log  # noqa: E402

_MSK = datetime.timezone(datetime.timedelta(hours=3))
_ORDER_SYM = re.compile(r"\[ORDER\] (?:buy|sell) \d+ ([A-Za-z0-9]+) @")
_COLS = ("robot_id", "mode", "ts_ms", "trade_num", "order_num", "symbol", "side",
         "qty", "price", "order_kind", "point_value", "pnl_gross_rub",
         "commission_rub", "pnl_net_rub", "pos_after", "avg_after", "dedup_key")


def _ts_ms(ts: str) -> int:
    """'2026-07-14 18:40:01' (MSK wall clock, as the runner logs it) -> epoch ms."""
    return int(datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=_MSK).timestamp() * 1000)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill algo_trades from runner logs")
    ap.add_argument("--dir", required=True, help="directory with <robot_id>.log files")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import asyncpg
    dsn = (os.environ.get("LAB_DB_URL") or "").replace("postgresql+asyncpg", "postgresql")
    if not dsn:
        sys.exit("LAB_DB_URL not set (source ~/.shectory_trade.env)")
    conn = await asyncpg.connect(dsn)
    try:
        pv_by_symbol = {r["symbol"]: float(r["point_value"]) for r in await conn.fetch(
            "SELECT DISTINCT ON (symbol) symbol, point_value FROM algo_trades "
            "ORDER BY symbol, seq")}
        total_ins = 0
        for fn in sorted(os.listdir(args.dir)):
            if not fn.endswith(".log"):
                continue
            rid = fn[:-4]
            text = open(os.path.join(args.dir, fn), encoding="utf-8", errors="replace").read()
            fills = parse_runner_log(text)
            if not fills:
                print(f"{rid}: реальных филлов в логе нет — пропуск")
                continue
            sym_m = _ORDER_SYM.search(text)
            if not sym_m:
                print(f"{rid}: не удалось определить инструмент — пропуск")
                continue
            symbol = sym_m.group(1)
            pv = pv_by_symbol.get(symbol)
            if not pv:
                print(f"{rid}: нет ₽/пункт для {symbol} в журнале — пропуск")
                continue
            # Everything the ledger already holds for this robot stays untouched:
            # only strictly OLDER fills are added, so the two never overlap.
            cutoff = await conn.fetchval(
                "SELECT min(ts_ms) FROM algo_trades WHERE robot_id=$1 AND mode='real'", rid)
            rows = []
            for f in fills:
                ts_ms = _ts_ms(f["ts"])
                if cutoff is not None and ts_ms >= int(cutoff):
                    continue
                gross = f["gross_points"] * pv
                comm = commission_for(symbol, f["price"], f["qty"], pv, taker=True)
                rows.append({
                    "robot_id": rid, "mode": "real", "ts_ms": ts_ms, "trade_num": None,
                    "order_num": None, "symbol": symbol, "side": f["side"],
                    "qty": f["qty"], "price": f["price"], "order_kind": "market",
                    "point_value": pv, "pnl_gross_rub": round(gross, 2),
                    "commission_rub": round(comm, 2),
                    "pnl_net_rub": round(gross - comm, 2),
                    "pos_after": f["pos_after"], "avg_after": f["avg_after"],
                    # pos_after discriminates a reversal's two fills: the close and the
                    # opposite open land in the SAME second at the SAME price/qty/side
                    # and collide without it (9 fills were swallowed on the first run).
                    "dedup_key": (f"lg:{rid}:{ts_ms}:{f['side']}:{f['qty']}"
                                  f":{f['price']:g}:{f['pos_after']}"),
                })
            if not rows:
                print(f"{rid}: журнал уже покрывает весь лог — добавлять нечего")
                continue
            # Страховка поверх cutoff: та же сделка могла попасть в журнал ДРУГИМ
            # бэкфиллом (bf:/q:) под другим dedup_key — UNIQUE это не ловит, а
            # двойная запись ломает реплей (24.07.2026: OPEN рисовался как AVR).
            have = [dict(x) for x in await conn.fetch(
                "SELECT ts_ms, side, qty, price FROM algo_trades WHERE robot_id=$1", rid)]
            before = len(rows)
            rows = drop_already_ledgered(have, rows)
            if before != len(rows):
                print(f"{rid}: {before - len(rows)} сделок уже есть в журнале "
                      f"(другой бэкфилл) — пропущены")
            if not rows:
                continue
            ins = 0
            if not args.dry_run:
                for row in rows:
                    got = await conn.fetchrow(
                        f"""INSERT INTO algo_trades ({','.join(_COLS)})
                            VALUES ({','.join(f'${i + 1}' for i in range(len(_COLS)))})
                            ON CONFLICT (dedup_key) DO NOTHING RETURNING seq""",
                        *[row[c] for c in _COLS])
                    ins += 1 if got is not None else 0
            net = sum(r["pnl_net_rub"] for r in rows)
            span = f"{fills[0]['ts'][5:16]}..{rows[-1]['ts_ms']}"
            print(f"{rid}: {len(rows)} филлов до журнала, net {net:+.2f} ₽ "
                  f"({symbol}, ₽/пункт {pv}) -> {'DRY-RUN' if args.dry_run else f'{ins} вставлено'}"
                  f"  [{span}]")
            total_ins += ins
        print(f"\nИТОГО вставлено: {total_ins}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
