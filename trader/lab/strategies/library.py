"""
Strategy library — a registry of classic indicator-based robots.

These patterns are exactly what the 1000+ Pine/Lua/StockSharp scripts in the
public sources implement (MACD, Bollinger, Stochastic, CCI, Keltner, ...).
Each robot is a SIGNAL function: given recent bars + params it returns the
DESIRED signed position (+1 long / -1 short / 0 flat / None = hold).
A shared `make_on_bar` turns any signal into a STL on_bar that places orders.

Combined with the optimizer's parameter sweep, this registry yields hundreds of
thousands of concrete robot variants from a compact, auditable code base.
"""
from __future__ import annotations

from functools import lru_cache

from trader.lab import indicators as I
from trader.lab.runtime import STLRuntime

# registry: id -> dict(name, source, params_schema, signal, warmup, default_params)
REGISTRY: dict[str, dict] = {}


def register(rid, name, source, params_schema, signal, warmup, avg=None):
    # Append the shared position-management (averaging/TP) params to EVERY strategy
    # so the optimizer can explore them on any robot. Defaults = off (no behaviour
    # change). A robot may pass its own `avg` set (e.g. an "M1" variant that forces
    # averaging on). AVG_PARAMS is defined below, before any register() call runs.
    schema = list(params_schema) + (avg if avg is not None else AVG_PARAMS)
    REGISTRY[rid] = {
        "id": rid, "name": name, "source": source,
        "params_schema": schema, "signal": signal, "warmup": warmup,
        "default_params": {p["key"]: p["default"] for p in schema},
    }


