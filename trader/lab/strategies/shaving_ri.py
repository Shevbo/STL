"""
Shaving RI 1 — «обстругивание» отклонения фьючерса RI от индексной корзины.

ИДЕЯ
    Фьючерс RI написан на индекс РТС, а индекс РТС = взвешенная корзина акций
    (Σ wᵢ·Pᵢ, веса публикует МосБиржа). В идеале RI и корзина ходят синхронно, и
    разность «RI − корзина» лежит на оси X. В жизни она отклоняется. Отклонения
    выкупаем: график выше нуля — RI дорог относительно корзины, продаём RI; ниже
    нуля — RI дёшев, покупаем. Выход — возврат к оси («догон до оси X»).

КОРЗИНА = ИНДЕКС RTSI, А НЕ РУЧНАЯ СУММА
    Σ wᵢ·Pᵢ по 40+ бумагам — это ровно то, что МосБиржа уже считает и публикует
    как индекс RTSI: с текущими весами, с делителем, с пересчётом при корпоративных
    действиях и с переводом в доллары. Ручная сумма даёт ХУДШУЮ копию того же числа
    и добавляет 40 источников сбоя (пропущенные бары, стоп-торги, дивидендные гэпы,
    ежеквартальный пересмотр весов). Поэтому опорная серия здесь — RTSI, минутные
    свечи с ISS. Скрипт scripts/shaving_ri_basket_check.py собирает ручную корзину
    по опубликованным весам и показывает, насколько точно она повторяет RTSI.

МАСШТАБ И БАЗИС — ПОЧЕМУ ГОЛАЯ РАЗНОСТЬ НЕ РАБОТАЕТ
    RI котируется примерно как RTSI × 100 (31.07.2026: RTSI 884, RIU6 88 140), а
    коэффициент не константа: за май-июль 2026 отношение RI/RTSI гуляло 96.3…102.0,
    то есть почти 6%. Это фьючерсный БАЗИС (контанго/бэквордация + стоимость денег),
    он дрейфует и стягивается к нулю к экспирации. Поэтому «RI − k·корзина» с
    фиксированным k — это не осциллятор вокруг нуля, а тренд, и правило «>0 продай»
    просто шортило бы контанго весь квартал.
    Решение — двухступенчатое сглаживание (те самые EMA1/EMA2):

        ratio_t = RI_t / BASKET_t              масштаб снимается автоматически
        axis_t  = EMA(ratio, ema_slow)         EMA2 — «ось X», медленный честный базис
        dev_t   = (ratio_t − axis_t) · BASKET_t   отклонение В ПУНКТАХ ЦЕНЫ RI
        sig_t   = EMA(dev, ema_fast)           EMA1 — снятие минутного шума
        sd_t    = СКО(dev) за z_win баров
        z_t     = sig_t / sd_t                 отклонение в сигмах

    Вход: z ≥ +entry_z → ПРОДАЁМ RI;  z ≤ −entry_z → ПОКУПАЕМ RI.
    Выход: z вернулся в полосу ±exit_z (догон до оси), либо стоп/время/тьма.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ: ЭТО НЕ АРБИТРАЖ, А ТАЙМИНГ
    Настоящий арбитраж требует ОБЕИХ ног: продать RI и купить корзину. Здесь
    торгуется одна нога — только RI. Значит P&L = (изменение отклонения) +
    (изменение самой корзины), и вторая часть — голый направленный риск, к идее
    отношения не имеющий. Отсюда три следствия, зашитые в параметры:
      • держать коротко (max_hold_min) — чем дольше сидим, тем больше в результате
        рынка и меньше отклонения;
      • денежный стоп в пунктах RI (sl_pts), а не «стоп по спреду»;
      • min_dev_pts — порог, ниже которого сделка не окупает комиссию
        (тейкерский круг по RI ≈ 19 ₽ ≈ 12 пунктов при 1.597 ₽/пункт).

ОКНО ТОРГОВЛИ ЗАДАЁТ КОРЗИНА, А НЕ FORTS
    RTSI считается только в основную сессию акций (~10:00–18:45 МСК, проверено:
    ~530 минут в день). FORTS торгуется 09:00–23:50. Вне окна корзины сигнала НЕТ:
    робот не открывает новых позиций, а открытую по умолчанию закрывает
    (hold_no_basket=0) — держать голый RI ночью против «отклонения», которое
    невозможно измерить, это уже не стратегия.

ЛИКВИДНОСТЬ ДАЛЬНЕГО КОНТРАКТА
    До того как контракт стал ближним, его минутки редкие и цены несвежие
    (RIU6 в мае 2026: медиана 2 контракта в минуту, 91% минут тоньше 10 лотов).
    Несвежая цена RI даёт ФАЛЬШИВОЕ отклонение, которое «возвращается», когда RI
    наконец печатает — на такой бумаге бэктест рисует прибыль, которую нельзя
    исполнить. min_vol отсекает такие минуты.

Standalone-модуль: своя машина состояния и свои выходы, слой make_on_bar
(усреднение/тейк/разножка) НЕ применяется. Усреднения нет намеренно — доливаться
в расходящийся спред это стандартный способ разориться на mean-reversion.
"""
from trader.lab.runtime import STLRuntime


