"""Проверка кандидатов ТЕКУЩИМ кодом на трёх окнах + СКОЛЬКО ГО ОНИ ЗАНИМАЮТ.

ГО добавлено по заказу оператора 21.08: net без занятого капитала не сравним между
строками. Считается по бирже (INITIALMARGIN с ISS) и по СЧЁТУ (× QUIK_MARGIN_MULTIPLIER
= 2.4): брокер берёт кратно больше биржи, и отчёт на биржевой марже завышал доходность
ровно в этот раз. Две колонки: под ПИК позиции (капитал, который надо ИМЕТЬ) и под
среднюю экспозицию (капитал, который реально ЗАНЯТ).
"""
import asyncio
import json
import types
from datetime import date

from trader.lab.backtest import run_single_backtest
from trader.lab.commission import commission_for
from trader.lab.iss_loader import fetch_contract_spec, load_bars_iss

MULT = 2.4                    # QUIK_MARGIN_MULTIPLIER с хостера
WINDOWS = [("RI", date(2026, 4, 20), date(2026, 8, 21), "склейка 4 мес"),
           ("RIU6", date(2026, 7, 20), date(2026, 8, 21), "RIU6 месяц"),
           ("RIM6", date(2026, 3, 20), date(2026, 6, 15), "RIM6 чужой")]


async def main():
    cfgs = json.load(open("/tmp/cands.json", encoding="utf-8"))
    spec = await fetch_contract_spec("RIU6") or {}
    pv = spec.get("point_value") or 1.0
    go1 = (spec.get("initial_margin") or 0.0) * MULT
    print(f"ГО за контракт: биржа {spec.get('initial_margin'):,.0f} x{MULT} = "
          f"{go1:,.0f} руб на счёте, ₽/пункт {pv:.3f}")
    for name, (sid, params) in cfgs.items():
        print(f"\n=== {name} ({sid}) ===")
        for sym, a, b, note in WINDOWS:
            bars = await load_bars_iss(sym, a, b, 1)
            if len(bars) < 5000:
                print(f"  {note:<14} баров {len(bars)} — мало, пропуск")
                continue
            mod = types.ModuleType("s")
            exec(compile("from trader.lab.strategies.library import make_on_bar\n"
                         f"on_bar = make_on_bar({sid!r})\n", "<s>", "exec"), mod.__dict__)
            r = await run_single_backtest(mod, bars, sym, {**params, "symbol": sym},
                                          point_value=pv)
            # Экспозиция по времени: позицию надо разложить по барам, а не по сделкам.
            fills = {}
            for t in r["trades"]:
                fills.setdefault(int(t["time"] or 0), []).append(t)
            pos = mx = 0
            expo = 0
            for bar in bars:
                for t in fills.get(bar.time, ()):
                    pos += t["qty"] * (1 if t["side"] == "buy" else -1)
                mx = max(mx, abs(pos))
                expo += abs(pos)
            avg_expo = expo / len(bars)
            net = r.get("net_profit") or 0.0
            o, c = bars[0].open, bars[-1].close
            hold = (c - o) * pv - 2 * commission_for(sym, o, 1, pv, taker=True)
            days = (bars[-1].time - bars[0].time) / 86400
            # Доходность на ЗАНЯТЫЙ капитал, приведённая к году линейно.
            ann = (net / (avg_expo * go1) * 365 / days * 100) if avg_expo else 0.0
            print(f"  {note:<14} net {net:>10,.0f}  RF {r.get('recovery_factor'):>6.2f}  "
                  f"сделок {r.get('total_trades'):>5}  пик {mx:>3} = ГО {mx * go1:>10,.0f}  "
                  f"средн {avg_expo:>5.2f} = ГО {avg_expo * go1:>9,.0f}  "
                  f"год {ann:>7.1f}%  | купил-и-держал {hold:>9,.0f}")

asyncio.run(main())