def make_on_bar(rid: str):
    """Build a STL on_bar(stl, params) from a registered signal function.

    Суффикс `__inv` = КОНТР-стратегия: та же логика, но сигнал инвертирован (где
    база покупает — контра продаёт). Нужен, потому что на некоторых контрактах
    выгоднее ФЕЙДИТЬ сигнал, чем следовать ему (RIU6: macd_cross__inv медиана
    +193k против −75k у прямой). Раньше инверсия жила только на i9 (без git), и
    контр-лидеры не открывались в витрине и не перебирались оптимизатором —
    теперь `make_on_bar('<id>__inv')` работает везде одинаково."""
    inv = rid.endswith("__inv")
    base_rid = rid[:-len("__inv")] if inv else rid
    spec = REGISTRY[base_rid]
    signal = spec["signal"]
    warmup = spec["warmup"]

    async def on_bar(stl: STLRuntime, params: dict) -> None:
        symbol = params["symbol"]
        base_unit = max(1, int(params.get("qty", 1)))
        # Betting system (+N after a loss, reset on a win): the entry size grows by
        # bet_step contracts after each losing CLOSED trade and resets to base after a
        # win (sequence 1,2,3,...). bet_step=0 → off. Capped at bet_max extra.
        bet_step = int(params.get("bet_step", 0) or 0)
        bet_max = int(params.get("bet_max", 10) or 10)
        bet_extra = int(stl.get_state("bet_extra", 0) or 0) if bet_step > 0 else 0
        # SuperAverage (super_y>0 AND super_z>0): after a LOSING exit, the NEXT entry's
        # qty AND avg_max both grow by super_y contracts; up to super_z escalations;
        # reset to base after a WINNING exit. Independent of the betting system (they
        # compose additively). Off by default -> identical behaviour to before.
        super_y = int(params.get("super_y", 0) or 0)
        super_z = int(params.get("super_z", 0) or 0)
        super_on = super_y > 0 and super_z > 0
        super_lvl = int(stl.get_state("super_level", 0) or 0) if super_on else 0
        super_add = super_lvl * super_y
        # Cooldown (cooldown_min>0): after an exit that booked >= cooldown_pct price
        # move (a real winner), block NEW entries for cooldown_min minutes; and in this
        # mode a signal flip becomes EXIT-ONLY (close to flat, re-evaluate after the
        # pause) instead of reversing straight into the opposite side.
        cooldown_min = int(params.get("cooldown_min", 0) or 0)
        cooldown_frac = float(params.get("cooldown_pct", 1.0) or 1.0) / 100.0
        # «Разножка» (min_gap_pts>0): не открывать и не доливать, пока цена не отошла
        # от ПРОШЛОГО ВХОДА минимум на N пунктов цены. В боковике робот иначе крутит
        # вход-выход по 20-50 пунктов, отдавая комиссию и спред (живой MACD·RIU6,
        # 2026-07-22: 200 сделок за день внутри коридора 86 700-87 200). Гейт стоит
        # ТОЛЬКО на добавлении объёма; выходы (разворотное закрытие, TP) не трогает
        # никогда — иначе убыточная позиция осталась бы без выхода.
        min_gap = float(params.get("min_gap_pts", 0) or 0)
        gap_ref = float(stl.get_state("gap_ref", 0) or 0)

        def gap_ok(p: float) -> bool:
            return min_gap <= 0 or gap_ref <= 0 or abs(p - gap_ref) >= min_gap

        def mark_entry(p: float) -> None:
            # Запоминаем ПОПЫТКУ. Точкой отсчёта она станет только когда вход реально
            # исполнится (см. подтверждение ниже): живая заявка может простоять
            # неисполненной и быть снята перед следующим баром.
            if min_gap > 0:
                stl.set_state("gap_pending", p)

        unit = base_unit + bet_extra + super_add                 # current entry size
        avg_max = max(unit, int(params.get("avg_max", 1) or 1) + super_add)  # max held
        k_step = float(params.get("avg_step_atr", 0) or 0) / 10.0  # add per k_step×ATR adverse
        tp = float(params.get("tp_atr", 0) or 0) / 10.0           # take-profit in ×ATR (0=off)
        atr_n = int(params.get("avg_atr_n", 14) or 14)
        atr_active = (k_step > 0) or (tp > 0)        # ATR only needed for averaging/TP
        need = max(warmup(params), atr_n + 1) if atr_active else warmup(params)
        bars = await stl.get_bars(symbol, tf=1, n=need)
        if len(bars) < need:
            return
        want = signal(bars, params)        # +1 / -1 / 0 / None
        if inv and want:                   # контр-стратегия: фейдим базовый сигнал
            want = -want
        price = bars[-1].close
        bar_time = bars[-1].time
        in_cooldown = cooldown_min > 0 and bar_time < int(stl.get_state("cooldown_until", 0) or 0)
        pos = await stl.get_position(symbol)
        cur = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
        avg = float(pos.avg_price)
        cur_dir = 1 if cur > 0 else (-1 if cur < 0 else 0)

        # «Разножка», подтверждение: точкой отсчёта становится только ИСПОЛНИВШИЙСЯ
        # вход. Считать отсчётом саму ЗАЯВКУ нельзя — цепочка неисполненных заявок
        # «уводит» отсчёт и пропускает добор вплотную к прошлому исполнению (живой
        # MACD·RIU6 2026-07-23: заявка 11:22 по 85240 не исполнилась, но сдвинула
        # отсчёт, и добор 11:42 прошёл в 40 пунктах от исполнения 11:14 по 85490).
        # Позиция, УШЕДШАЯ от нуля или сменившая знак = вход исполнился; сокращение
        # к нулю (выход) отсчёт не двигает, иначе развороту некуда открываться.
        if min_gap > 0:
            gap_prev_pos = int(stl.get_state("gap_pos", cur) or 0)
            gap_pending = float(stl.get_state("gap_pending", 0) or 0)
            if gap_pending > 0 and (abs(cur) > abs(gap_prev_pos) or cur * gap_prev_pos < 0):
                gap_ref = gap_pending
                stl.set_state("gap_ref", gap_pending)
                stl.set_state("gap_pending", 0)
            stl.set_state("gap_pos", cur)

        # Эффект фильтров в ПУНКТАХ. Каждый отсеянный вход записывается «фантомом» и
        # оценивается ровно через SKIP_HORIZON_SEC: сколько он принёс бы, если бы
        # состоялся. Сумма их P&L с ОБРАТНЫМ знаком = сколько фильтр сберёг (минус =
        # недозаработал). Правило одно и то же для всех — не зависит от того, как
        # сложилась реальная позиция, поэтому его легко объяснить и проверить.
        # Рубли считает UI (пункты x ₽/пункт) — стратегия про рубли не знает.
        SKIP_HORIZON_SEC = 3600

        def settle_phantoms(now_ts: int, cur_price: float) -> None:
            ph = stl.get_state("skip_phantoms", None) or []
            if not ph:
                return
            due = [e for e in ph if int(e.get("t", 0)) + SKIP_HORIZON_SEC <= now_ts]
            if not due:
                return
            pnl = sum((cur_price - float(e["p"])) * int(e["d"]) * int(e["q"]) for e in due)
            stl.set_state("filter_saved_pts",
                          float(stl.get_state("filter_saved_pts", 0) or 0) - pnl)
            stl.set_state("skip_phantoms",
                          [e for e in ph if int(e.get("t", 0)) + SKIP_HORIZON_SEC > now_ts])

        settle_phantoms(int(bar_time), price)   # дозревшие фантомы -> копилка эффекта

        def note_skip(kind: str, p: float) -> None:
            """Счётчик отсева входа фильтром (kind='gap' разножка / 'cooldown' остывание):
            копит нарастающий итог в состоянии робота + пишет событие в журнал. Порядок
            заявок не меняет — только наблюдаемость (сколько входов фильтр не пустил)."""
            key = kind + "_skips"
            n = int(stl.get_state(key, 0) or 0) + 1
            stl.set_state(key, n)
            # фантом отсеянного входа: направление = сторона, которую хотел сигнал
            d = 1 if (want or 0) > 0 else -1
            ph = list(stl.get_state("skip_phantoms", None) or [])
            if len(ph) < 200:                       # ограничитель, чтобы состояние не росло
                ph.append({"p": p, "d": d, "q": fresh_entry_size(), "t": int(bar_time)})
                stl.set_state("skip_phantoms", ph)
            what = "разножка" if kind == "gap" else "остывание"
            msg = f"{what} отсеяла вход @ {p:.0f} (всего {n})"
            # В раннере -> SKIP-событие детального лога робота (видно на карточке +
            # считается из per-robot логов); в бэктест/STL event() нет -> обычный log.
            ev = getattr(stl, "event", None)
            if callable(ev):
                ev("SKIP", msg)
            else:
                stl.log("[SKIP] " + msg)

        def on_exit(exit_price: float, entry_avg: float, direction: int) -> None:
            """Book a close: update SuperAverage escalation + the cooldown timer.
            ret>0 is a winner (resets escalation), ret<0 a loser (escalates)."""
            ret = (exit_price - entry_avg) / entry_avg * direction if entry_avg else 0.0
            if super_on:
                stl.set_state("super_level", 0 if ret > 0 else min(super_lvl + 1, super_z))
            if cooldown_min > 0 and ret >= cooldown_frac:
                stl.set_state("cooldown_until", bar_time + cooldown_min * 60)

        def fresh_entry_size() -> int:
            """Entry size using the CURRENT (post-exit) escalation + betting state."""
            lvl = int(stl.get_state("super_level", 0) or 0) if super_on else 0
            be = int(stl.get_state("bet_extra", 0) or 0) if bet_step > 0 else 0
            return base_unit + be + lvl * super_y

        # 1) Signal flip / flat → close the whole position. In cooldown mode this is
        #    EXIT-ONLY; otherwise it reverses straight into the new side.
        if cur != 0 and want is not None and (want == 0 or (want > 0) != (cur > 0)):
            if bet_step > 0:                      # closed-trade result drives the betting system
                bet_extra = min(bet_extra + bet_step, bet_max) if (price - avg) * cur_dir < 0 else 0
                stl.set_state("bet_extra", bet_extra)
            on_exit(price, avg, cur_dir)
            await stl.place_order(symbol, "sell" if cur > 0 else "buy", abs(cur), price)
            if want != 0 and cooldown_min == 0:      # cooldown mode -> exit-only, not a skip
                if gap_ok(price):
                    await stl.place_order(symbol, "buy" if want > 0 else "sell", fresh_entry_size(), price)
                    mark_entry(price)
                else:
                    note_skip("gap", price)
            return
        # 2) Flat → open a fresh base position on a signal (unless cooling down).
        if cur == 0:
            if want is not None and want != 0:
                if in_cooldown:
                    note_skip("cooldown", price)
                elif not gap_ok(price):
                    note_skip("gap", price)
                else:
                    await stl.place_order(symbol, "buy" if want > 0 else "sell", fresh_entry_size(), price)
                    mark_entry(price)
            return
        # 3) Holding (signal agrees or is None) → manage take-profit + averaging by ATR.
        if not ((tp > 0) or (k_step > 0 and abs(cur) < avg_max)):
            return
        # ATR только по ХВОСТУ, не по всему окну: pivot тянет 2200 баров ради прошлого
        # дня, а Уайлдеровский ATR экспоненциально забывает старое (вес (n-1)/n за шаг),
        # поэтому atr_n*40 баров дают значение, неотличимое от полного (проверено: net
        # бит-в-бит). Без этого atr() гонял 2200-баровый цикл на КАЖДОМ баре — 84с из 142
        # в профиле pivot с усреднением (2026-07-23). Касается ВСЕХ стратегий с avg/TP.
        _atr_tail = bars[-(atr_n * 40 + 1):]
        atrv = I.atr(_h(_atr_tail), _l(_atr_tail), _c(_atr_tail), atr_n)
        if atrv <= 0:
            return
        if tp > 0:    # take-profit measured from the (averaged) entry (a TP is a win)
            if cur_dir > 0 and price >= avg + tp * atrv:
                if bet_step > 0:
                    stl.set_state("bet_extra", 0)
                on_exit(price, avg, cur_dir)
                await stl.place_order(symbol, "sell", abs(cur), price)
                return
            if cur_dir < 0 and price <= avg - tp * atrv:
                if bet_step > 0:
                    stl.set_state("bet_extra", 0)
                on_exit(price, avg, cur_dir)
                await stl.place_order(symbol, "buy", abs(cur), price)
                return
        if k_step > 0 and abs(cur) < avg_max:   # average in: add a unit on adverse move
            add = min(unit, avg_max - abs(cur))
            if cur_dir > 0 and price <= avg - k_step * atrv:
                if gap_ok(price):
                    await stl.place_order(symbol, "buy", add, price)
                    mark_entry(price)
                else:
                    note_skip("gap", price)
                return
            if cur_dir < 0 and price >= avg + k_step * atrv:
                if gap_ok(price):
                    await stl.place_order(symbol, "sell", add, price)
                    mark_entry(price)
                else:
                    note_skip("gap", price)
                return

    return on_bar


# ── helpers for schema ────────────────────────────────────────────────────────
def P(key, label, default, lo, hi, hint=""):
    return {"key": key, "label": label, "type": "number", "default": default, "min": lo, "max": hi, "hint": hint}


