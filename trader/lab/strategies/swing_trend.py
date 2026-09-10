"""
Swing Trend — медленный тренд с покупкой отката. Без лестницы, симметричный.

ЗАЧЕМ. Полгода перебора давали ноль по одной причине: все механизмы были
внутридневные (круг 22–37 ₽ съедал эдж) и почти все с лестницей усреднения
(мартингейл: высокая доля выигрышей, ноль видимой просадки, скрытый хвост —
форма «залипшего сигнала» и «миража нулевого риска»). Здесь обратное:

  • частота 1–4 сделки в НЕДЕЛЮ — комиссия тонет в ходе на тысячи пунктов;
  • позиция фиксированная qty (лестницы нет) — плюс не из долива, а из сигнала;
  • симметричный режим — на 28%-ном падении RI стоит в шорте, а не в лонге.

МЕХАНИЗМ. Режим = close против медленной EMA (running, O(1)): выше — бычий,
ниже — медвежий. Вход — завершение отката: в бычьем режиме ждём, пока цена
закроется под быстрой EMA, и входим, когда она закрывается обратно НАД ней
(симметрично для шорта). Выход — трейлинг-стоп ATR (atr·atr_mult), либо тейк
rr·R, либо переворот режима. Позиция носится овернайт, в конце дня не закрывается.

Стратегия-модуль вне реестра: running-индикаторы хранятся в state и считаются за
O(1) на бар (иначе EMA на 8000 бар × 260k баров — ночь без прогона). qty=1 по
умолчанию: кандидат должен быть в плюсе уже при плоской позиции.
"""
from trader.lab.runtime import STLRuntime


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"SwingTrend started | slow={params.get('slow', 4000)} fast={params.get('fast', 500)} "
        f"atr={params.get('atr_n', 200)}×{params.get('atr_mult', 3)} rr={float(params.get('rr_x10', 20)) / 10:.1f} "
        f"qty={params.get('qty', 1)} invert={params.get('invert', 0)} symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    slow = max(10, int(params.get("slow", 4000)))
    fast = max(5, int(params.get("fast", 500)))
    atr_n = max(5, int(params.get("atr_n", 200)))
    atr_mult = float(params.get("atr_mult", 3))
    rr = float(params.get("rr_x10", 20)) / 10.0
    invert = int(params.get("invert", 0))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))

    bars = await stl.get_bars(symbol, tf=1, n=3)
    if len(bars) < 3:
        return
    prev, cur = bars[-2], bars[-1]

    # --- running-индикаторы (O(1) на бар) ---
    n_seen = int(stl.get_state("n_seen", 0) or 0)
    ema_slow = float(stl.get_state("ema_slow", 0) or 0)
    ema_fast = float(stl.get_state("ema_fast", 0) or 0)
    atr = float(stl.get_state("atr", 0) or 0)
    if n_seen == 0:
        ema_slow = ema_fast = cur.close
        atr = cur.high - cur.low
    else:
        a_s = 2.0 / (slow + 1)
        a_f = 2.0 / (fast + 1)
        ema_slow += a_s * (cur.close - ema_slow)
        ema_fast += a_f * (cur.close - ema_fast)
        tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        atr += (tr - atr) / atr_n
    prev_slow = ema_slow
    prev_fast = ema_fast
    stl.set_state("n_seen", n_seen + 1)
    stl.set_state("ema_slow", ema_slow)
    stl.set_state("ema_fast", ema_fast)
    stl.set_state("atr", atr)

    warm = n_seen + 1 >= max(slow, fast, atr_n)
    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
    dirn = int(stl.get_state("dir", 0) or 0)

    # --- ведение открытой позиции: трейлинг-стоп, тейк, переворот режима ---
    if cur_qty != 0 and dirn != 0:
        entry = float(stl.get_state("entry", 0) or 0)
        R = float(stl.get_state("init_stop", 0) or 0)        # риск R = atr·atr_mult на входе
        stop = float(stl.get_state("stop", 0) or 0)
        # Трейлинг обновляем по ПРЕДЫДУЩЕМУ бару, проверяем на текущем: иначе стоп,
        # посчитанный из close этого же бара, срабатывает в день своего появления.
        if dirn > 0:
            stop = max(stop, prev.close - atr * atr_mult)
            stl.set_state("stop", stop)
            exit_px = None
            if cur.low <= stop:
                exit_px = stop
            elif rr > 0 and cur.high >= entry + rr * R:
                exit_px = entry + rr * R
            elif cur.close < ema_slow:                        # режим перевернулся
                exit_px = cur.close
            if exit_px is not None:
                await stl.place_order(symbol, "sell", abs(cur_qty), exit_px)
                _flat(stl)
                return
        else:
            stop = min(stop, prev.close + atr * atr_mult)
            stl.set_state("stop", stop)
            exit_px = None
            if cur.high >= stop:
                exit_px = stop
            elif rr > 0 and cur.low <= entry - rr * R:
                exit_px = entry - rr * R
            elif cur.close > ema_slow:
                exit_px = cur.close
            if exit_px is not None:
                await stl.place_order(symbol, "buy", abs(cur_qty), exit_px)
                _flat(stl)
                return

    # --- вход: завершение отката в сторону режима ---
    if cur_qty == 0 and warm:
        reg_long = cur.close > ema_slow
        reg_short = cur.close < ema_slow
        up_x = reg_long and prev.close <= prev_fast and cur.close > ema_fast
        dn_x = reg_short and prev.close >= prev_fast and cur.close < ema_fast
        trade_dir = 0
        if up_x:
            trade_dir = 1
        elif dn_x:
            trade_dir = -1
        if trade_dir:
            trade_dir = -trade_dir if invert else trade_dir
            allowed = (trade_dir > 0 and allow_long) or (trade_dir < 0 and allow_short)
            if allowed and atr > 0:
                await stl.place_order(symbol, "buy" if trade_dir > 0 else "sell", qty, cur.close)
                stl.set_state("dir", trade_dir)
                stl.set_state("entry", cur.close)
                stl.set_state("init_stop", atr * atr_mult)
                stl.set_state("stop", atr * atr_mult)


