"""Разметка истории RI детектором режима: где рос, где падал, где стоял.

По КОНТРАКТАМ, а не по склейке: у склейки на ролле реальный скачок цены, и окно,
попавшее на шов, разметится ходом, которого рынок не делал. Контракт RI живёт как
раз около 12 недель — ровно тот фрейм, который заказан.
"""
import asyncio, json, sys
from datetime import date, timedelta
from trader.lab.iss_loader import load_bars_iss
from trader.lab.trend_detector import detect_regime

CONTRACTS = ["RIZ5", "RIH6", "RIM6", "RIU6"]
WEEKS = [4, 8, 12]


async def main():
    out = {}
    for sym in CONTRACTS:
        try:
            bars = await load_bars_iss(sym, date(2025, 9, 1), date(2026, 8, 17), 24)
        except Exception as exc:
            out[sym] = {"error": str(exc)[:120]}
            continue
        if len(bars) < 20:
            out[sym] = {"error": f"мало баров: {len(bars)}"}
            continue
        rows = []
        for w in WEEKS:
            n = w * 5                      # торговых дней в окне
            if len(bars) < n:
                continue
            step = max(1, n // 4)
            for i in range(0, len(bars) - n + 1, step):
                seg = bars[i:i + n]
                r = detect_regime(seg)
                rows.append({"weeks": w,
                             "from": date.fromtimestamp(seg[0].time).isoformat(),
                             "to": date.fromtimestamp(seg[-1].time).isoformat(),
                             **r.as_dict()})
        out[sym] = {"bars": len(bars),
                    "first": date.fromtimestamp(bars[0].time).isoformat(),
                    "last": date.fromtimestamp(bars[-1].time).isoformat(),
                    "windows": rows}
    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