def _st(stl: STLRuntime, key: str, default=None):
    v = stl.get_state(key)
    return default if v is None else v


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"Shaving RI started | {params.get('symbol')} vs {params.get('basket', 'RTSI')} "
            f"ema_slow={params.get('ema_slow', 300)} ema_fast={params.get('ema_fast', 15)} "
            f"z={float(params.get('entry_z_x10', 20)) / 10:.1f}/"
            f"{float(params.get('exit_z_x10', 5)) / 10:.1f}")


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    basket = params.get("basket", "RTSI")
    qty = max(1, int(params.get("qty", 1)))
    ema_slow = max(2, int(params.get("ema_slow", 300)))
    ema_fast = max(1, int(params.get("ema_fast", 15)))
    z_win = max(10, int(params.get("z_win", 240)))
    entry_z = float(params.get("entry_z_x10", 20)) / 10.0
    exit_z = float(params.get("exit_z_x10", 5)) / 10.0
    min_dev = float(params.get("min_dev_pts", 30))
    sl_pts = float(params.get("sl_pts", 0))
    max_hold = int(params.get("max_hold_min", 240))
    max_stale = int(params.get("max_stale_min", 3))
    min_vol = int(params.get("min_vol", 5))
    hold_dark = int(params.get("hold_no_basket", 0))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))

    bars = await stl.get_bars(symbol, tf=1, n=2)
    if not bars:
        return
    cur = bars[-1]

    # ── опорная серия: последний бар корзины НЕ ПОЗЖЕ текущего бара RI ──────────
    bb = await stl.get_bars(basket, tf=1, n=1)
    fresh = False
    if bb:
        age_min = (cur.time - bb[-1].time) / 60.0
        fresh = 0 <= age_min <= max_stale and bb[-1].close > 0

    # ── инкрементальный пересчёт оси и отклонения: ровно ОДИН раз на новый бар
    #    корзины. Повторное прогоняние того же значения через EMA раздувало бы
    #    вес несвежей цены и стягивало ось к последнему принту. ─────────────────
    if fresh and bb[-1].time != _st(stl, "lbt", 0):
        stl.set_state("lbt", bb[-1].time)
        bc = bb[-1].close
        ratio = cur.close / bc
        axis = _st(stl, "ax")
        axis = ratio if axis is None else axis + (2.0 / (ema_slow + 1)) * (ratio - axis)
        stl.set_state("ax", axis)
        dev = (ratio - axis) * bc
        sig = _st(stl, "sm")
        sig = dev if sig is None else sig + (2.0 / (ema_fast + 1)) * (dev - sig)
        stl.set_state("sm", sig)
        # Скользящее СКО отклонения на суммах — O(1) на бар, окно z_win.
        win = list(_st(stl, "w", []))
        wsum = float(_st(stl, "ws", 0.0)) + dev
        wsq = float(_st(stl, "wq", 0.0)) + dev * dev
        win.append(dev)
        if len(win) > z_win:
            old = win.pop(0)
            wsum -= old
            wsq -= old * old
        stl.set_state("w", win)
        stl.set_state("ws", wsum)
        stl.set_state("wq", wsq)
        stl.set_state("n", int(_st(stl, "n", 0)) + 1)

    win = _st(stl, "w", [])
    sig = _st(stl, "sm")
    n_seen = int(_st(stl, "n", 0))
    z = 0.0
    ready = False
    if sig is not None and len(win) >= z_win and n_seen >= ema_slow:
        m = float(_st(stl, "ws", 0.0)) / len(win)
        var = float(_st(stl, "wq", 0.0)) / len(win) - m * m
        sd = var ** 0.5 if var > 0 else 0.0
        if sd > 0:
            z = sig / sd
            ready = True
    stl.set_state("z", round(z, 3))
    stl.set_state("dev", round(sig, 1) if sig is not None else None)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)

    # ── управление открытой позицией (проверяется ДО любых входов) ──────────────
    if cur_qty != 0:
        entry = float(_st(stl, "ent", cur.close))
        held = int(_st(stl, "held", 0)) + 1
        stl.set_state("held", held)
        reason = None
        if not fresh and not hold_dark:
            reason = "no-basket"
        elif sl_pts > 0 and ((cur_qty > 0 and cur.close <= entry - sl_pts)
                             or (cur_qty < 0 and cur.close >= entry + sl_pts)):
            reason = "stop"
        elif max_hold > 0 and held >= max_hold:
            reason = "time"
        elif ready and ((cur_qty > 0 and z >= -exit_z) or (cur_qty < 0 and z <= exit_z)):
            reason = "axis"
        if reason:
            await stl.place_order(symbol, "sell" if cur_qty > 0 else "buy", abs(cur_qty), cur.close)
            stl.set_state("held", 0)
            stl.log(f"EXIT {reason} {abs(cur_qty)} @ {cur.close:.0f} z={z:.2f}")
        return

    # ── вход: только на свежей корзине, ликвидной минуте RI и прогретых окнах ───
    if not ready or not fresh or cur.volume < min_vol:
        return
    if abs(sig) < min_dev:
        return
    side = None
    if z >= entry_z and allow_short:
        side = "sell"
    elif z <= -entry_z and allow_long:
        side = "buy"
    if side is None:
        return
    await stl.place_order(symbol, side, qty, cur.close)
    stl.set_state("ent", cur.close)
    stl.set_state("held", 0)
    stl.log(f"ENTER {side} {qty} @ {cur.close:.0f} z={z:.2f} dev={sig:.0f}pts")


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Shaving RI stopped")


