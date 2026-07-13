"""
US-Open Opening Range + Fair Value Gap (ICT "Silver Bullet" style).

Idea (one trade per day, anchored to the US equity open = 16:30 MSK / 09:30 New York):
  1. The FIRST `range_min`-minute candle after 16:30 MSK sets the day's opening range
     [RH, RL] (its high and low). On FORTS M1 bars that is the 16:30..16:44 block.
  2. After the range closes, on the 1-minute chart wait for BOTH a range breakout AND a
     Fair Value Gap (3-bar imbalance) in the SAME direction:
        long  : close > RH  and bullish FVG (low[-1] > high[-3], body >= min_frac)
        short : close < RL  and bearish FVG (high[-1] < low[-3], -body >= min_frac)
  3. ENTER ONCE. Stop just beyond the reference candle (long: RL - sl_buf, short: RH +
     sl_buf); take-profit at rr:1 of that risk (default 2:1).
  4. Exit on TP, SL, or a forced end-of-day flatten (23:45 MSK). NEVER re-enter until the
     next day — one setup per session by design.

Bars are MSK-wall-clock stamped as UTC (verified against the RI session 09:00..23:50), so
datetime.utcfromtimestamp(bar.time) yields MSK wall time directly.

Every ambiguous choice (range length, entry window, FVG threshold, R:R, sides, buffer) is a
parameter so the optimizer can sweep it. Standalone module (own per-day state machine) — the
signal-based make_on_bar framework cannot hold "entered today / stop / target" state.
"""
from datetime import datetime, timezone

from trader.lab.runtime import STLRuntime

_EOD_HM = 23 * 60 + 45          # flatten any open position at 23:45 MSK (before session close)


def _hm_day(t: int):
    """(minutes-since-midnight MSK, YYYYMMDD) for a bar epoch."""
    d = datetime.fromtimestamp(t, tz=timezone.utc)   # bars are MSK stamped as UTC
    return d.hour * 60 + d.minute, d.year * 10000 + d.month * 100 + d.day


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"US-Open FVG started | open={params.get('open_hour',16)}:{params.get('open_min',30)} "
            f"range={params.get('range_min',15)}m rr={float(params.get('rr_x10',20))/10:.1f}:1 "
            f"symbol={params.get('symbol')}")


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    open_hm = int(params.get("open_hour", 16)) * 60 + int(params.get("open_min", 30))
    range_min = int(params.get("range_min", 15))
    signal_min = int(params.get("signal_min", 60))
    min_frac = float(params.get("min_frac", 5)) / 10000.0
    rr = float(params.get("rr_x10", 20)) / 10.0
    sl_buf = float(params.get("sl_buf", 0) or 0)
    req_fvg = int(params.get("req_fvg", 1))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))
    flatten_eod = int(params.get("flatten_eod", 1))

    need = max(5, range_min + signal_min + 10)       # reach back to the opening range all day
    bars = await stl.get_bars(symbol, tf=1, n=need)
    if len(bars) < 3:
        return
    cur = bars[-1]
    hm, day = _hm_day(cur.time)

    # New trading day -> reset the whole state machine.
    if stl.get_state("day") != day:
        stl.set_state("day", day)
        stl.set_state("rh", None)
        stl.set_state("rl", None)
        stl.set_state("entered", 0)
        stl.set_state("done", 0)
        stl.set_state("dir", 0)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)

    # 1) Manage an open position: take-profit / stop-loss / end-of-day flatten.
    if cur_qty != 0 and stl.get_state("entered"):
        sl = stl.get_state("sl")
        tp = stl.get_state("tp")
        dirn = stl.get_state("dir")
        if dirn > 0:                                  # long
            if cur.low <= sl:                         # stop first (conservative on a wide bar)
                await stl.place_order(symbol, "sell", abs(cur_qty), sl)
                stl.set_state("done", 1)
                return
            if cur.high >= tp:
                await stl.place_order(symbol, "sell", abs(cur_qty), tp)
                stl.set_state("done", 1)
                return
        else:                                         # short
            if cur.high >= sl:
                await stl.place_order(symbol, "buy", abs(cur_qty), sl)
                stl.set_state("done", 1)
                return
            if cur.low <= tp:
                await stl.place_order(symbol, "buy", abs(cur_qty), tp)
                stl.set_state("done", 1)
                return
        if flatten_eod and hm >= _EOD_HM:
            await stl.place_order(symbol, "sell" if dirn > 0 else "buy", abs(cur_qty), cur.close)
            stl.set_state("done", 1)
        return

    # Already traded (or trade finished) today -> stay out until tomorrow.
    if stl.get_state("done") or stl.get_state("entered"):
        return

    # 2) Build the opening range once, right after it closes.
    rh = stl.get_state("rh")
    rl = stl.get_state("rl")
    if rh is None:
        if hm < open_hm + range_min:
            return                                    # range still forming (or before US open)
        win = []
        for b in bars:
            bhm, bday = _hm_day(b.time)
            if bday == day and open_hm <= bhm < open_hm + range_min:
                win.append(b)
        if not win:
            return                                    # no bars in the window this day -> skip
        rh = max(b.high for b in win)
        rl = min(b.low for b in win)
        stl.set_state("rh", rh)
        stl.set_state("rl", rl)

    # 3) Entry only inside the signal window after the range.
    if hm < open_hm + range_min or hm > open_hm + range_min + signal_min:
        return

    bull_fvg = bars[-1].low > bars[-3].high
    bear_fvg = bars[-1].high < bars[-3].low
    body = (cur.close - cur.open) / cur.close if cur.close else 0.0

    if allow_long and cur.close > rh and (not req_fvg or (bull_fvg and body >= min_frac)):
        entry = cur.close
        sl = rl - sl_buf
        risk = entry - sl
        if risk <= 0:
            return
        tp = entry + rr * risk
        await stl.place_order(symbol, "buy", qty, entry)
        stl.set_state("entered", 1)
        stl.set_state("dir", 1)
        stl.set_state("sl", sl)
        stl.set_state("tp", tp)
        stl.log(f"LONG {entry:.0f} SL={sl:.0f} TP={tp:.0f} (range {rl:.0f}-{rh:.0f})")
        return
    if allow_short and cur.close < rl and (not req_fvg or (bear_fvg and -body >= min_frac)):
        entry = cur.close
        sl = rh + sl_buf
        risk = sl - entry
        if risk <= 0:
            return
        tp = entry - rr * risk
        await stl.place_order(symbol, "sell", qty, entry)
        stl.set_state("entered", 1)
        stl.set_state("dir", -1)
        stl.set_state("sl", sl)
        stl.set_state("tp", tp)
        stl.log(f"SHORT {entry:.0f} SL={sl:.0f} TP={tp:.0f} (range {rl:.0f}-{rh:.0f})")
        return


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("US-Open FVG stopped")


