"""Strategy introspection for the showcase UI.

Produces a JSON-able dict describing the robot's CURRENT decision state: the
computed features, the desired position (want), a human "waiting for" line and
the orders the robot PLANS to fire when the signal confirms. The FVG explainer
mirrors sig_fvg in trader/lab/strategies/library.py EXACTLY (same formulas) —
it explains the real code, it does not approximate it.
"""

from trader.lab import indicators as I
from trader.lab.strategies.library import REGISTRY


def _fvg_explain(bars, params: dict) -> dict:
    """Mirror of sig_fvg: bullish gap = low[i] > high[i-2]; bearish = high[i] <
    low[i-2]; confirmation = |body|/close >= min_frac/10000 in gap direction."""
    if len(bars) < 3:
        return {"ready": False, "waiting_for": f"накопление баров: {len(bars)}/3"}
    b2, b0 = bars[-3], bars[-1]  # i-2 and i
    close = b0.close or 1.0
    body = (b0.close - b0.open) / close
    min_frac = float(params.get("min_frac", 5)) / 10000.0
    gap_up = b0.low > b2.high
    gap_dn = b0.high < b2.low
    want = None
    if gap_up and body >= min_frac:
        want = 1
    elif gap_dn and -body >= min_frac:
        want = -1

    if want == 1:
        waiting = "СИГНАЛ ЛОНГ: бычий FVG подтверждён телом"
    elif want == -1:
        waiting = "СИГНАЛ ШОРТ: медвежий FVG подтверждён телом"
    elif gap_up:
        waiting = (f"бычий разрыв есть (low {b0.low:.0f} > high[i-2] {b2.high:.0f}), "
                   f"жду подтверждения телом: {body*10000:.1f} < {min_frac*10000:.0f} (×10⁴)")
    elif gap_dn:
        waiting = (f"медвежий разрыв есть (high {b0.high:.0f} < low[i-2] {b2.low:.0f}), "
                   f"жду подтверждения телом: {-body*10000:.1f} < {min_frac*10000:.0f} (×10⁴)")
    else:
        waiting = (f"жду 3-барный разрыв: нужно low > {b2.high:.0f} (бычий) "
                   f"или high < {b2.low:.0f} (медвежий); сейчас low {b0.low:.0f} / high {b0.high:.0f}")

    return {
        "ready": True,
        "want": want,
        "waiting_for": waiting,
        "features": {
            "gap_up": gap_up,
            "gap_dn": gap_dn,
            "body_x10000": round(body * 10000, 2),
            "min_frac_x10000": round(min_frac * 10000, 2),
            "bar_i": {"t": b0.time, "o": b0.open, "h": b0.high, "l": b0.low, "c": b0.close},
            "bar_i2": {"t": b2.time, "h": b2.high, "l": b2.low},
        },
    }


_EXPLAINERS = {"fvg": _fvg_explain}


def _module_explain(strategy_id: str, bars, params: dict, state: dict) -> dict:
    """Standalone-module strategy (us_open_fvg, donchian_breakout, ...): there is no
    REGISTRY signal function to re-run read-only, but the module keeps its LIVE
    decision state (entry/sl/tp/dir/entered/done) in the runtime — those are the
    EXACT levels the robot will exit on, so report them instead of the old scary
    «стратегия X не найдена» (operator, 2026-07-24: «непонятно и тревожно… надо
    писать что именно ждёт стратегия — когда сработает TP и SL»)."""
    sl = state.get("sl")
    tp = state.get("tp")
    dirn = state.get("dir") or 0
    entry = state.get("entry")
    out: dict = {"ready": True, "want": None, "module_strategy": True,
                 "levels_from_state": True}
    if state.get("done"):
        out["waiting_for"] = ("сделка дня уже сделана — до конца сессии робот не входит "
                              "(стратегия «одна сделка в день»)")
    elif state.get("entered") and sl and tp:
        side = "лонг" if dirn > 0 else "шорт"
        out["waiting_for"] = (
            f"в позиции ({side} от {entry:.0f}): выход по TP {tp:.0f} или по SL {sl:.0f} — "
            f"робот сам закроет по рынку, когда бар их коснётся")
        out["exit_levels"] = {"tp": round(float(tp), 2), "sl": round(float(sl), 2),
                              "entry": round(float(entry or 0), 2), "dir": int(dirn)}
    elif state.get("rh") and state.get("rl"):
        out["waiting_for"] = (
            f"диапазон открытия построен: {state['rl']:.0f}—{state['rh']:.0f}; "
            f"жду пробой и возврат (FVG) для входа")
        out["range"] = {"hi": round(float(state["rh"]), 2), "lo": round(float(state["rl"]), 2)}
    else:
        out["waiting_for"] = ("жду окно открытия США: строится диапазон первых "
                              f"{params.get('range_min', 6)} минут, вход — только после него")
    return out