STRATEGY_META = {
    "name": "Shaving RI 1",
    "description": (
        "Выкуп отклонения RI от индексной корзины RTSI. ratio=RI/RTSI, ось = EMA2(ratio), "
        "отклонение dev=(ratio−ось)×RTSI в пунктах RI, сигнал = EMA1(dev)/СКО. "
        "z>+entry → продаём RI, z<−entry → покупаем, выход при возврате к оси. "
        "Торгуется ОДНА нога (только RI), поэтому это тайминг-сигнал, а не безрисковый арбитраж."
    ),
    "source": "Индексный базис RTSI (веса корзины: moex.com, ISS analytics/RTSI)",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6",
         "hint": "FORTS тикер фьючерса на индекс РТС"},
        {"key": "basket", "label": "Корзина", "type": "text", "default": "RTSI",
         "hint": "Опорная серия. RTSI = готовая взвешенная корзина МосБиржи (Σ веc×цена)"},
        {"key": "ema_slow", "label": "EMA2 — ось X (баров)", "type": "number", "default": 300,
         "min": 30, "max": 3000,
         "hint": "Медленная EMA отношения RI/корзина = честный базис. Больше — ось жёстче, отклонения крупнее"},
        {"key": "ema_fast", "label": "EMA1 — сглаживание (баров)", "type": "number", "default": 15,
         "min": 1, "max": 300,
         "hint": "Быстрая EMA отклонения: срезает минутный шум. 1 = без сглаживания"},
        {"key": "z_win", "label": "Окно СКО (баров)", "type": "number", "default": 240,
         "min": 30, "max": 1200,
         "hint": "На скольких барах меряем нормальный размер отклонения (сигму)"},
        {"key": "entry_z_x10", "label": "Вход, сигм ×10", "type": "number", "default": 20,
         "min": 5, "max": 60,
         "hint": "20 = вход при отклонении 2.0 сигмы. Меньше — чаще сделки и больше шума"},
        {"key": "exit_z_x10", "label": "Выход, сигм ×10", "type": "number", "default": 5,
         "min": 0, "max": 30,
         "hint": "0 = ждать полного возврата на ось. 5 = закрыть в полосе ±0.5 сигмы"},
        {"key": "min_dev_pts", "label": "Мин. отклонение, пунктов RI", "type": "number", "default": 30,
         "min": 0, "max": 500,
         "hint": "Порог окупаемости: тейкерский круг по RI ≈ 12 пунктов. Ниже порога не входим"},
        {"key": "sl_pts", "label": "Стоп, пунктов RI", "type": "number", "default": 0,
         "min": 0, "max": 3000,
         "hint": "Денежный стоп по цене RI (нога голая, риск направленный). 0 = выключен"},
        {"key": "max_hold_min", "label": "Максимум удержания (мин)", "type": "number", "default": 240,
         "min": 0, "max": 1440,
         "hint": "Чем дольше держим, тем больше в P&L рынка и меньше отклонения. 0 = без ограничения"},
        {"key": "min_vol", "label": "Мин. объём минутки RI", "type": "number", "default": 5,
         "min": 0, "max": 500,
         "hint": "Отсекает несвежие цены дальнего контракта: они дают фальшивое отклонение"},
        {"key": "max_stale_min", "label": "Допустимый возраст корзины (мин)", "type": "number", "default": 3,
         "min": 1, "max": 60,
         "hint": "Старше — считаем, что корзины нет (индекс РТС считают только в сессию акций)"},
        {"key": "hold_no_basket", "label": "Держать без корзины (0/1)", "type": "number", "default": 0,
         "min": 0, "max": 1,
         "hint": "0 = закрыть позицию, когда индекс перестал считаться (вечер/ночь FORTS)"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Покупать RI, когда он дешевле корзины"},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Продавать RI, когда он дороже корзины"},
        {"key": "qty", "label": "Контрактов", "type": "number", "default": 1, "min": 1, "max": 20,
         "hint": "Лотность. Усреднения нет — позиция всегда qty контрактов"},
    ],
}
