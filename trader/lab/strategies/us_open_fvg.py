"""
US-Open Opening Range + FVG / Retest (ICT "Silver Bullet" + Влад Дудукчанов "Свеча 9:30").

One trade per day, anchored to the US equity open (09:30 New York — 16:30 MSK under
US daylight saving ~Mar-Nov, 17:30 MSK in winter; open_hour/open_min params):
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


def _hm_day(t: int, offset_min: int = 0):
    """(minutes-since-midnight MSK, YYYYMMDD) for a bar epoch.

    Backtest/ISS bars are MSK-wall stamped as UTC -> offset 0 (default, historic
    behaviour). The AGENT RUNNER builds TRUE-UTC bars from the QUIK tape, so a
    deploy there must pass bar_offset_min=180 to recover MSK wall time — with 0
    the strategy would anchor the "16:30" range at 19:30 MSK and the EOD flatten
    at 02:45. Infrastructure param, deliberately NOT in params_schema (never a
    sweep axis)."""
    d = datetime.fromtimestamp(t + offset_min * 60, tz=timezone.utc)
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
    tp_trail = int(params.get("tp_trail", 0))               # 0=fixed TP, 1=trailing
    trail_act = float(params.get("trail_act", 100)) / 100.0  # activate after this profit (×H)
    trail_dist = float(params.get("trail_dist", 50)) / 100.0  # trail distance behind peak (×H)
    trail_slip = float(params.get("trail_slip", 0)) / 100.0   # slippage on the trail exit (×H)

    need = max(5, range_min + signal_min + 10)
    bars = await stl.get_bars(symbol, tf=1, n=need)
    if len(bars) < 3:
        return
    cur = bars[-1]
    bar_off = int(params.get("bar_offset_min", 0))   # 180 on the agent runner (true-UTC bars)
    hm, day = _hm_day(cur.time, bar_off)

    if stl.get_state("day") != day:                        # new trading day -> reset
        stl.set_state("day", day)
        for k in ("rh", "rl"):
            stl.set_state(k, None)
        for k in ("entered", "done", "dir", "broke_dir"):
            stl.set_state(k, 0)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)

    # Safety: a position that survived into a new day (its `entered` flag was cleared by the
    # reset) must be CLOSED, never added to by a fresh entry below — otherwise it accumulates
    # day over day into a runaway position + P&L. This is the belt for a missed EOD flatten.
    if cur_qty != 0 and not stl.get_state("entered"):
        await stl.place_order(symbol, "sell" if cur_qty > 0 else "buy", abs(cur_qty), cur.close)
        stl.set_state("done", 1)
        return

    # 1) Manage an open position: fixed take-profit OR a trailing stop, plus stop-loss and
    #    an end-of-day flatten. tp_trail=1 drops the fixed TP and rides a stop that trails
    #    trail_dist×H behind the best price once profit reaches trail_act×H (slippage
    #    trail_slip×H budgeted on the exit fill).
    if cur_qty != 0 and stl.get_state("entered"):
        sl = stl.get_state("sl")
        tp = stl.get_state("tp")
        dirn = stl.get_state("dir")
        entry = stl.get_state("entry")
        height = (stl.get_state("rh") or 0) - (stl.get_state("rl") or 0)
        exit_px = None
        if dirn > 0:
            if tp_trail and height > 0:
                peak = max(stl.get_state("peak", entry), cur.high)
                stl.set_state("peak", peak)
                trail = peak - trail_dist * height if (peak - entry) >= trail_act * height else None
                eff_stop = max(sl, trail) if trail is not None else sl
                if cur.low <= eff_stop:
                    exit_px = eff_stop - trail_slip * height
            else:
                if cur.low <= sl:
                    exit_px = sl
                elif cur.high >= tp:
                    exit_px = tp
            if exit_px is not None:
                await stl.place_order(symbol, "sell", abs(cur_qty), exit_px)
                stl.set_state("done", 1)
                return
        else:
            if tp_trail and height > 0:
                trough = min(stl.get_state("peak", entry), cur.low)
                stl.set_state("peak", trough)
                trail = trough + trail_dist * height if (entry - trough) >= trail_act * height else None
                eff_stop = min(sl, trail) if trail is not None else sl
                if cur.high >= eff_stop:
                    exit_px = eff_stop + trail_slip * height
            else:
                if cur.high >= sl:
                    exit_px = sl
                elif cur.low <= tp:
                    exit_px = tp
            if exit_px is not None:
                await stl.place_order(symbol, "buy", abs(cur_qty), exit_px)
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
            bhm, bday = _hm_day(b.time, bar_off)
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
        stl.set_state("entry", price)
        stl.set_state("peak", price)
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
        "ICT opening-range with FVG or retest confirmation. One trade per day off the US open. "
        "Открытие США = 09:30 Нью-Йорк ВСЕГДА; в МСК это 16:30 при американском летнем времени "
        "(~март–ноябрь) и 17:30 зимой — при переводе часов в США переключите open_hour."
    ),
    "source": "ICT Silver Bullet + Влад Дудукчанов «Свеча 9:30»",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "open_hour", "label": "Час открытия США (МСК)", "type": "number", "default": 16, "min": 9, "max": 23,
         "hint": "09:30 Нью-Йорк = 16 МСК при летнем времени США (~март–ноябрь), 17 зимой. Переключать при переводе часов в США"},
        {"key": "open_min", "label": "Минута открытия", "type": "number", "default": 30, "min": 0, "max": 59,
         "hint": "Минута открытия США (обычно 30)"},
        {"key": "range_min", "label": "Мин. опорной свечи", "type": "number", "default": 5, "min": 3, "max": 30,
         "hint": "Длина опорной свечи после открытия США (видео: 5). Хай/лоу = диапазон дня"},
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
        {"key": "tp_trail", "label": "Трейлинг TP (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "0 = фикс TP (R:R). 1 = трейлинг-стоп вместо фикс TP (профит едет за ценой)"},
        {"key": "trail_act", "label": "Активация трейла % H", "type": "number", "default": 100, "min": 0, "max": 500,
         "hint": "Трейл включается после профита ≥ этот % высоты диапазона (100 = 1 диапазон)"},
        {"key": "trail_dist", "label": "Дистанция трейла % H", "type": "number", "default": 50, "min": 5, "max": 300,
         "hint": "Стоп тянется на этот % высоты диапазона за пиком цены"},
        {"key": "trail_slip", "label": "Проскальзывание % H", "type": "number", "default": 0, "min": 0, "max": 50,
         "hint": "Защита от проскальзывания: exit-филл трейла хуже на этот % высоты диапазона"},
        {"key": "flatten_eod", "label": "Флэт в конце дня (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "1 = закрыть по рынку в 23:45 МСК, если TP/SL не сработали"},
        {"key": "qty", "label": "Контрактов", "type": "number", "default": 1, "min": 1, "max": 10, "hint": "Лотность"},
    ],
}