def _generic_explain(strategy_id: str, bars, params: dict, state: dict) -> dict:
    """Fallback for strategies without a dedicated explainer: run the registered
    signal function read-only and report the desired position."""
    spec = REGISTRY.get(strategy_id)
    if spec is None:
        return _module_explain(strategy_id, bars, params, state)
    # Merge registry defaults under the robot's params — warmup/signal expect the
    # full schema, а робот может нести только переопределённые ключи.
    params = {**spec["default_params"], **params}
    need = spec["warmup"](params)
    if len(bars) < need:
        return {"ready": False, "waiting_for": f"накопление баров: {len(bars)}/{need}"}
    # ОКНО, А НЕ ВЕСЬ ХВОСТ. make_on_bar зовёт signal() ровно на `need` последних барах;
    # если показать сигнал по всем 600, консоль покажет ПЕРЕВОРОТ там, где трейдер его
    # не видит. Так и было у macd_cross fast=57/slow=48: монитор писал «СИГНАЛ ШОРТ», а
    # робот в ту же секунду покупал (lxk22tsffsxiiotb8kmpsato, 6 суток, 0 шортов).
    atr_n = int(params.get("avg_atr_n", 14) or 14)
    if float(params.get("avg_step_atr", 0) or 0) > 0 or float(params.get("tp_atr", 0) or 0) > 0:
        need = max(need, atr_n + 1)
    if len(bars) < need:
        return {"ready": False, "waiting_for": f"накопление баров: {len(bars)}/{need}"}
    # «Долина смерти» серию НЕ трогает: сигнал всегда по сырому окну (долина
    # гейтит только входы — dv_block ниже; замороженный сигнал в долине лишал
    # робота сигнального ВЫХОДА, жив. lxk22 08.08.2026: шорт −290 пт при want=None).
    try:
        want = spec["signal"](bars[-need:], params)
    except Exception as exc:  # noqa: BLE001 — introspection must never crash the host
        return {"ready": False, "waiting_for": f"ошибка сигнала: {exc}"}
    label = {1: "СИГНАЛ ЛОНГ", -1: "СИГНАЛ ШОРТ", 0: "сигнал: выйти в кэш"}.get(want)
    return {"ready": True, "want": want,
            "waiting_for": label or "сигнала нет — держу текущее состояние"}


def planned_orders(want, position: int, price: float, params: dict) -> list[dict]:
    """Orders the robot WOULD place right now given want vs current position —
    mirrors make_on_bar's close-then-open logic (flip closes first)."""
    if price <= 0 or want is None:
        return []
    qty = max(1, int(params.get("qty", 1)))
    out: list[dict] = []
    if position != 0 and (want == 0 or (want > 0) != (position > 0)):
        out.append({"side": "sell" if position > 0 else "buy", "qty": abs(position),
                    "price": price, "reason": "закрытие позиции (смена/снятие сигнала)"})
        if want != 0:
            out.append({"side": "buy" if want > 0 else "sell", "qty": qty,
                        "price": price, "reason": "открытие по новому сигналу",
                        "entry": True})
    elif position == 0 and want in (1, -1):
        out.append({"side": "buy" if want > 0 else "sell", "qty": qty,
                    "price": price, "reason": "вход по сигналу", "entry": True})
    return out


