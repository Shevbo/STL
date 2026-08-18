"""Проверка строки лидерборда своим счётом: сколько контрактов она держала и чем рисковала.

Лидер кампании на растущем окне обязан быть проверен на один вопрос: он ОБОГНАЛ
«купил и держал» или просто держал лонг несколькими контрактами. Абсолютный net на
этот вопрос не отвечает, отвечает пиковая позиция и доля времени в рынке.
"""
import asyncio, json, types
from datetime import date
from trader.lab.iss_loader import load_bars_iss, fetch_contract_spec
from trader.lab.backtest import run_single_backtest
from trader.lab.commission import commission_for

SYM = "RIU6"
A, B = date(2026, 7, 17), date(2026, 7, 30)
ROWS = [
    ("лидер кампании", "bollinger_bo_m1",
     {"qty": 1, "mult": 18, "period": 33, "avg_max": 5, "avg_step_atr": 10, "avg_atr_n": 14,
      "tp_atr": 0, "sl_frac": 0, "sl_pct": 0, "dv_bars": 240, "dv_range_pts": 750,
      "min_gap_pts": 0, "min_gap_atr": 0, "cooldown_min": 0, "cooldown_pct": 1,
      "allow_long": 1, "allow_short": 1, "reg_n": 0, "reg_band": 0, "reg_mode": 1,
      "tod_m1": 0, "tod_m2": 0, "tod_s1": 3, "tod_s2": 3, "tod_s3": 3, "symbol": SYM}),
    ("второй", "bollinger_bo_m1",
     {"qty": 1, "mult": 34, "period": 33, "avg_max": 5, "avg_step_atr": 10, "avg_atr_n": 14,
      "tp_atr": 0, "sl_frac": 0, "sl_pct": 0, "dv_bars": 120, "dv_range_pts": 250,
      "min_gap_pts": 0, "min_gap_atr": 0, "cooldown_min": 0, "cooldown_pct": 1,
      "allow_long": 1, "allow_short": 1, "reg_n": 0, "reg_band": 0, "reg_mode": 1,
      "tod_m1": 0, "tod_m2": 0, "tod_s1": 3, "tod_s2": 3, "tod_s3": 3, "symbol": SYM}),
]


async def main():
    pv = (await fetch_contract_spec(SYM) or {}).get("point_value") or 1.0
    bars = await load_bars_iss(SYM, A, B, 1)
    o, c = bars[0].open, bars[-1].close
    fee = commission_for(SYM, o, 1, pv, taker=True) + commission_for(SYM, c, 1, pv, taker=True)
    peak = mae_h = 0.0
    for b in bars:
        cur = (b.close - o) * pv
        peak = max(peak, cur)
        mae_h = max(mae_h, peak - cur)
    hold = (c - o) * pv - fee
    print(f"{SYM} {A}..{B} баров {len(bars)}  {o} -> {c}")
    print(f"ЭТАЛОН купил-и-держал 1 контракт: net {hold:,.0f}  MAE {mae_h:,.0f}  "
          f"RF {hold/mae_h:.2f}  в рынке 100% времени")
    print()
    for name, sid, params in ROWS:
        mod = types.ModuleType("s")
        exec(compile("from trader.lab.strategies.library import make_on_bar\n"
                     f"on_bar = make_on_bar({sid!r})\n", "<s>", "exec"), mod.__dict__)
        r = await run_single_backtest(mod, bars, SYM, params, point_value=pv)
        fills = {}
        for t in r["trades"]:
            fills.setdefault(int(t["time"] or 0), []).append(t)
        pos, avg, realized, peak, mae, maxpos, in_mkt, expo = 0, 0.0, 0.0, None, 0.0, 0, 0, 0
        for b in bars:
            for t in fills.get(b.time, ()):
                q = t["qty"] * (1 if t["side"] == "buy" else -1)
                if pos == 0 or (pos > 0) == (q > 0):
                    avg = (avg * abs(pos) + t["price"] * abs(q)) / (abs(pos) + abs(q))
                else:
                    closed = min(abs(pos), abs(q))
                    realized += (t["price"] - avg) * closed * (1 if pos > 0 else -1) * pv
                    if abs(q) > abs(pos):
                        avg = t["price"]
                pos += q
            maxpos = max(maxpos, abs(pos))
            in_mkt += 1 if pos else 0
            expo += abs(pos)          # для СРЕДНЕЙ экспозиции по времени
            cur = realized + ((b.close - avg) * pos * pv if pos else 0.0)
            peak = cur if peak is None else max(peak, cur)
            mae = max(mae, peak - cur)
        net = r.get("net_profit")
        print(f"{name} ({sid}): net {net:,.0f}  пик позиции {maxpos} контр.  MAE {mae:,.0f}  "
              f"RF {net/mae if mae else float('inf'):.2f}")
        avg_expo = expo / len(bars)
        # ДВЕ разные величины, и обе нужны (поправка окна stl-dev-spare 17.08):
        # пик меряет ЗАРЕЗЕРВИРОВАННЫЙ капитал — ГО надо иметь под пик, даже если он
        # держался минуту; средняя экспозиция меряет ЗАНЯТЫЙ. Лестничный конфиг по
        # первой проигрывает эталону, а по второй может его обгонять втрое.
        print(f"   на ПИК-контракт {net/max(1,maxpos):,.0f} | на СРЕДНИЙ контракт "
              f"{net/avg_expo if avg_expo else 0:,.0f} (средняя экспозиция {avg_expo:.2f}) | "
              f"эталон {hold:,.0f}")
        print(f"   в рынке {100*in_mkt/len(bars):.0f}% времени | сделок {r.get('total_trades')}")

asyncio.run(main())