STRATEGY_META = {
    "name": "US-Open Range + FVG",
    "description": (
        "ICT opening-range + Fair Value Gap. One trade per day off the US equity open."
    ),
    "source": "ICT Silver Bullet concept",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "range_min", "label": "Мин. опорной свечи", "type": "number", "default": 15, "min": 5, "max": 30,
         "hint": "Длина опорной свечи после 16:30 МСК (хай/лоу = диапазон дня)"},
        {"key": "signal_min", "label": "Окно входа (мин)", "type": "number", "default": 60, "min": 15, "max": 180,
         "hint": "Сколько минут после закрытия диапазона разрешён вход"},
        {"key": "rr_x10", "label": "R:R ×10 (20=2:1)", "type": "number", "default": 20, "min": 5, "max": 50,
         "hint": "Тейк = R:R × риск. 20 = цель 2:1 к стопу"},
        {"key": "min_frac", "label": "Мин. тело FVG ×10000", "type": "number", "default": 5, "min": 0, "max": 50,
         "hint": "Порог тела подтверждающей свечи (5 = 0.05%)"},
        {"key": "sl_buf", "label": "Буфер стопа (пункты)", "type": "number", "default": 0, "min": 0, "max": 200,
         "hint": "Доп. отступ стопа за опорную свечу"},
        {"key": "req_fvg", "label": "Требовать FVG (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "1 = нужен FVG в сторону пробоя, 0 = только пробой"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить входы в лонг"},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить входы в шорт"},
        {"key": "flatten_eod", "label": "Флэт в конце дня (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "1 = закрыть по рынку в 23:45 МСК, если TP/SL не сработали"},
        {"key": "qty", "label": "Контрактов", "type": "number", "default": 1, "min": 1, "max": 10, "hint": "Лотность"},
    ],
}