def entry_block(price: float, params: dict, state: dict, bar_time: int) -> str:
    """Почему вход НЕ пройдёт прямо сейчас: разножка или остывание. Зеркало
    гейтов make_on_bar (gap_ok / in_cooldown) — оба стоят только на НАБОРЕ
    объёма, выходы и тейк они не трогают. Без этого карточка показывала
    «вход по сигналу» ровно в тот час, когда фильтр вход не пускал, и молчание
    робота выглядело поломкой (живой MACD·RIU6, 29.07.2026)."""
    min_gap = float(params.get("min_gap_pts", 0) or 0)
    gap_ref = float(state.get("gap_ref", 0) or 0)
    if min_gap > 0 and gap_ref > 0 and price > 0 and abs(price - gap_ref) < min_gap:
        return (f"разножка держит: {abs(price - gap_ref):.0f} из {min_gap:.0f} пт "
                f"от прошлого входа {gap_ref:.0f}")
    cd_min = int(params.get("cooldown_min", 0) or 0)
    until = int(state.get("cooldown_until", 0) or 0)
    if cd_min > 0 and bar_time and bar_time < until:
        return f"остывание держит: ещё {(until - bar_time) // 60 + 1} мин"
    return ""


def dv_block(params: dict, bars) -> str:
    """«Долина смерти» держит вход прямо сейчас? Зеркало гейта in_dv make_on_bar.
    В отличие от разножки/остывания долина гейтит и вход с нуля, и обратную ногу
    разворота, и доборы — поэтому карточка помечает ВСЕ entry-заявки, не только
    уровни доборов."""
    dv_win = int(params.get("dv_bars", 0) or 0)
    dv_pts = float(params.get("dv_range_pts", 0) or 0)
    if dv_win <= 0 or dv_pts <= 0 or not bars or len(bars) < dv_win:
        return ""
    closes = [b.close for b in bars[-dv_win:]]
    rng = max(closes) - min(closes)
    if rng < dv_pts:
        return (f"долина смерти: коридор закрытий {rng:.0f} пт за {dv_win} баров "
                f"(< {dv_pts:.0f}) — новые входы закрыты до пробоя")
    return ""


def management_levels(bars, params: dict, position: int, avg: float) -> list[dict]:
    """PRICE-LEVEL orders the position-management layer is watching — the exact
    mirror of make_on_bar part 3 (take-profit + ATR averaging). These are real
    levels (unlike signal entries), so the UI draws them as chart lines; they
    move as ATR changes per bar — the visible \"перестановка\"."""
    if position == 0 or avg <= 0 or not bars:
        return []
    tp = float(params.get("tp_atr", 0) or 0) / 10.0
    k_step = float(params.get("avg_step_atr", 0) or 0) / 10.0
    avg_max = max(1, int(params.get("avg_max", 1) or 1))
    atr_n = int(params.get("avg_atr_n", 14) or 14)
    if not (tp > 0 or (k_step > 0 and abs(position) < avg_max)):
        return []
    if len(bars) < atr_n + 1:
        return []
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    atrv = I.atr(h, lo, c, atr_n)
    if atrv <= 0:
        return []
    cur_dir = 1 if position > 0 else -1
    out: list[dict] = []
    if tp > 0:
        level = avg + tp * atrv if cur_dir > 0 else avg - tp * atrv
        out.append({"side": "sell" if cur_dir > 0 else "buy", "qty": abs(position),
                    "price": round(level, 2), "level": True,
                    "reason": f"тейк-профит: avg {avg:.0f} {'+' if cur_dir > 0 else '−'} "
                              f"{tp:g}×ATR({atr_n})={atrv:.0f}"})
    if k_step > 0 and abs(position) < avg_max:
        level = avg - k_step * atrv if cur_dir > 0 else avg + k_step * atrv
        qty = max(1, int(params.get("qty", 1)))
        add = min(qty, avg_max - abs(position))
        out.append({"side": "buy" if cur_dir > 0 else "sell", "qty": add,
                    "price": round(level, 2), "level": True, "entry": True,
                    "reason": f"усреднение: avg {'−' if cur_dir > 0 else '+'} "
                              f"{k_step:g}×ATR({atr_n})={atrv:.0f}"})
    return out


