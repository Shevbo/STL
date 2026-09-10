"""
Impulse Fade — резкий импульс за минуты, вход ПРОТИВ него, тейк на возврате к средней.

ЗАКАЗ ОПЕРАТОРА 10.09.2026. rich_fool ловил лестницу пробоя только в предоткрытие
и умер от частоты: 0 из 2430 строк дают >=150 сделок за полгода. Здесь тот же
физический эффект — «дёрнули и вернули» — но БЕЗ привязки к часу: импульс ищется
на всём торговом дне, а тейк ставится не на продолжении, а на ВОЗВРАТЕ цены к
средней. Это фейд, а не пробой: rich_fool покупал ускорение, этот его продаёт.

МЕХАНИЗМ (всё на M1, окно импульса imp_bars <= 5 минут):
  ИМПУЛЬС   ход |close[-1] - close[-1-imp_bars]| за imp_bars баров, измеренный в
            ATR: move >= imp_atr·ATR (порог в единицах текущей волатильности, а
            не в пунктах — иначе одна сетка не живёт на RI и Si сразу).
  ВХОД      ПРОТИВ импульса: рвануло вверх → шорт, вниз → лонг (invert=1 зеркалит
            и торгует ПО импульсу — зеркальный гейт «механизм, а не сторона»).
  ТЕЙК      возврат к средней EMA(mean_n): выход, когда цена дошла до неё, либо
            когда взято take_atr·ATR — что случится раньше.
  СТОП      stop_atr·ATR от входа: импульс, который не вернулся, продолжается.
  ВРЕМЯ     max_hold баров — идея живёт минуты; висящая позиция это уже не фейд.
  ПАУЗА     cooldown баров после выхода — один импульс = одна сделка, а не серия
            входов на каждом баре затухающего движения.

Позиция фиксированная qty (лестницы нет), симметрична, running-индикаторы O(1).
"""
from trader.lab.runtime import STLRuntime


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"ImpulseFade started | imp={params.get('imp_bars', 3)}бар×{params.get('imp_atr', 20)}/10ATR "
        f"mean={params.get('mean_n', 60)} take={params.get('take_atr', 15)}/10 "
        f"stop={params.get('stop_atr', 20)}/10 hold={params.get('max_hold', 30)} "
        f"invert={params.get('invert', 0)} symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    imp_bars = max(1, int(params.get("imp_bars", 3)))
    imp_atr = float(params.get("imp_atr", 20)) / 10.0
    mean_n = max(5, int(params.get("mean_n", 60)))
    atr_n = max(5, int(params.get("atr_n", 200)))
    take_atr = float(params.get("take_atr", 15)) / 10.0
    stop_atr = float(params.get("stop_atr", 20)) / 10.0
    max_hold = max(1, int(params.get("max_hold", 30)))
    cooldown = max(0, int(params.get("cooldown", 5)))
    invert = int(params.get("invert", 0))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))

    # Нужны бары окна импульса + предыдущий для TR.
    bars = await stl.get_bars(symbol, tf=1, n=imp_bars + 2)
    if len(bars) < imp_bars + 2:
        return
    prev, cur = bars[-2], bars[-1]

    # --- running EMA(mean_n) и ATR(atr_n): значения в state = предыдущий бар ---
    n_seen = int(stl.get_state("n_seen", 0) or 0)
    m_prev = float(stl.get_state("ema_mean", 0) or 0)
    atr_prev = float(stl.get_state("atr", 0) or 0)
    if n_seen == 0:
        m_new = cur.close
        atr_new = cur.high - cur.low
    else:
        m_new = m_prev + (2.0 / (mean_n + 1)) * (cur.close - m_prev)
        tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        atr_new = atr_prev + (tr - atr_prev) / atr_n
    stl.set_state("n_seen", n_seen + 1)
    stl.set_state("ema_mean", m_new)
    stl.set_state("atr", atr_new)

    warm = n_seen + 1 >= max(mean_n, atr_n)
    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
    dirn = int(stl.get_state("dir", 0) or 0)

    # --- ведение позиции: тейк по возврату к средней / по take_atr, стоп, время ---
    if cur_qty != 0 and dirn != 0:
        entry = float(stl.get_state("entry", 0) or 0)
        a0 = float(stl.get_state("atr_at_entry", 0) or 0) or atr_new
        held = int(stl.get_state("held", 0) or 0) + 1
        stl.set_state("held", held)
        take_abs = a0 * take_atr
        stop_abs = a0 * stop_atr
        exit_px = None
        if dirn > 0:                                  # лонг: фейдим импульс ВНИЗ
            if cur.low <= entry - stop_abs:
                exit_px = entry - stop_abs
            elif cur.high >= m_new:                   # цена вернулась к средней
                exit_px = max(m_new, cur.open)
            elif cur.high >= entry + take_abs:
                exit_px = entry + take_abs
            elif held >= max_hold:
                exit_px = cur.close
            if exit_px is not None:
                await stl.place_order(symbol, "sell", abs(cur_qty), exit_px)
                _flat(stl, cooldown)
                return
        else:                                         # шорт: фейдим импульс ВВЕРХ
            if cur.high >= entry + stop_abs:
                exit_px = entry + stop_abs
            elif cur.low <= m_new:
                exit_px = min(m_new, cur.open)
            elif cur.low <= entry - take_abs:
                exit_px = entry - take_abs
            elif held >= max_hold:
                exit_px = cur.close
            if exit_px is not None:
                await stl.place_order(symbol, "buy", abs(cur_qty), exit_px)
                _flat(stl, cooldown)
                return
        return

    # --- пауза после выхода: затухающий импульс не должен давать серию входов ---
    cd = int(stl.get_state("cooldown_left", 0) or 0)
    if cd > 0:
        stl.set_state("cooldown_left", cd - 1)
        return

    # --- вход: импульс за imp_bars баров, измеренный в ATR, фейдим его ---
    if cur_qty == 0 and warm and atr_new > 0:
        move = cur.close - bars[-1 - imp_bars].close
        if abs(move) >= imp_atr * atr_new:
            want = -1 if move > 0 else 1              # ПРОТИВ импульса
            if invert:
                want = -want                          # зеркало: по импульсу
            allowed = (want > 0 and allow_long) or (want < 0 and allow_short)
            if allowed:
                await stl.place_order(symbol, "buy" if want > 0 else "sell", qty, cur.close)
                stl.set_state("dir", want)
                stl.set_state("entry", cur.close)
                stl.set_state("atr_at_entry", atr_new)
                stl.set_state("held", 0)


