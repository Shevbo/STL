"""Strategy introspection for the showcase UI.

Produces a JSON-able dict describing the robot's CURRENT decision state: the
computed features, the desired position (want), a human "waiting for" line and
the orders the robot PLANS to fire when the signal confirms. The FVG explainer
mirrors sig_fvg in trader/lab/strategies/library.py EXACTLY (same formulas) —
it explains the real code, it does not approximate it.
"""

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


def _generic_explain(strategy_id: str, bars, params: dict) -> dict:
    """Fallback for strategies without a dedicated explainer: run the registered
    signal function read-only and report the desired position."""
    spec = REGISTRY.get(strategy_id)
    if spec is None:
        return {"ready": False, "waiting_for": f"стратегия {strategy_id} не найдена"}
    # Merge registry defaults under the robot's params — warmup/signal expect the
    # full schema, а робот может нести только переопределённые ключи.
    params = {**spec["default_params"], **params}
    need = spec["warmup"](params)
    if len(bars) < need:
        return {"ready": False, "waiting_for": f"накопление баров: {len(bars)}/{need}"}
    try:
        want = spec["signal"](bars, params)
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
                        "price": price, "reason": "открытие по новому сигналу"})
    elif position == 0 and want in (1, -1):
        out.append({"side": "buy" if want > 0 else "sell", "qty": qty,
                    "price": price, "reason": "вход по сигналу"})
    return out


def explain(strategy_id: str, bars, params: dict, position: int) -> dict:
    """Full introspection blob for RobotStatus.signal_json."""
    fn = _EXPLAINERS.get(strategy_id)
    d = fn(bars, params) if fn else _generic_explain(strategy_id, bars, params)
    d["strategy_id"] = strategy_id
    d["bars_count"] = len(bars)
    d["position"] = position
    price = bars[-1].close if bars else 0.0
    d["last_close"] = price
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
    return d