SYM = {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIM6", "hint": "FORTS тикер"}
GH = "https://github.com/topics/trading-strategies"

# Position-management modifier injected into EVERY strategy (see register), so the
# optimizer can explore averaging-instead-of-SL on any robot. Defaults = OFF:
# avg_max=1 (no adds) + tp_atr=0 (exit on signal only) == original behaviour.
# *_atr params are integers stored ×10 (10 = 1.0×ATR) for the integer sweep.
AVG_PARAMS = [
    P("avg_max", "Усреднение: макс контрактов", 1, 1, 10),
    P("avg_step_atr", "Усреднение: шаг ×ATR/10 (0=выкл)", 0, 0, 30),
    P("tp_atr", "Тейк-профит ×ATR/10 (0=по сигналу)", 0, 0, 60),
    P("avg_atr_n", "ATR период (усреднение)", 14, 5, 40),
    P("min_gap_pts", "Разножка: мин. пунктов от прошлого входа (0=выкл)", 0, 0, 1000),
]
# "M1" variants: averaging FORCED on (min ≥ 2 contracts, step always > 0), so the
# modification always averages-instead-of-SL — distinct from the plain robot which
# can sweep averaging off.
AVG_PARAMS_FORCED = [
    P("avg_max", "Усреднение: макс контрактов", 5, 2, 10),
    P("avg_step_atr", "Усреднение: шаг ×ATR/10", 10, 5, 30),
    P("tp_atr", "Тейк-профит ×ATR/10 (0=по сигналу)", 0, 0, 60),
    P("avg_atr_n", "ATR период (усреднение)", 14, 5, 40),
    P("min_gap_pts", "Разножка: мин. пунктов от прошлого входа (0=выкл)", 0, 0, 1000),
]

# ════════════════════════════════════════════════════════════════════════════
#  STRATEGY SIGNALS  (closes = [b.close ...]; highs/lows similar)
# ════════════════════════════════════════════════════════════════════════════

def _c(bars): return [b.close for b in bars]
def _h(bars): return [b.high for b in bars]
def _l(bars): return [b.low for b in bars]
def _o(bars): return [b.open for b in bars]


# 1. MACD crossover (long+short)
def sig_macd(bars, p):
    closes = _c(bars)
    m, s = I.macd(closes, int(p["fast"]), int(p["slow"]), int(p["signal"]))
    return 1 if m > s else -1
register("macd_cross", "MACD Crossover",
         "https://github.com/topics/macd",
         [SYM, P("fast", "Быстрая EMA", 12, 3, 30), P("slow", "Медленная EMA", 26, 10, 60),
          P("signal", "Сигнальная", 9, 3, 30), P("qty", "Контрактов", 1, 1, 10)],
         sig_macd, lambda p: int(p["slow"]) + int(p["signal"]) + 2)


# 2. Bollinger Bands mean-reversion
def sig_bollinger_mr(bars, p):
    closes = _c(bars)
    lo, mid, up = I.bollinger(closes, int(p["period"]), float(p["mult"]) / 10)
    c = closes[-1]
    if c < lo: return 1
    if c > up: return -1
    return 0
register("bollinger_mr", "Bollinger Mean-Reversion",
         "https://github.com/topics/bollinger-bands",
         [SYM, P("period", "Период", 20, 5, 60), P("mult", "Сигма ×10", 20, 10, 40, "20=2.0σ"),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_bollinger_mr, lambda p: int(p["period"]) + 2)


# 3. Bollinger breakout (trend)
def sig_bollinger_bo(bars, p):
    closes = _c(bars)
    lo, mid, up = I.bollinger(closes, int(p["period"]), float(p["mult"]) / 10)
    c = closes[-1]
    if c > up: return 1
    if c < lo: return -1
    return None
register("bollinger_bo", "Bollinger Breakout",
         "https://github.com/topics/bollinger-bands",
         [SYM, P("period", "Период", 20, 5, 60), P("mult", "Сигма ×10", 20, 10, 40),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_bollinger_bo, lambda p: int(p["period"]) + 2)
# M1 = Bollinger Breakout с усреднением вместо стоп-лосса (модификация для прокачки).
register("bollinger_bo_m1", "Bollinger Breakout M1",
         "https://github.com/topics/bollinger-bands",
         [SYM, P("period", "Период", 20, 5, 60), P("mult", "Сигма ×10", 20, 10, 40),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_bollinger_bo, lambda p: int(p["period"]) + 2, avg=AVG_PARAMS_FORCED)


def sig_2ema(bars, p):
    # Two-EMA crossover: EMA1 above EMA2 → long, below → short. Always in market;
    # the flip logic opens/closes on the cross. (Reverse of the public DeskBot 2EMA.)
    closes = _c(bars)
    f = I.ema_last(closes, int(p["ema1"]))
    s = I.ema_last(closes, int(p["ema2"]))
    return 1 if f > s else -1
register("shectory_2ema", "Shectory-2EMA",
         "https://github.com/topics/moving-average-crossover",
         [SYM, P("ema1", "EMA1 (быстрая)", 10, 3, 60), P("ema2", "EMA2 (медленная)", 140, 20, 400),
          P("qty", "Базовый объём", 1, 1, 20),
          P("bet_step", "Система ставок +N после убытка (0=выкл)", 1, 0, 5),
          P("bet_max", "Макс добавка по ставкам", 10, 1, 30)],
         sig_2ema, lambda p: int(p["ema2"]) + 2)


# 4. Stochastic oscillator
def sig_stochastic(bars, p):
    k = I.stochastic(_h(bars), _l(bars), _c(bars), int(p["period"]))
    if k < float(p["oversold"]): return 1
    if k > float(p["overbought"]): return -1
    return 0
register("stochastic", "Stochastic Oscillator",
         "https://github.com/topics/stochastic",
         [SYM, P("period", "Период", 14, 5, 40), P("oversold", "Перепродан", 20, 5, 40),
          P("overbought", "Перекуплен", 80, 60, 95), P("qty", "Контрактов", 1, 1, 10)],
         sig_stochastic, lambda p: int(p["period"]) + 2)


# 5. CCI (Commodity Channel Index)
def sig_cci(bars, p):
    v = I.cci(_h(bars), _l(bars), _c(bars), int(p["period"]))
    th = float(p["threshold"])
    if v < -th: return 1
    if v > th: return -1
    return 0
register("cci", "CCI Reversal",
         "https://github.com/topics/cci",
         [SYM, P("period", "Период", 20, 5, 50), P("threshold", "Порог", 100, 50, 200),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_cci, lambda p: int(p["period"]) + 2)


# 6. Williams %R
def sig_williams(bars, p):
    v = I.williams_r(_h(bars), _l(bars), _c(bars), int(p["period"]))
    if v < -float(p["oversold"]): return 1
    if v > -float(p["overbought"]): return -1
    return 0
register("williams_r", "Williams %R",
         "https://github.com/topics/williams-r",
         [SYM, P("period", "Период", 14, 5, 40), P("oversold", "Перепродан", 80, 60, 95),
          P("overbought", "Перекуплен", 20, 5, 40), P("qty", "Контрактов", 1, 1, 10)],
         sig_williams, lambda p: int(p["period"]) + 2)


# 7. Momentum
def sig_momentum(bars, p):
    v = I.momentum(_c(bars), int(p["period"]))
    if v > 0: return 1
    if v < 0: return -1
    return 0
register("momentum", "Momentum",
         "https://github.com/topics/momentum-trading",
         [SYM, P("period", "Период", 10, 2, 60), P("qty", "Контрактов", 1, 1, 10)],
         sig_momentum, lambda p: int(p["period"]) + 2)


# 8. ROC threshold
def sig_roc(bars, p):
    v = I.roc(_c(bars), int(p["period"]))
    th = float(p["threshold"]) / 100
    if v > th: return 1
    if v < -th: return -1
    return 0
register("roc", "Rate of Change",
         "https://github.com/topics/trading-strategies",
         [SYM, P("period", "Период", 12, 2, 60), P("threshold", "Порог %×100", 50, 5, 300, "50=0.5%"),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_roc, lambda p: int(p["period"]) + 2)


# 9. Triple SMA (fast/mid/slow alignment)
def sig_triple_sma(bars, p):
    closes = _c(bars)
    f = I.sma(closes, int(p["fast"]))
    m = I.sma(closes, int(p["mid"]))
    s = I.sma(closes, int(p["slow"]))
    if f > m > s: return 1
    if f < m < s: return -1
    return 0
register("triple_sma", "Triple SMA Alignment",
         "https://github.com/topics/moving-average",
         [SYM, P("fast", "Быстрая SMA", 5, 2, 30), P("mid", "Средняя SMA", 20, 10, 60),
          P("slow", "Медленная SMA", 50, 30, 200), P("qty", "Контрактов", 1, 1, 10)],
         sig_triple_sma, lambda p: int(p["slow"]) + 2)


# 10. Keltner channel breakout
def sig_keltner(bars, p):
    lo, mid, up = I.keltner(_h(bars), _l(bars), _c(bars),
                            int(p["ema_period"]), int(p["atr_period"]), float(p["mult"]) / 10)
    c = _c(bars)[-1]
    if c > up: return 1
    if c < lo: return -1
    return None
register("keltner_bo", "Keltner Breakout",
         "https://github.com/topics/keltner-channel",
         [SYM, P("ema_period", "EMA период", 20, 5, 60), P("atr_period", "ATR период", 10, 5, 40),
          P("mult", "Множитель ×10", 20, 10, 40), P("qty", "Контрактов", 1, 1, 10)],
         sig_keltner, lambda p: max(int(p["ema_period"]), int(p["atr_period"])) + 2)


# 11. RSI + trend filter (RSI pullback in EMA-trend)
def sig_rsi_trend(bars, p):
    closes = _c(bars)
    r = I.rsi(closes, int(p["rsi_period"]))
    trend = closes[-1] > I.ema_last(closes, int(p["ema_period"]))
    if trend and r < float(p["oversold"]): return 1
    if not trend and r > float(p["overbought"]): return -1
    return 0
register("rsi_trend", "RSI + Trend Filter",
         "https://github.com/topics/rsi",
         [SYM, P("rsi_period", "RSI период", 14, 5, 40), P("ema_period", "EMA фильтр", 50, 20, 200),
          P("oversold", "Перепродан", 40, 20, 50), P("overbought", "Перекуплен", 60, 50, 80),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_rsi_trend, lambda p: max(int(p["rsi_period"]), int(p["ema_period"])) + 2)


# 12. Dual EMA + ATR breakout combo
def sig_ema_atr(bars, p):
    closes = _c(bars)
    fast = I.ema_last(closes, int(p["fast"]))
    slow = I.ema_last(closes, int(p["slow"]))
    a = I.atr(_h(bars), _l(bars), closes, int(p["atr_period"]))
    c = closes[-1]
    if fast > slow and c > slow + float(p["mult"]) / 10 * a: return 1
    if fast < slow and c < slow - float(p["mult"]) / 10 * a: return -1
    return 0
register("ema_atr", "EMA Trend + ATR Filter",
         "https://github.com/topics/trading-strategies",
         [SYM, P("fast", "Быстрая EMA", 9, 3, 40), P("slow", "Медленная EMA", 30, 15, 120),
          P("atr_period", "ATR период", 14, 5, 40), P("mult", "ATR множ ×10", 5, 1, 30),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_ema_atr, lambda p: int(p["slow"]) + 2)


# ════════════════════════════════════════════════════════════════════════════
#  ICT / SMART-MONEY strategies — ported from SkrimerForever/moex-trading-bot
#  (MOEX hackathon Go bot). Re-implemented here as pure OHLCV signal functions;
#  no external code is executed. Formulas match that repo's features/ict_structure.go,
#  features/pivot_points.go and the deterministic strategy.
# ════════════════════════════════════════════════════════════════════════════

# 13. Fair Value Gap (FVG) — 3-bar imbalance momentum.
#   Bullish FVG: low[i] > high[i-2]  → gap up   → лонг (импульс вверх).
#   Bearish FVG: high[i] < low[i-2]  → gap down → шорт.
#   Confirmed only if the current body moves in the gap direction by ≥ min_frac.
def sig_fvg(bars, p):
    h, lo, c, o = _h(bars), _l(bars), _c(bars), _o(bars)
    if len(c) < 3:
        return None
    min_frac = float(p.get("min_frac", 5)) / 10000.0   # ×10000: 5 = 0.05%
    body = (c[-1] - o[-1]) / c[-1] if c[-1] else 0.0
    if lo[-1] > h[-3] and body >= min_frac:
        return 1
    if h[-1] < lo[-3] and -body >= min_frac:
        return -1
    return None
register("fvg", "Fair Value Gap (ICT)",
         "https://github.com/SkrimerForever/moex-trading-bot",
         [SYM, P("min_frac", "Мин. тело ×10000 (5=0.05%)", 5, 0, 50),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_fvg, lambda p: 4)


# 14. Order Block — last counter-trend candle before a strong impulse, then retest.
#   Find an impulse in the last `lookback` bars where |body|/close ≥ impulse_frac.
#   Bullish impulse → order block = последняя медвежья свеча перед ним; лонг, когда
#   текущая цена возвращается в её зону [low, high]. Bearish — зеркально.
def sig_order_block(bars, p):
    h, lo, c, o = _h(bars), _l(bars), _c(bars), _o(bars)
    look = int(p.get("lookback", 20))
    if len(c) < look + 3:
        return None
    impulse_frac = float(p.get("impulse_frac", 30)) / 10000.0   # ×10000: 30 = 0.30%
    price = c[-1]
    # scan recent bars (excluding the very last, which is the retest bar) for an impulse
    for i in range(len(c) - 2, len(c) - look - 1, -1):
        body = (c[i] - o[i]) / c[i] if c[i] else 0.0
        if body >= impulse_frac:                       # bullish impulse
            for j in range(i - 1, max(i - look, 0) - 1, -1):
                if c[j] < o[j]:                        # last down candle = bull OB
                    if lo[j] <= price <= h[j]:
                        return 1
                    break
            break
        if -body >= impulse_frac:                      # bearish impulse
            for j in range(i - 1, max(i - look, 0) - 1, -1):
                if c[j] > o[j]:                        # last up candle = bear OB
                    if lo[j] <= price <= h[j]:
                        return -1
                    break
            break
    return None
register("order_block", "Order Block (ICT)",
         "https://github.com/SkrimerForever/moex-trading-bot",
         [SYM, P("lookback", "Окно поиска импульса", 20, 5, 60),
          P("impulse_frac", "Импульс ×10000 (30=0.3%)", 30, 5, 100),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_order_block, lambda p: int(p.get("lookback", 20)) + 3)


# 15. Pivot Points reversal — classic floor-trader pivots from the PREVIOUS day.
#   P=(H+L+C)/3, R1=2P−L, S1=2P−H, R2=P+(H−L), S2=P−(H−L) on prior session.
#   Mean-reversion: цена ≤ S1 → перепродано → лонг; цена ≥ R1 → перекуплено → шорт.
@lru_cache(maxsize=None)
def _date_of(ts: int):
    """ts -> UTC calendar date. Memoized: the pivot strategy requests a 2200-bar
    window every bar, so the same timestamps were re-converted millions of times
    (a 3-month sweep built ~400M datetime objects per combo). lru_cache makes each
    timestamp convert once. Pure function of ts, so this changes no result."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _prev_day_hlc(bars):
    """High/low/close of the calendar day before the last bar's day. Bars carry MSK
    wall-clock stamped as UTC, so the UTC date == the trading date for grouping.

    Scans backward from the end and stops once the previous trading day is fully
    collected, instead of two full passes over the whole (up to 2200-bar) window."""
    last_day = _date_of(bars[-1].time)
    i = len(bars) - 1
    while i >= 0 and _date_of(bars[i].time) == last_day:   # skip the current day
        i -= 1
    if i < 0:
        return None
    prev_day = _date_of(bars[i].time)
    # Scanning back, the first prev-day bar hit is that day's LAST bar -> its close.
    close = bars[i].close
    hi = bars[i].high
    lo = bars[i].low
    i -= 1
    while i >= 0 and _date_of(bars[i].time) == prev_day:
        b = bars[i]
        if b.high > hi:
            hi = b.high
        if b.low < lo:
            lo = b.low
        i -= 1
    return hi, lo, close

# Prev-day HLC is CONSTANT for a whole trading day, yet _prev_day_hlc rescanned up
# to two days of 1-min bars on EVERY bar: 71% of this strategy's runtime (profiled
# 2026-07-23 — 300 combos took ~1500s vs ~40s for the other strategies, and a sweep
# of it pegged the i9 for hours). Keyed by (symbol, day) it is computed once per day.
# Historical bars are immutable, so the cache is valid across combos AND runs in the
# same worker. A None (history not deep enough yet) is NEVER cached — a later bar of
# the same day may reach far enough back.
_PIVOT_HLC: dict[tuple, tuple] = {}


def sig_pivot(bars, p):
    key = (p.get("symbol", ""), _date_of(bars[-1].time))
    hlc = _PIVOT_HLC.get(key)
    if hlc is None:
        hlc = _prev_day_hlc(bars)
        if hlc is None:
            return 0
        if len(_PIVOT_HLC) > 4096:      # bounded: ~1 entry per symbol-day
            _PIVOT_HLC.clear()
        _PIVOT_HLC[key] = hlc
    ph, pl, pc = hlc
    pivot = (ph + pl + pc) / 3.0
    rng = ph - pl
    lvl = int(p.get("level", 1))                       # 1 → R1/S1, 2 → R2/S2
    r = pivot + rng if lvl == 2 else 2 * pivot - pl
    s = pivot - rng if lvl == 2 else 2 * pivot - ph
    price = bars[-1].close     # был _c(bars)[-1]: список из 2200 закрытий ради одного
    if price <= s:
        return 1
    if price >= r:
        return -1
    return 0
register("pivot_reversal", "Pivot Points Reversal",
         "https://github.com/SkrimerForever/moex-trading-bot",
         [SYM, P("level", "Уровень (1=R1/S1, 2=R2/S2)", 1, 1, 2),
          P("qty", "Контрактов", 1, 1, 10)],
         sig_pivot, lambda p: 2200)   # ≥2 FORTS days of 1-min bars → full prev-day HLC


# ── Param descriptions (по ключу) + краткое описание стратегий ──────────────────
# Generic but accurate per-parameter explanations, injected into every schema so
# each field gets an (i) tooltip. Keyed by param `key`.
PARAM_DESC: dict[str, str] = {
    "symbol": "Тикер фьючерса на FORTS (например RIM6 — фьючерс на индекс РТС, SiM6 — доллар/рубль, GZM6 — газпром). От выбора зависят стоимость одного пункта цены и гарантийное обеспечение (ГО).",
    "qty": "Количество контрактов (лотов) в одной сделке. 1 = 1 фьючерсный контракт. Робот покупает или продаёт ровно столько штук за раз. Чем больше — тем выше потенциальная прибыль, но и риск, и требуемое ГО (залог) растут пропорционально.",
    "period": "Сколько последних баров (свечей) используется для расчёта индикатора. Например, period=14 означает «смотрим на последние 14 минутных свечей». Меньше — быстрее реагирует на движение цены, но больше ложных срабатываний. Больше — сигналы надёжнее, но вход позже.",
    "fast": "Период быстрой скользящей средней. Это количество баров для расчёта короткой средней линии. Маленькое значение (3-9) — линия плотно следует за ценой, быстро разворачивается. Большое — сглаживает шум.",
    "slow": "Период медленной скользящей средней. Задаёт «несущий» тренд на большем количестве баров. Когда быстрая пересекает медленную — это сигнал разворота тренда. Чем больше slow, тем реже и крупнее сделки.",
    "mid": "Период средней скользящей средней. Используется как промежуточный фильтр: все три линии должны выстроиться по порядку (быстрая > средняя > медленная для лонга) — тогда только входим.",
    "signal": "Период сигнальной линии MACD. Это EMA от разницы быстрой и медленной EMA. Когда основная линия MACD пересекает сигнальную — это момент входа или выхода из позиции.",
    "mult": "Множитель ширины канала или порога. Хранится ×10: значение 20 означает множитель 2.0. Например, для полос Боллинджера 2.0 означает «2 стандартных отклонения». Больше множитель — полосы шире, сигналы реже, но надёжнее.",
    "threshold": "Порог срабатывания. Значение, которое должно быть превышено для подачи сигнала. Например, CCI должен быть выше +100 для продажи или ниже −100 для покупки. Чем дальше порог от нуля, тем реже, но увереннее сигналы.",
    "oversold": "Уровень перепроданности. Когда индикатор падает ниже этого числа — инструмент «слишком дёшев», ожидаем отскок вверх → сигнал на покупку (вход в лонг).",
    "overbought": "Уровень перекупленности. Когда индикатор поднимается выше этого числа — инструмент «слишком дорог», ожидаем откат вниз → сигнал на продажу (вход в шорт).",
    "rsi_period": "Период расчёта RSI (индекс относительной силы). Сколько баров используется. 14 — стандарт. Короче период — RSI резче колеблется, чаще заходит в зоны перекупленности/перепроданности, больше сделок.",
    "ema_period": "Период EMA для фильтра тренда. Если цена выше этой EMA — тренд восходящий, ищем только покупки. Если ниже — нисходящий, ищем только продажи. Длиннее период — тренд определяется устойчивее, но с задержкой.",
    "atr_period": "Сколько баров используется для расчёта ATR (среднего истинного диапазона — меры волатильности). 14 — стандарт. Влияет на ширину каналов и расстояние до тейк-профита в пунктах.",
    "avg_max": "Максимальное количество контрактов в позиции при усреднении. 1 = усреднение выключено (робот держит ровно qty контрактов). 5 = робот может докупать до 5 контрактов, если цена идёт против него — это снижает среднюю цену входа, но увеличивает риск и ГО.",
    "avg_step_atr": "Насколько цена должна уйти ПРОТИВ позиции, чтобы робот докупил ещё qty контрактов (усреднение вниз). В долях ATR, хранится ×10: 24 = 2.4×ATR. При ATR=130 пунктов это 312 пунктов против входа. 0 = усреднение выключено. Стратегия докупает ТОЛЬКО против движения; «усиление» (добор в плюс) на графике — это ярлык аналитики, а не отдельная логика.",
    "tp_atr": "Тейк-профит: на каком расстоянии от средней цены входа робот закрывает позицию в плюс. В долях ATR, хранится ×10: 60 = 6.0×ATR. При ATR=130 пунктов это тейк в 780 пунктов от средней. 0 = тейка нет, выход только по обратному сигналу.",
    "avg_atr_n": "Период ATR в БАРАХ М1: 21 = ATR по 21 одноминутному бару (НЕ «21-минутная свеча»). ATR — средний размах цены за один бар в пунктах (метод Уайлдера). Короче период — ATR чувствительнее к последним движениям, шаг усреднения и тейк меняются быстрее.",
    "ema1": "Быстрая EMA (например 10 баров). Когда она выше EMA2 — сигнал в лонг. Когда ниже — сигнал в шорт. Это основная линия, которая реагирует на цену.",
    "ema2": "Медленная EMA (например 140 баров). Задаёт долгосрочный тренд. Пересечение быстрой EMA1 с медленной EMA2 — это момент смены направления позиции.",
    "bet_step": "Система увеличения ставок после убытков: после каждой убыточной сделки следующий вход увеличивается на N контрактов (1→2→3…). После прибыльной сделки сбрасывается к базовому объёму. 0 = система выключена, всегда входим базовым qty.",
    "bet_max": "Максимальная добавка контрактов по системе ставок. Например, bet_max=10 при базовом qty=1 означает «не больше 1+10=11 контрактов за раз». Защита от бесконечного роста ставок при длинной серии убытков.",
    # SuperTrend / standalone strategy params
    "multiplier": "Множитель ширины полос SuperTrend. Хранится ×10: 30 = 3.0×ATR. Это расстояние от средней цены до верхней/нижней полосы в единицах ATR. Больше множитель — полосы дальше, тренд держится дольше, меньше ложных переворотов, но и реакция на разворот медленнее.",
    # Donchian params
    "entry_period": "Период входа в барах (N). Робот покупает когда цена пробивает максимум за последние N баров. 20 — классика Turtle Trading. Больше N — вход по более сильному пробою, реже сделки, но крупнее движение.",
    "exit_period": "Период выхода в барах (M). Робот продаёт когда цена падает ниже минимума за последние M баров. M < N обычно (например 10 при N=20). Меньше M — быстрее выход при развороте, меньше прибыль, но и меньше потерь.",
    # ICT / smart-money params (SkrimerForever/moex-trading-bot)
    "min_frac": "Фильтр СИЛЫ импульса, а НЕ расстояние между барами. Вход по FVG требует двух вещей: (1) трёхбарный разрыв — low текущего 1-мин бара выше high позапрошлого (бычий) или зеркально; (2) тело ТЕКУЩЕЙ 1-минутной свечи ≥ этого порога. Порог хранится ×10000 от цены: 15 = 0.15% (при цене 87000 это тело ≥ ~130 пунктов). Больше — сильнее нужен импульс, меньше входов, но чище.",
    "lookback": "Окно поиска импульсной свечи для Order Block, в барах. Робот сканирует последние N баров назад в поисках сильного движения и предшествующей ему контр-свечи (зоны заказов). Больше — ловит более старые зоны.",
    "impulse_frac": "Порог импульса для Order Block: |тело| / цена. Хранится ×10000: 30 = 0.30%. Свеча считается импульсной, если её тело больше этого порога. Больше — только мощные движения формируют зону, реже сигналы.",
    "level": "Какой уровень разворотных пивотов использовать: 1 = R1/S1 (ближние, чаще срабатывают), 2 = R2/S2 (дальние, реже, но сильнее экстремум). Уровни считаются от вчерашних High/Low/Close.",
}

STRATEGY_DESC: dict[str, str] = {
    "macd_cross": (
        "MACD Crossover — трендовая стратегия на пересечении линий MACD.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляется MACD = быстрая EMA − медленная EMA (например 12 и 26 баров).\n"
        "2. Вычисляется сигнальная линия — EMA от MACD (например 9 баров).\n"
        "3. Если MACD выше сигнальной → лонг (покупаем и держим).\n"
        "4. Если MACD ниже сигнальной → шорт (продаём и держим).\n"
        "5. Всегда в рынке после прогрева: робот переворачивается при каждом пересечении.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• При смене сигнала с шорта на лонг: закрывает шорт и открывает лонг.\n"
        "• При смене с лонга на шорт: закрывает лонг и открывает шорт.\n"
        "• Без сигнала разворота — держит текущую позицию.\n\n"
        "ДЛЯ ЧЕГО: ловит трендовые движения. На боковике часто переворачивается — теряет на комиссии."
    ),
    "bollinger_mr": (
        "Bollinger Mean-Reversion — контртрендовая стратегия возврата к среднему.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Строится полоса Боллинджера: средняя SMA(period) ± mult×σ (например 20 баров, 2.0σ).\n"
        "2. Если цена закрытия ниже нижней полосы → инструмент «перепродан», покупаем (лонг).\n"
        "3. Если цена закрытия выше верхней полосы → инструмент «перекуплен», продаём (шорт).\n"
        "4. Если цена внутри полосы → плоская позиция (не держим ничего).\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт (если был) и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг (если был) и открывает шорт.\n"
        "• Сигнал=0: закрывает любую позицию — выходим из рынка.\n\n"
        "ДЛЯ ЧЕГО: зарабатывает на возврате цены к средней после выбросов. Хорошо работает в диапазоне/боковике."
    ),
    "bollinger_bo": (
        "Bollinger Breakout — трендовая стратегия пробоя полос Боллинджера.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Строится полоса Боллинджера: средняя SMA(period) ± mult×σ (например 20 баров, 2.0σ).\n"
        "2. Если цена закрытия выше верхней полосы → пробой вверх, покупаем (лонг).\n"
        "3. Если цена закрытия ниже нижней полосы → пробой вниз, продаём (шорт).\n"
        "4. Если цена внутри полосы → держим текущую позицию (None — не трогаем).\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт (если был) и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг (если был) и открывает шорт.\n"
        "• Сигнал=None (цена внутри полосы): ничего не делаем — держим что есть.\n\n"
        "ОТЛИЧИЕ от Bollinger MR: эта стратегия идёт ЗА пробоем (трендовая), а MR — ПРОТИВ (контртренд).\n\n"
        "ДЛЯ ЧЕГО: ловит сильные импульсные движения после пробоя полос. Держит позицию пока тренд intact."
    ),
    "bollinger_bo_m1": (
        "Bollinger Breakout M1 — та же стратегия пробоя Боллинджера НО с принудительным усреднением.\n\n"
        "ОТЛИЧИЕ от базовой:\n"
        "• avg_max начинается с 5 (а не с 1) — робот МОЖЕТ и БУДЕТ добирать контракты против движения.\n"
        "• avg_step_atr = 10 (1.0×ATR) — шаг усреднения включен по умолчанию.\n"
        "• Вместо стоп-лосса используется добор: если цена идёт против позиции на 1.0×ATR —\n"
        "  добавляем ещё контракт, улучшая среднюю цену входа.\n"
        "• Тейк-профит настраивается отдельно (по умолчанию 0 — выход по сигналу).\n\n"
        "ЛОГИКА СДЕЛОК: точно как у Bollinger Breakout (пробой → вход, внутри полосы → держим),\n"
        "НО с добавлением усреднения/тейк-профита на каждом баре:\n"
        "1. Если цена ушла против позиции на avg_step_atr×ATR и контрактов < avg_max → докупаем.\n"
        "2. Если цена дошла до средней ± tp_atr×ATR → закрываем с прибылью.\n\n"
        "ДЛЯ ЧЕГО: модификация для «прокачки» — лучше держит просадку без стоп-лосса,\n"
        "докупая на откатах. Фактически мартингейл-подобная стратегия с ограничением."
    ),
    "stochastic": (
        "Stochastic Oscillator — контртрендовая стратегия на осцилляторе стохастик.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляется %K — где цена сейчас относительно диапазона high-low за period баров (14).\n"
        "2. Если %K ниже уровня перепроданности (обычно 20) → покупаем (лонг).\n"
        "3. Если %K выше уровня перекупленности (обычно 80) → продаём (шорт).\n"
        "4. Если %K между уровнями → плоская позиция (ждём).\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг и открывает шорт.\n"
        "• Сигнал=0 (в середине): закрывает любую позицию.\n\n"
        "ДЛЯ ЧЕГО: классический осциллятор перекупленности/перепроданности. Работает\n"
        "лучше всего в боковике; на сильном тренде даёт ложные сигналы."
    ),
    "cci": (
        "CCI (Commodity Channel Index) Reversal — контртрендовая на экстремумах.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляется CCI = (типичная цена − SMA типичной цены) / (0.015 × среднее отклонение).\n"
        "2. Если CCI ниже −threshold (например −100) → инструмент перепродан, покупаем (лонг).\n"
        "3. Если CCI выше +threshold (например +100) → инструмент перекуплен, продаём (шорт).\n"
        "4. Если CCI между порогами → плоская позиция.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг и открывает шорт.\n"
        "• Сигнал=0: закрывает любую позицию.\n\n"
        "ДЛЯ ЧЕГО: ловит развороты от статистических экстремумов. В отличие от RSI/Stochastic,\n"
        "CCI не ограничен 0-100 и может показать более сильные выбросы."
    ),
    "williams_r": (
        "Williams %R — контртрендовый осциллятор перекупленности/перепроданности.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляется %R = (максимум за period − цена закрытия) / (максимум − минимум) × −100.\n"
        "2. %R всегда от −100 (дно) до 0 (пик).\n"
        "3. Если %R ниже −oversold (например −80) → перепродан, покупаем (лонг).\n"
        "4. Если %R выше −overbought (например −20) → перекуплен, продаём (шорт).\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1 (%R < −80): закрывает шорт и открывает лонг.\n"
        "• Сигнал=−1 (%R > −20): закрывает лонг и открывает шорт.\n"
        "• Сигнал=0 (в середине): закрывает любую позицию.\n\n"
        "ДЛЯ ЧЕГО: аналог Stochastic, но перевёрнутый и более чувствительный к крайним значениям.\n"
        "Хорошо находит точки разворота в диапазоне."
    ),
    "momentum": (
        "Momentum — трендовая стратегия на импульсе цены.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляется моментум = цена сейчас − цена period баров назад (например 10).\n"
        "2. Если моментум > 0 → цена растёт, покупаем (лонг).\n"
        "3. Если моментум < 0 → цена падает, продаём (шорт).\n"
        "4. При смене знака моментума — переворот позиции.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Всегда в рынке: либо лонг, либо шорт.\n"
        "• При пересечении нуля снизу вверх: закрывает шорт, открывает лонг.\n"
        "• При пересечении нуля сверху вниз: закрывает лонг, открывает шорт.\n\n"
        "ДЛЯ ЧЕГО: простейший тренд-фолловер. period=1 → идёт за каждым тиком;\n"
        "period=10 → фильтрует шум, идёт за направлением последних 10 баров."
    ),
    "roc": (
        "Rate of Change — трендовая стратегия на скорости изменения цены.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляется ROC = (цена сейчас − цена period назад) / цена period назад × 100%.\n"
        "2. Если ROC > threshold (например +0.5%) → сильный рост, покупаем (лонг).\n"
        "3. Если ROC < −threshold (например −0.5%) → сильное падение, продаём (шорт).\n"
        "4. Если ROC между порогами → держим текущую позицию (не трогаем).\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг и открывает шорт.\n"
        "• Сигнал=None (внутри порогов): держим что есть.\n\n"
        "ОТЛИЧИЕ от Momentum: ROC в процентах, а не в пунктах; есть порог фильтрации.\n\n"
        "ДЛЯ ЧЕГО: фильтрует слабые движения через процентный порог. Ловит только уверенные\n"
        "направленные импульсы."
    ),
    "triple_sma": (
        "Triple SMA Alignment — трендовая стратегия на выравнивании трёх скользящих средних.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляются три SMA: быстрая (5), средняя (20), медленная (50).\n"
        "2. Если быстрая > средняя > медленная → восходящий тренд, покупаем (лонг).\n"
        "3. Если быстрая < средняя < медленная → нисходящий тренд, продаём (шорт).\n"
        "4. В остальных случаях (перемешивание) → плоская позиция.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Все три SMA выстроились по возрастанию: закрывает шорт, открывает лонг.\n"
        "• Все три SMA выстроились по убыванию: закрывает лонг, открывает шорт.\n"
        "• SMA перемешаны (например быстрая ниже средней но выше медленной): закрывает всё.\n\n"
        "ДЛЯ ЧЕГО: жёсткий трендовый фильтр — входит только когда все три масштаба\n"
        "подтверждают одно направление. Мало сделок, высокая точность."
    ),
    "keltner_bo": (
        "Keltner Breakout — трендовая на пробое канала Кельтнера.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Строится канал: средняя EMA(period) ± mult×ATR(ATR_period).\n"
        "2. Если цена закрытия выше верхней границы → пробой вверх, покупаем (лонг).\n"
        "3. Если цена закрытия ниже нижней границы → пробой вниз, продаём (шорт).\n"
        "4. Если цена внутри канала → держим текущую позицию.\n\n"
        "ОТЛИЧИЕ от Bollinger: канал Кельтнера использует ATR (истинную волатильность)\n"
        "вместо стандартного отклонения, и EMA вместо SMA. ATR лучше отражает реальный\n"
        "размер движения, особенно на FORTS с его волатильностью.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг и открывает шорт.\n"
        "• Сигнал=None: держим что есть.\n\n"
        "ДЛЯ ЧЕГО: тренд-фолловер на ATR-канале. Менее склонен к ложным пробоям чем\n"
        "Боллинджер на волатильном рынке."
    ),
    "rsi_trend": (
        "RSI + Trend Filter — покупка на откатах RSI в направлении тренда.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Определяется тренд: цена выше EMA(ema_period) → восходящий; ниже → нисходящий.\n"
        "2. Вычисляется RSI(rsi_period).\n"
        "3. Если тренд восходящий И RSI ниже oversold (например 40) → откат вверх, покупаем (лонг).\n"
        "4. Если тренд нисходящий И RSI выше overbought (например 60) → откат вниз, продаём (шорт).\n"
        "5. В остальных случаях → плоская позиция.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Сигнал=1: закрывает шорт и открывает лонг.\n"
        "• Сигнал=−1: закрывает лонг и открывает шорт.\n"
        "• Сигнал=0: закрывает всё.\n\n"
        "ДЛЯ ЧЕГО: входит только в направлении тренда на временных откатах. Не пытается\n"
        "ловить развороты против тренда — меньше ложных входов."
    ),
    "ema_atr": (
        "EMA Trend + ATR Filter — двойная EMA с фильтром импульса по ATR.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляются быстрая EMA(fast) и медленная EMA(slow).\n"
        "2. Вычисляется ATR(atr_period) — мера волатильности.\n"
        "3. Если быстрая EMA > медленная EMA И цена > медленная EMA + mult×ATR:\n"
        "   → сильный восходящий импульс, покупаем (лонг).\n"
        "4. Если быстрая EMA < медленная EMA И цена < медленная EMA − mult×ATR:\n"
        "   → сильный нисходящий импульс, продаём (шорт).\n"
        "5. В остальных случаях → плоская позиция.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Двойное подтверждение: нужно И направление EMA И достаточный ATR-импульс.\n"
        "• Без импульса — не входим даже при пересечении EMA.\n\n"
        "ДЛЯ ЧЕГО: фильтрует слабые пересечения EMA которые не подтверждены волатильностью.\n"
        "Даёт меньше входов чем чистый EMA-кросс, но с лучшим качеством."
    ),
    "shectory_2ema": (
        "Shectory-2EMA — двойная EMA с пересечением и системой ставок.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Вычисляются две EMA: быстрая (ema1, например 10 баров) и медленная (ema2, например 140).\n"
        "2. Если ema1 > ema2 → восходящий тренд, лонг.\n"
        "3. Если ema1 < ema2 → нисходящий тренд, шорт.\n"
        "4. Всегда в рынке — переворот при каждом пересечении.\n"
        "5. Встроена СИСТЕМА СТАВОК (bet_step/bet_max):\n"
        "   • После убыточной сделки следующий объём растёт на bet_step контрактов (1→2→3…).\n"
        "   • После прибыльной сделки объём сбрасывается к базовому qty.\n"
        "   • bet_max ограничивает максимальную добавку.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Пересечение ema1 снизу вверх: закрывает шорт, открывает лонг (qty + bet_extra).\n"
        "• Пересечение ema1 сверху вниз: закрывает лонг, открывает шорт (qty + bet_extra).\n"
        "• При убыточном закрытии: bet_extra += bet_step.\n\n"
        "ДЛЯ ЧЕГО: долгосрочный тренд-фолловер с «доливкой» после убытков. Медленная\n"
        "EMA=140 даёт сильный фильтр — мало сделок, крупные движения."
    ),
    "fvg": (
        "Fair Value Gap (ICT) — вход по ценовому разрыву (имбалансу).\n"
        "Порт из SkrimerForever/moex-trading-bot (MOEX-хакатон).\n\n"
        "ТАЙМФРЕЙМ: М1 (минутные свечи, жёстко). Агент-робот строит бары из ленты\n"
        "сделок QUIK; стратегия исполняется ОДИН раз по закрытию каждой минутки.\n"
        "Бэктест реплеит те же М1 — поведение 1:1.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. Смотрятся 3 последние свечи. Разрыв (FVG) — когда между свечой i и i-2 есть\n"
        "   незаполненный «зазор» цены (рынок прошёл импульсом, не торгуясь в этой зоне).\n"
        "2. Бычий FVG: low[i] > high[i-2] (зазор вверх) → лонг.\n"
        "3. Медвежий FVG: high[i] < low[i-2] (зазор вниз) → шорт.\n"
        "4. Подтверждение: тело текущей свечи в сторону разрыва ≥ min_frac (фильтр шума).\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Бычий FVG → закрывает шорт, открывает лонг (разворотная пара заявок).\n"
        "• Медвежий FVG → закрывает лонг, открывает шорт.\n"
        "• Нет разрыва → держим позицию; при позиции работают тейк и усреднение (ниже).\n\n"
        "ПАРАМЕТРЫ:\n"
        "• symbol — FORTS-тикер (например RIU6).\n"
        "• qty — контрактов на вход (базовый размер каждой новой позиции).\n"
        "• min_frac — мин. тело подтверждающей свечи, ×10000 от цены\n"
        "  (12 = 0.12%). Больше — меньше сделок, чище сигналы.\n"
        "• tp_atr — тейк-профит ×10 в ATR от средней входа (60 = 6.0×ATR;\n"
        "  0 = тейка нет, выход только по обратному сигналу).\n"
        "• avg_max — макс. контрактов в позиции. 1 = усреднение выключено;\n"
        "  больше — докупает на просадке до этого потолка.\n"
        "• avg_step_atr — шаг усреднения ×10 в ATR (24 = докупка qty при ходе\n"
        "  против позиции на 2.4×ATR; 0 = выкл).\n"
        "• avg_atr_n — период ATR (баров М1) для тейка и усреднения.\n"
        "• bet_step / bet_max — прогрессия размера после убыточной сделки\n"
        "  (+bet_step контрактов за убыток, сброс после прибыли; 0 = выкл).\n"
        "• max позиция (спека) — жёсткий потолок контрактов: заявка сверх него\n"
        "  не отправляется вовсе (страховка перед лимитами агента).\n\n"
        "ДЛЯ ЧЕГО: ловит сильные импульсные движения «умных денег», оставляющие\n"
        "имбаланс. Работает на трендовых рывках; на спокойном рынке сигналов мало,\n"
        "на пиле возможны серии мелких убыточных разворотов."
    ),
    "order_block": (
        "Order Block (ICT) — вход от зоны заказов крупного игрока.\n"
        "Порт из SkrimerForever/moex-trading-bot.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. В последних lookback барах ищется импульсная свеча: |тело|/цена ≥ impulse_frac.\n"
        "2. Order Block = последняя ПРОТИВОПОЛОЖНАЯ свеча перед импульсом:\n"
        "   • Бычий импульс → последняя медвежья свеча = зона поддержки (бычий OB).\n"
        "   • Медвежий импульс → последняя бычья свеча = зона сопротивления (медвежий OB).\n"
        "3. Когда цена возвращается (ретест) в диапазон [low, high] этой свечи —\n"
        "   вход в сторону импульса.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Цена в бычьем OB → лонг. Цена в медвежьем OB → шорт.\n"
        "• Иначе ждём (None).\n\n"
        "ДЛЯ ЧЕГО: торгует откаты к институциональным зонам перед продолжением\n"
        "движения. Меньше сделок, вход по «следам» крупного объёма."
    ),
    "pivot_reversal": (
        "Pivot Points Reversal — разворот от классических floor-пивотов.\n"
        "Порт из SkrimerForever/moex-trading-bot.\n\n"
        "КАК РАБОТАЕТ:\n"
        "1. По вчерашним High/Low/Close считается опорный уровень:\n"
        "   P = (H + L + C) / 3.\n"
        "2. Сопротивление R1 = 2P − L, поддержка S1 = 2P − H.\n"
        "   Дальние: R2 = P + (H−L), S2 = P − (H−L). Уровень выбирается параметром level.\n"
        "3. Контртренд: цена ≤ S (поддержка) → перепродано → лонг;\n"
        "   цена ≥ R (сопротивление) → перекуплено → шорт; между уровнями → выход.\n\n"
        "ЛОГИКА СДЕЛОК:\n"
        "• Пробитие/касание поддержки → лонг (ждём отскок).\n"
        "• Касание сопротивления → шорт.\n"
        "• Внутри коридора → закрываем позицию.\n\n"
        "ДЛЯ ЧЕГО: классика интрадей-трейдинга. Хорошо в диапазонные дни; на сильном\n"
        "тренде уровни пробиваются (контртренд проигрывает)."
    ),
}


def numeric_params(strat: dict) -> list[dict]:
    """Tunable numeric params of a strategy (excludes qty/symbol). Each: {key, lo, hi}.

    Single source for the campaign scripts that derive a sweep space from a strategy
    schema (was copy-pasted across optimize_adaptive / enqueue_campaign / queue_campaign).
    Step/grid heuristics stay per-script — those are intentionally different (wide
    random net vs local refine vs coarse grid), not duplicates."""
    out = []
    for p in strat["params_schema"]:
        if p.get("type") != "number" or p["key"] == "qty":
            continue
        lo = int(p.get("min", p["default"]))
        hi = int(p.get("max", p["default"]))
        if hi < lo:
            lo, hi = hi, lo
        out.append({"key": p["key"], "lo": lo, "hi": hi})
    return out


def list_strategies() -> list[dict]:
    """Public listing for the API (without the signal callable). Injects per-param
    descriptions (for the (i) tooltips) and a short strategy description."""
    out = []
    for rid, s in REGISTRY.items():
        # enrich each param with a desc (and a hint fallback) by key
        schema = []
        for p in s["params_schema"]:
            q = dict(p)
            d = PARAM_DESC.get(q["key"])
            if d and not q.get("desc"):
                q["desc"] = d
            if d and not q.get("hint"):
                q["hint"] = d
            schema.append(q)
        out.append({
            "id": rid, "name": s["name"], "source": s["source"],
            "description": STRATEGY_DESC.get(rid, ""),
            "params_schema": schema,
            "script_code": f"from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('{rid}')",
            "default_params": s["default_params"],
        })
    return out