def _flat(stl: STLRuntime) -> None:
    for k in ("dir", "entry", "init_stop", "stop"):
        stl.set_state(k, 0)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("SwingTrend stopped")


STRATEGY_META = {
    "name": "Swing Trend — медленный тренд, покупка отката",
    "description": (
        "Режим = close против медленной EMA. Вход — завершение отката: в бычьем режиме "
        "цена закрывается под быстрой EMA, затем обратно над ней (симметрично для шорта). "
        "Выход — трейлинг-стоп ATR·atr_mult, тейк rr·R или переворот режима. Позиция "
        "фиксированная qty, без лестницы, носится овернайт. Низкая частота: комиссия "
        "амортизируется крупным ходом, а не съедает эдж."
    ),
    "source": "гипотеза backtests 10.09.2026: низкая частота + без мартингейла",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "slow", "label": "Период медленной EMA (баров)", "type": "number", "default": 4000, "min": 500, "max": 12000,
         "hint": "Задаёт режим: 4000 M1 ≈ 3 суток, 8000 ≈ 5.5 суток"},
        {"key": "fast", "label": "Период быстрой EMA (баров)", "type": "number", "default": 500, "min": 100, "max": 2000,
         "hint": "Уровень, от которого ловится откат"},
        {"key": "atr_n", "label": "Период ATR (баров)", "type": "number", "default": 200, "min": 20, "max": 1000,
         "hint": "Сглаживание размаха для стопа и R"},
        {"key": "atr_mult", "label": "Стоп, ×ATR", "type": "number", "default": 3, "min": 1, "max": 6,
         "hint": "Начальный и трейлинг-стоп = ATR·atr_mult от входа"},
        {"key": "rr_x10", "label": "R:R ×10 (20=2:1, 0=только стоп)", "type": "number", "default": 20, "min": 0, "max": 50,
         "hint": "Тейк = rr·R от входа. 0 — выключает тейк, выход только по стопу/режиму"},
        {"key": "qty", "label": "Контрактов на позицию", "type": "number", "default": 1, "min": 1, "max": 10,
         "hint": "Фиксированный размер, лестницы нет"},
        {"key": "invert", "label": "Инверсия (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "1 = фейд: вход против режима"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
    ],
}