def _flat(stl: STLRuntime, cooldown: int) -> None:
    for k in ("dir", "entry", "atr_at_entry", "held"):
        stl.set_state(k, 0)
    stl.set_state("cooldown_left", cooldown)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("ImpulseFade stopped")


STRATEGY_META = {
    "name": "Impulse Fade — фейд резкого импульса с возвратом к средней",
    "description": (
        "Ищет резкий ход за imp_bars минут (порог в ATR, не в пунктах — одна сетка "
        "живёт на разных инструментах) и входит ПРОТИВ него. Тейк — возврат цены к "
        "EMA(mean_n) или take_atr·ATR, что раньше; стоп stop_atr·ATR; выход по времени "
        "max_hold. Пауза cooldown после сделки: один импульс = одна сделка. Работает "
        "весь торговый день, не только в предоткрытие. invert=1 торгует ПО импульсу."
    ),
    "source": "заказ оператора 10.09.2026: развитие rich_fool на весь день, фейд вместо пробоя",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "imp_bars", "label": "Окно импульса (баров M1)", "type": "number", "default": 3, "min": 1, "max": 5,
         "hint": "За сколько минут набран ход. <=5 по постановке"},
        {"key": "imp_atr", "label": "Порог импульса ×10 ATR (20=2.0)", "type": "number", "default": 20, "min": 8, "max": 60,
         "hint": "Ход в единицах ATR, а не в пунктах"},
        {"key": "mean_n", "label": "Период средней EMA (баров)", "type": "number", "default": 60, "min": 10, "max": 480,
         "hint": "Куда возвращается цена — цель тейка"},
        {"key": "atr_n", "label": "Период ATR (баров)", "type": "number", "default": 200, "min": 20, "max": 1000},
        {"key": "take_atr", "label": "Тейк ×10 ATR (15=1.5)", "type": "number", "default": 15, "min": 5, "max": 40,
         "hint": "Потолок тейка, если средняя далеко"},
        {"key": "stop_atr", "label": "Стоп ×10 ATR (20=2.0)", "type": "number", "default": 20, "min": 5, "max": 50,
         "hint": "Импульс, который не вернулся, продолжается"},
        {"key": "max_hold", "label": "Держать не дольше (баров)", "type": "number", "default": 30, "min": 3, "max": 240,
         "hint": "Идея живёт минуты"},
        {"key": "cooldown", "label": "Пауза после сделки (баров)", "type": "number", "default": 5, "min": 0, "max": 60},
        {"key": "qty", "label": "Контрактов на позицию", "type": "number", "default": 1, "min": 1, "max": 10,
         "hint": "Фиксированный размер, лестницы нет"},
        {"key": "invert", "label": "Инверсия (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "1 = вход ПО импульсу вместо фейда"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
    ],
}
