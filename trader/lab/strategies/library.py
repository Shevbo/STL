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
    """Build a STL on_bar(stl, params) from a registered signal function."""
    spec = REGISTRY[rid]
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
        unit = base_unit + bet_extra                             # current entry size
        avg_max = max(unit, int(params.get("avg_max", 1) or 1))   # max contracts to hold
        k_step = float(params.get("avg_step_atr", 0) or 0) / 10.0  # add per k_step×ATR adverse
        tp = float(params.get("tp_atr", 0) or 0) / 10.0           # take-profit in ×ATR (0=off)
        atr_n = int(params.get("avg_atr_n", 14) or 14)
        atr_active = (k_step > 0) or (tp > 0)        # ATR only needed for averaging/TP
        need = max(warmup(params), atr_n + 1) if atr_active else warmup(params)
        bars = await stl.get_bars(symbol, tf=1, n=need)
        if len(bars) < need:
            return
        want = signal(bars, params)        # +1 / -1 / 0 / None
        price = bars[-1].close
        pos = await stl.get_position(symbol)
        cur = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
        avg = float(pos.avg_price)
        cur_dir = 1 if cur > 0 else (-1 if cur < 0 else 0)

        # 1) Signal flip / flat → close the whole position (and open the new side).
        if cur != 0 and want is not None and (want == 0 or (want > 0) != (cur > 0)):
            if bet_step > 0:                      # closed-trade result drives the betting system
                bet_extra = min(bet_extra + bet_step, bet_max) if (price - avg) * cur_dir < 0 else 0
                stl.set_state("bet_extra", bet_extra)
            await stl.place_order(symbol, "sell" if cur > 0 else "buy", abs(cur), price)
            if want != 0:
                await stl.place_order(symbol, "buy" if want > 0 else "sell", base_unit + bet_extra, price)
            return
        # 2) Flat → open a fresh base position on a signal.
        if cur == 0:
            if want is not None and want != 0:
                await stl.place_order(symbol, "buy" if want > 0 else "sell", unit, price)
            return
        # 3) Holding (signal agrees or is None) → manage take-profit + averaging by ATR.
        if not ((tp > 0) or (k_step > 0 and abs(cur) < avg_max)):
            return
        atrv = I.atr(_h(bars), _l(bars), _c(bars), atr_n)
        if atrv <= 0:
            return
        if tp > 0:    # take-profit measured from the (averaged) entry (a TP is a win)
            if cur_dir > 0 and price >= avg + tp * atrv:
                if bet_step > 0:
                    stl.set_state("bet_extra", 0)
                await stl.place_order(symbol, "sell", abs(cur), price)
                return
            if cur_dir < 0 and price <= avg - tp * atrv:
                if bet_step > 0:
                    stl.set_state("bet_extra", 0)
                await stl.place_order(symbol, "buy", abs(cur), price)
                return
        if k_step > 0 and abs(cur) < avg_max:   # average in: add a unit on adverse move
            add = min(unit, avg_max - abs(cur))
            if cur_dir > 0 and price <= avg - k_step * atrv:
                await stl.place_order(symbol, "buy", add, price)
                return
            if cur_dir < 0 and price >= avg + k_step * atrv:
                await stl.place_order(symbol, "sell", add, price)
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
]
# "M1" variants: averaging FORCED on (min ≥ 2 contracts, step always > 0), so the
# modification always averages-instead-of-SL — distinct from the plain robot which
# can sweep averaging off.
AVG_PARAMS_FORCED = [
    P("avg_max", "Усреднение: макс контрактов", 5, 2, 10),
    P("avg_step_atr", "Усреднение: шаг ×ATR/10", 10, 5, 30),
    P("tp_atr", "Тейк-профит ×ATR/10 (0=по сигналу)", 0, 0, 60),
    P("avg_atr_n", "ATR период (усреднение)", 14, 5, 40),
]

# ════════════════════════════════════════════════════════════════════════════
#  STRATEGY SIGNALS  (closes = [b.close ...]; highs/lows similar)
# ════════════════════════════════════════════════════════════════════════════

def _c(bars): return [b.close for b in bars]
def _h(bars): return [b.high for b in bars]
def _l(bars): return [b.low for b in bars]


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


