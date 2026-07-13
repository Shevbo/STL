"""
US-Open Opening Range + FVG / Retest (ICT "Silver Bullet" + Влад Дудукчанов "Свеча 9:30").

One trade per day, anchored to the US equity open (16:30 MSK / 09:30 New York):
  1. The FIRST `range_min`-minute candle after the open sets the day's range [RH, RL] (its
     high and low). On FORTS M1 bars with range_min=5 that is the 16:30..16:34 block.
  2. After the range closes, on the 1-minute chart form the entry by ONE of two modes:
       entry_mode 0  FVG breakout : close beyond the level + a same-direction Fair Value Gap
                                    (long: close>RH, low[-1]>high[-3], body>=min_frac).
       entry_mode 1  RETEST       : the video's false-breakout filter. A 1-min close beyond
                                    the level, THEN wait for price to return (retest) to that
                                    level and reject with a strong "shaved" bar closing back
                                    in the trade direction (body/range >= rej_frac). Enter on
                                    that bar's close. Filters out false breakouts that never
                                    hold the level on the retest.
       entry_mode 2  either       : whichever fires first.
  3. ENTER ONCE. Stop = a signed % of the range height from the range edge (see stop_pct).
     Take-profit at rr:1 of that risk (default 2:1).
  4. Exit on TP, SL, or a forced end-of-day flatten (23:45 MSK). NEVER re-enter until the
     next day — one setup per session by design.

Bars are MSK-wall-clock stamped as UTC (verified against the RI session 09:00..23:50), so
datetime.utcfromtimestamp(bar.time) yields MSK wall time directly.

Standalone module (own per-day state machine) — the signal-based make_on_bar framework
cannot hold "entered today / broke / stop / target" state. Every choice is a swept param.
"""
from datetime import datetime, timezone

from trader.lab.runtime import STLRuntime

_EOD_HM = 23 * 60 + 45          # flatten any open position at 23:45 MSK (before session close)


def _hm_day(t: int):
    """(minutes-since-midnight MSK, YYYYMMDD) for a bar epoch."""
    d = datetime.fromtimestamp(t, tz=timezone.utc)   # bars are MSK stamped as UTC
    return d.hour * 60 + d.minute, d.year * 10000 + d.month * 100 + d.day


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"US-Open FVG/Retest started | open={params.get('open_hour',16)}:{params.get('open_min',30)} "
            f"range={params.get('range_min',5)}m mode={params.get('entry_mode',1)} "
            f"rr={float(params.get('rr_x10',20))/10:.1f}:1 symbol={params.get('symbol')}")


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    open_hm = int(params.get("open_hour", 16)) * 60 + int(params.get("open_min", 30))
    range_min = int(params.get("range_min", 5))
    signal_min = int(params.get("signal_min", 60))
    entry_mode = int(params.get("entry_mode", 1))          # 0=FVG 1=retest 2=either
    min_frac = float(params.get("min_frac", 5)) / 10000.0
    req_fvg = int(params.get("req_fvg", 1))
    rej_frac = float(params.get("rej_frac", 50)) / 100.0   # body/range for the rejection bar
    stop_pct = float(params.get("stop_pct", 0))            # stop offset, % of range height, signed
    rr = float(params.get("rr_x10", 20)) / 10.0
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))
    flatten_eod = int(params.get("flatten_eod", 1))

    need = max(5, range_min + signal_min + 10)
    bars = await stl.get_bars(symbol, tf=1, n=need)
    if len(bars) < 3:
        return
    cur = bars[-1]
    hm, day = _hm_day(cur.time)

    if stl.get_state("day") != day:                        # new trading day -> reset
        stl.set_state("day", day)
        for k in ("rh", "rl"):
            stl.set_state(k, None)
        for k in ("entered", "done", "dir", "broke_dir"):
            stl.set_state(k, 0)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)

    # 1) Manage an open position: take-profit / stop-loss / end-of-day flatten.
    if cur_qty != 0 and stl.get_state("entered"):
        sl = stl.get_state("sl")
        tp = stl.get_state("tp")
        dirn = stl.get_state("dir")
        if dirn > 0:
            if cur.low <= sl:
                await stl.place_order(symbol, "sell", abs(cur_qty), sl)
                stl.set_state("done", 1)
                return
            if cur.high >= tp:
                await stl.place_order(symbol, "sell", abs(cur_qty), tp)
                stl.set_state("done", 1)
                return
        else:
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

    if stl.get_state("done") or stl.get_state("entered"):  # already traded today
        return

    # 2) Build the opening range once, right after it closes.
    rh = stl.get_state("rh")
    rl = stl.get_state("rl")
    if rh is None:
        if hm < open_hm + range_min:
            return
        win = []
        for b in bars:
            bhm, bday = _hm_day(b.time)
            if bday == day and open_hm <= bhm < open_hm + range_min:
                win.append(b)
        if not win:
            return
        rh = max(b.high for b in win)
        rl = min(b.low for b in win)
        stl.set_state("rh", rh)
        stl.set_state("rl", rl)

    # 3) Entry only inside the signal window after the range.
    if hm < open_hm + range_min or hm > open_hm + range_min + signal_min:
        return
    height = rh - rl
    if height <= 0:
        return

    async def enter(dirn: int, price: float) -> bool:
        if dirn > 0:
            sl = rl - (stop_pct / 100.0) * height
            risk = price - sl
            if risk <= 0:
                return False
            tp = price + rr * risk
            await stl.place_order(symbol, "buy", qty, price)
        else:
            sl = rh + (stop_pct / 100.0) * height
            risk = sl - price
            if risk <= 0:
                return False
            tp = price - rr * risk
            await stl.place_order(symbol, "sell", qty, price)
        stl.set_state("entered", 1)
        stl.set_state("dir", dirn)
        stl.set_state("sl", sl)
        stl.set_state("tp", tp)
        stl.log(f"{'LONG' if dirn > 0 else 'SHORT'} {price:.0f} SL={sl:.0f} TP={tp:.0f} "
                f"(range {rl:.0f}-{rh:.0f})")
        return True

    body = (cur.close - cur.open) / cur.close if cur.close else 0.0
    rng = cur.high - cur.low

    # --- FVG breakout mode (0 or 2) ---
    if entry_mode in (0, 2):
        bull_fvg = bars[-1].low > bars[-3].high
        bear_fvg = bars[-1].high < bars[-3].low
        if allow_long and cur.close > rh and (not req_fvg or (bull_fvg and body >= min_frac)):
            if await enter(1, cur.close):
                return
        if allow_short and cur.close < rl and (not req_fvg or (bear_fvg and -body >= min_frac)):
            if await enter(-1, cur.close):
                return

    # --- Retest + rejection mode (1 or 2): the video's false-breakout filter ---
    if entry_mode in (1, 2):
        bd = int(stl.get_state("broke_dir", 0) or 0)
        if bd == 0:                                        # stage 1: record the breakout close
            if allow_long and cur.close > rh:
                stl.set_state("broke_dir", 1)
            elif allow_short and cur.close < rl:
                stl.set_state("broke_dir", -1)
            return
        rej = rng > 0 and abs(cur.close - cur.open) / rng >= rej_frac   # "shaved" bar
        if bd > 0 and cur.low <= rh and cur.close > rh and cur.close > cur.open and rej:
            await enter(1, cur.close)
            return
        if bd < 0 and cur.high >= rl and cur.close < rl and cur.close < cur.open and rej:
            await enter(-1, cur.close)
            return


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("US-Open FVG/Retest stopped")


