"""Ось разножки (min_gap_pts) на боевой спеке lxk22 — задания для i9.

ПОВОД (21.09.2026). Утренний лось: робот вошёл в понедельник с ATR, собранным из
мёртвого вечера выходных (средний размах бара 14-28 пт), шаг усреднения 2.1×ATR
вышел 66 пт, а реальный ход утра был 100-140 пт на бар. Позиция набралась с −1 до
−20 за 41 минуту и закрылась переворотом: −11 040 руб за день. Контрфакт по
журналу: разножка 200 пт оставила бы пик позиции 4 и фикс −2 628.

НО ОДИН ДЕНЬ НИЧЕГО НЕ ЗНАЧИТ: тот же фильтр режет доборы и в дни, когда
усреднение принесло роботу его +213 тыс. Поэтому ось гоняется парами на длинном
окне и отдельно по архивному стакану.

Считает ТОЛЬКО i9 (правило оператора): задания кладутся в очередь, агент
забирает их сам.

    STL_API=http://localhost:8000 python scripts/queue_gap_axis.py --symbol RIU6 \
        --date-from 2026-02-26 --date-to 2026-09-17
    ... --book-key bookRIU6v2 --date-from 2026-08-13   # то же по стакану
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

# Боевая спека lxk22 из зеркала агента 20.09.2026. bar_offset_min=0: бары агента
# и ISS — московская стенка как UTC (у раннера 180, у него бары в настоящем UTC).
LIVE = {"qty": 1, "avg_max": 20, "fast": 57, "slow": 48, "signal": 10, "tp_atr": 80,
        "avg_atr_n": 25, "avg_step_atr": 21, "cooldown_min": 0, "cooldown_pct": 1,
        "nd_days": 5, "gap_auto": 0, "k_avg": 20, "sl_frac": 0, "sl_pct": 100,
        "allow_long": 1, "allow_short": 1, "dv_bars": 60, "dv_range_pts": 300,
        "bet_step": 2, "bet_max": 10, "super_y": 2, "super_z": 2,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1,
        "bar_offset_min": 0, "flatten_end": 1}
GAPS = (0, 100, 150, 200, 300, 400)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--book-key", help="ключ выжимки стакана; без него — по барам")
    ap.add_argument("--campaign", default="gapaxis")
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        sys.exit("нет OPT_AGENT_TOKEN в окружении")
    headers = {"X-Agent-Token": tok, "Content-Type": "application/json"}
    mode = "book" if a.book_key else "bar"

    with httpx.Client(base_url=a.api, headers=headers, timeout=60) as client:
        tpl = {s["id"]: s for s in client.get("/api/v1/strategies").json()}
        if "macd_shectory1" not in tpl:
            sys.exit("нет шаблона macd_shectory1")
        code = tpl["macd_shectory1"]["script_code"]

        ok = err = 0
        for gap in GAPS:
            ps = {**LIVE, "min_gap_pts": gap, "symbol": a.symbol}
            if a.book_key:
                ps["book_key"] = a.book_key
            body = {
                "scriptCode": code,
                "baseParams": ps,
                "paramSets": [{}],
                "symbol": a.symbol,
                "dateFrom": f"{a.date_from}T00:00:00",
                "dateTo": f"{a.date_to}T23:59:59",
                "engine": "remote",
                # номер в имени кампании обязателен: id прогона = кампания+стратегия+символ
                "campaign": f"{a.campaign}{mode}{gap:03d}",
            }
            if a.dry_run:
                print(f"  {mode} разножка {gap}: {a.symbol} {a.date_from}..{a.date_to}")
                continue
            try:
                client.post("/api/v1/backtest/run", json=body).raise_for_status()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                err += 1
                print(f"  ошибка на разножке {gap}: {exc}")
        print(f"{mode}: поставлено {ok}, ошибок {err}"
              if not a.dry_run else "dry-run, ничего не поставлено")


if __name__ == "__main__":
    main()