# ── Param descriptions (по ключу) + краткое описание стратегий ──────────────────
# Generic but accurate per-parameter explanations, injected into every schema so
# each field gets an (i) tooltip. Keyed by param `key`.
PARAM_DESC: dict[str, str] = {
    "symbol": "Торгуемый фьючерс FORTS (тикер@тип). От инструмента зависят стоимость пункта и ГО.",
    "qty": "Сколько контрактов в сделке. Влияет на размер позиции, ГО и риск пропорционально.",
    "period": "Длина окна индикатора в барах. Меньше — быстрее реакция и больше сигналов/шума; больше — глаже и позже вход.",
    "fast": "Период быстрой линии. Чем меньше, тем чувствительнее к развороту, но больше ложных сигналов.",
    "slow": "Период медленной линии. Задаёт «несущий» тренд; больше — реже и крупнее сделки.",
    "mid": "Период средней линии. Используется как промежуточный фильтр выравнивания тренда.",
    "signal": "Период сигнальной линии MACD. Сглаживает MACD; пересечение даёт вход/выход.",
    "mult": "Множитель ширины канала (хранится ×10: 20 = 2.0). Больше — полосы дальше, реже сигналы.",
    "threshold": "Порог срабатывания индикатора. Дальше от нуля — реже, но более «уверенные» сигналы.",
    "oversold": "Уровень перепроданности: ниже него — сигнал на покупку (вход в лонг).",
    "overbought": "Уровень перекупленности: выше него — сигнал на продажу (вход в шорт).",
    "rsi_period": "Период RSI. Короче — резче колебания осциллятора, больше входов.",
    "ema_period": "Период EMA-фильтра тренда. Длиннее — более устойчивый, но запаздывающий фильтр.",
    "atr_period": "Окно расчёта ATR (волатильности). Влияет на ширину канала/фильтра в пунктах.",
    "avg_max": "Усреднение вместо стоп-лосса: максимум контрактов в позиции. 1 = выкл (обычный режим). >1 — добор против движения до N, улучшая среднюю. Реальный ГО считается от пика контрактов.",
    "avg_step_atr": "Шаг добора в долях ATR (хранится ×10: 10 = 1.0×ATR). Добираем контракт, когда цена ушла против средней на этот шаг. 0 = усреднение выключено.",
    "tp_atr": "Тейк-профит в долях ATR от средней цены (×10: 20 = 2.0×ATR). 0 = выход только по сигналу. Часто фиксирует отскок усреднённой позиции.",
    "avg_atr_n": "Период ATR для шага усреднения и тейка. Короче — чувствительнее к текущей волатильности.",
    "ema1": "Быстрая EMA. Пересекает медленную снизу вверх → сигнал в лонг, сверху вниз → в шорт.",
    "ema2": "Медленная EMA. Задаёт несущий тренд; пересечение быстрой с ней даёт вход/разворот.",
    "bet_step": "Система ставок: после убыточной сделки следующий объём +N контрактов; после прибыльной — сброс к базовому (1,2,3,…). 0 = выкл.",
    "bet_max": "Потолок добавки по системе ставок — сколько максимум контрактов добавить сверх базового (защита от разгона мартингейла).",
}

STRATEGY_DESC: dict[str, str] = {
    "macd_cross": "Пересечение MACD и сигнальной линии: лонг при MACD выше сигнальной, шорт — ниже. Трендовая, в обе стороны.",
    "bollinger_mr": "Возврат к среднему по полосам Боллинджера: покупка ниже нижней полосы, продажа выше верхней. Контртренд.",
    "bollinger_bo": "Пробой полос Боллинджера: лонг при пробое верхней, шорт — нижней. Трендовая, ловит импульс.",
    "stochastic": "Стохастик-осциллятор: покупка из зоны перепроданности, продажа из перекупленности. Контртренд.",
    "cci": "CCI (индекс товарного канала): разворот от экстремальных значений ±порог. Контртренд.",
    "williams_r": "Williams %R: осциллятор перекупленности/перепроданности. Покупка снизу, продажа сверху диапазона. Контртренд.",
    "momentum": "Моментум: лонг при положительном импульсе цены, шорт — при отрицательном. Трендовая.",
    "roc": "Rate of Change: вход по скорости изменения цены выше/ниже порога. Трендовая.",
    "triple_sma": "Выравнивание трёх SMA: лонг при быстрая>средняя>медленная, шорт — наоборот. Трендовый фильтр.",
    "keltner_bo": "Пробой канала Кельтнера (EMA ± множитель×ATR): лонг вверх, шорт вниз. Трендовая.",
    "rsi_trend": "RSI с фильтром тренда по EMA: покупка на откате в восходящем тренде, продажа — в нисходящем.",
    "ema_atr": "Двойная EMA + подтверждение прорыва на ATR: вход по направлению тренда при достаточном импульсе.",
}


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