STRATEGY_META = {
    "name": "US-Open Range + FVG/Retest",
    "description": (
        "ICT opening-range with FVG or retest confirmation. One trade per day off the US open."
    ),
    "source": "ICT Silver Bullet + Влад Дудукчанов «Свеча 9:30»",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "range_min", "label": "Мин. опорной свечи", "type": "number", "default": 5, "min": 3, "max": 30,
         "hint": "Длина опорной свечи после 16:30 МСК (видео: 5). Хай/лоу = диапазон дня"},
        {"key": "signal_min", "label": "Окно входа (мин)", "type": "number", "default": 60, "min": 15, "max": 180,
         "hint": "Сколько минут после закрытия диапазона разрешён вход"},
        {"key": "entry_mode", "label": "Режим входа 0/1/2", "type": "number", "default": 1, "min": 0, "max": 2,
         "hint": "0=FVG+пробой, 1=ретест+отвержение (видео), 2=любой"},
        {"key": "rr_x10", "label": "R:R ×10 (20=2:1)", "type": "number", "default": 20, "min": 5, "max": 50,
         "hint": "Тейк = R:R × риск. 20 = цель 2:1 к стопу"},
        {"key": "stop_pct", "label": "Стоп % от диапазона", "type": "number", "default": 0, "min": -100, "max": 100,
         "hint": "Расстояние стопа = знак% высоты опорной свечи от края. 0=на краю, >0 шире (за диапазон), <0 туже (внутрь), -100=у противоположного края"},
        {"key": "rej_frac", "label": "Тело свечи-отвержения %", "type": "number", "default": 50, "min": 0, "max": 100,
         "hint": "Ретест-режим: мин. тело/размах бара отвержения (50 = бритый наполовину)"},
        {"key": "min_frac", "label": "Мин. тело FVG ×10000", "type": "number", "default": 5, "min": 0, "max": 50,
         "hint": "FVG-режим: порог тела подтверждающей свечи (5 = 0.05%)"},
        {"key": "req_fvg", "label": "Требовать FVG (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "FVG-режим: 1 = нужен FVG в сторону пробоя, 0 = только пробой"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить входы в лонг"},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить входы в шорт"},
        {"key": "flatten_eod", "label": "Флэт в конце дня (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "1 = закрыть по рынку в 23:45 МСК, если TP/SL не сработали"},
        {"key": "qty", "label": "Контрактов", "type": "number", "default": 1, "min": 1, "max": 10, "hint": "Лотность"},
    ],
}
