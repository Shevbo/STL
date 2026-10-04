#!/usr/bin/env python3
"""Проверка эвристики «point_value строки лидерборда None или 1.0 = пункты» против измеренной единицы.

Измеренная единица: net перепрогона (бэкфилл `<campaign_run>-bf<rank>`) против gross_points x pv по сделкам
(showcase_volume.infer_unit). Строка лидерборда находится по campaign_run и равенству params. Дополнительно
net / initial_equity против total_return строки (если колонки заполнены).

    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/unit_heuristic_check.py [--n 15]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts import showcase_volume as sv  # noqa: E402


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def heuristic_unit(point_value) -> str:
    return "points" if point_value in (None, 1, 1.0) else "rub"


async def main(n: int) -> None:
    import asyncpg
    c = await asyncpg.connect(os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql"))
    meta = {}
    for x in await c.fetch("select symbol, point_value, price_step, price_step_value from instrument_meta"):
        pv = x["point_value"] or (x["price_step_value"] / x["price_step"]
                                  if x["price_step_value"] and x["price_step"] else None)
        if pv:
            meta[x["symbol"]] = float(pv)
    ctx = sv.BarsCtx(os.path.join(ROOT, "agent_bars"), meta)
    rows = await c.fetch("""select run_id, params, trades, net_profit from backtest_results
        where run_id ~ '-bf[0-9]$' and trades is not null order by run_id""")
    seen, out = set(), []
    for r in rows:
        cr = r["run_id"].rsplit("-bf", 1)[0]
        params = _j(r["params"])
        lb = await c.fetchrow("""select strategy, point_value, initial_equity, total_return, net_profit
            from optimization_leaderboard where campaign_run=$1 and params @> $2::jsonb and $2::jsonb @> params
            order by net_profit desc limit 1""", cr, json.dumps(params))
        if not lb or (cr, lb["strategy"]) in seen:
            continue
        tr = _j(r["trades"])
        pk = sv.peak_from_trades(tr)
        pv = ctx.pv_at(params.get("symbol"), pk[2] if pk else None)
        meas = sv.infer_unit(r["net_profit"], sv.gross_points(tr), pv)
        if meas is None:
            continue
        seen.add((cr, lb["strategy"]))
        ie, tret = lb["initial_equity"], lb["total_return"]
        out.append({"campaign": cr[:38], "strategy": lb["strategy"], "symbol": params.get("symbol"),
                    "lb_pv": lb["point_value"], "heur": heuristic_unit(lb["point_value"]), "measured": meas,
                    "pv_meta": pv, "net/ie": round(lb["net_profit"] / ie, 3) if ie else None,
                    "total_return": round(tret, 3) if tret is not None else None})
    first, seen_s = [], set()
    for o in out:  # сначала по одной на стратегию: выборка разнородная
        if o["strategy"] not in seen_s:
            seen_s.add(o["strategy"])
            first.append(o)
    sample = (first + [o for o in out if o not in first])[:n]
    for o in sample:
        print(o, "OK" if o["heur"] == o["measured"] else "ОШИБКА")
    bad = sum(o["heur"] != o["measured"] for o in sample)
    print(f"выборка {len(sample)} из {len(out)} кампаний/стратегий; ошибок эвристики {bad} ({bad / max(len(sample), 1):.0%})")
    await c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    asyncio.run(main(ap.parse_args().n))
