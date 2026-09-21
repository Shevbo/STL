"""Ось на КАЖДОМ контракте отдельно — задания для i9.

ЗАЧЕМ ПОКОНТРАКТНО. Склейка через экспирацию даёт ложные скачки цены на роллах, и
строка, выигравшая на склейке, может выигрывать на швах. Каждый контракт — своя
выборка: судим по числу контрактов, где ось помогла, а не по одной сумме.

Окно каждого задания берётся ИЗ САМОГО ФАЙЛА баров: агент сверяет, покрывает ли
кэш оба края запрошенного окна, и при несовпадении молча уходит в ISS — за
несуществующим кодом вроде spRIH2 он там ничего не найдёт.

    STL_API=http://localhost:8000 python scripts/queue_contract_axis.py \
        --prefix sp --axis avg_from_last=0,1 --campaign fromlast
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

import httpx

LIVE = {"qty": 1, "avg_max": 20, "fast": 57, "slow": 48, "signal": 10, "tp_atr": 80,
        "avg_atr_n": 25, "avg_step_atr": 21, "cooldown_min": 0, "cooldown_pct": 1,
        "nd_days": 5, "gap_auto": 0, "k_avg": 20, "sl_frac": 0, "sl_pct": 100,
        "allow_long": 1, "allow_short": 1, "dv_bars": 60, "dv_range_pts": 300,
        "bet_step": 2, "bet_max": 10, "super_y": 2, "super_z": 2,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1,
        "bar_offset_min": 0, "flatten_end": 1}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="sp")
    ap.add_argument("--dir", default="agent_bars")
    ap.add_argument("--axis", required=True, help="КЛЮЧ=знач,знач")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        sys.exit("нет OPT_AGENT_TOKEN в окружении")
    key, _, vals = a.axis.partition("=")
    key = key.strip()
    grid = [int(v) for v in vals.split(",") if v.strip()]

    files = sorted(glob.glob(os.path.join(os.path.expanduser(a.dir), f"{a.prefix}*.json")))
    if not files:
        sys.exit(f"нет файлов {a.prefix}*.json в {a.dir}")

    headers = {"X-Agent-Token": tok, "Content-Type": "application/json"}
    with httpx.Client(base_url=a.api, headers=headers, timeout=60) as client:
        code_tpl = {s["id"]: s for s in client.get("/api/v1/strategies").json()}
        script = code_tpl["macd_shectory1"]["script_code"]

        ok = err = 0
        for path in files:
            sym = os.path.basename(path)[:-5]
            rows = json.load(open(path, encoding="utf-8"))["rows"]
            d0 = datetime.fromtimestamp(rows[0][0], tz=timezone.utc).strftime("%Y-%m-%d")
            d1 = datetime.fromtimestamp(rows[-1][0], tz=timezone.utc).strftime("%Y-%m-%d")
            for i, v in enumerate(grid):
                body = {
                    "scriptCode": script,
                    "baseParams": {**LIVE, key: v, "symbol": sym},
                    "paramSets": [{}],
                    "symbol": sym,
                    "dateFrom": f"{d0}T00:00:00",
                    "dateTo": f"{d1}T23:59:59",
                    "engine": "remote",
                    "campaign": f"{a.campaign}{sym}v{i}",
                }
                if a.dry_run:
                    print(f"  {sym} {key}={v}: {d0}..{d1} ({len(rows)} баров)")
                    continue
                try:
                    client.post("/api/v1/backtest/run", json=body).raise_for_status()
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    err += 1
                    print(f"  ошибка {sym} {key}={v}: {exc}")
        print(f"поставлено {ok}, ошибок {err}" if not a.dry_run else "dry-run")


if __name__ == "__main__":
    main()