def explain(strategy_id: str, bars, params: dict, position: int,
            avg: float = 0.0, state: dict | None = None) -> dict:
    """Full introspection blob for RobotStatus.signal_json. `state` is the
    strategy's own live state dict (STLRuntime get_state/set_state) — the only
    place a standalone module keeps its real exit levels."""
    state = state or {}
    fn = _EXPLAINERS.get(strategy_id)
    d = fn(bars, params) if fn else _generic_explain(strategy_id, bars, params, state)
    d["strategy_id"] = strategy_id
    d["bars_count"] = len(bars)
    d["position"] = position
    price = bars[-1].close if bars else 0.0
    d["last_close"] = price
    # Current ATR (points) over avg_atr_n M1 bars, ALWAYS present (even when flat)
    # so the parameter editor can show live "×ATR = N пунктов" conversions.
    atr_n = int(params.get("avg_atr_n", 14) or 14)
    if len(bars) >= atr_n + 1:
        try:
            d["atr"] = round(I.atr([b.high for b in bars], [b.low for b in bars],
                                   [b.close for b in bars], atr_n), 2)
        except Exception:
            d["atr"] = 0.0
    else:
        d["atr"] = 0.0
    # What fires on the NEXT confirming signal: if a signal is live now, the
    # actual orders; otherwise the hypothetical entry orders for either side.
    want = d.get("want")
    if want is not None:
        d["planned_orders"] = planned_orders(want, position, price, params)
    else:
        d["planned_orders"] = []
        if price > 0 and d.get("ready"):
            qty = max(1, int(params.get("qty", 1)))
            d["armed"] = [
                {"side": "buy", "qty": qty, "price": price,
                 "reason": "если подтвердится бычий сигнал"},
                {"side": "sell", "qty": qty, "price": price,
                 "reason": "если подтвердится медвежий сигнал"},
            ]
    # Position-management PRICE LEVELS (TP / averaging) — drawn as chart lines.
    d["planned_orders"] = d.get("planned_orders", []) + management_levels(
        bars, params, position, avg)
    # Гейты входа: план, который фильтр прямо сейчас не пустит, помечаем — иначе
    # карточка обещает заявку, которой не будет.
    dvb = dv_block(params, bars)
    if dvb:
        d["entry_blocked"] = dvb
        for o in d["planned_orders"]:
            # Долина гейтит ЛЮБОЙ вход (с нуля, разворот, добор) — метим все entry.
            if o.get("entry"):
                o["blocked"] = dvb
        for o in d.get("armed", []):
            o["reason"] += " (долина смерти держит)"
    blk = entry_block(price, params, state, int(bars[-1].time) if bars else 0)
    if blk and not dvb:
        d["entry_blocked"] = blk
        for o in d["planned_orders"]:
            # ТОЛЬКО ДОБОРЫ. Разножка и остывание стоят в make_on_bar исключительно
            # на ветке усреднения (gap_ok там и только там); вход по сигналу и
            # разворот они не трогают с 31.07.2026. Помечая любой entry, карточка
            # объявляла заблокированным разворот в шорт — и при разборе «почему
            # робот не шортит» это уводило прямо в ложный след (05.08.2026).
            # Доборы отличает level=true: у них есть ценовой уровень.
            if o.get("entry") and o.get("level"):
                o["blocked"] = blk
    return d
