"""
Impulse Fade — заявки СТОЯТ далеко от цены и ждут «волосяного прокола».

СПЕЦИФИКАЦИЯ ОПЕРАТОРА 12.09.2026. Ловим не импульс вообще, а редкое событие:
новость, паника, вход или выход кита, работа манипулятора. На графике это шпиль
одним-двумя барами, прошивающий уровни нескольких дневных свечей и возвращающийся
на 1/2-2/3 хода назад в течение 1-240 минут. Заявки живут ВЕСЬ торговый день, без
привязки к часу (в этом отличие от rich_fool, который стоял только в предоткрытие
и умер от частоты: 0 из 2430 строк дали >=150 сделок за полгода).

ЧТО ИЗМЕНИЛОСЬ ПРОТИВ ПЕРВОЙ ВЕРСИИ (10.09) И ПОЧЕМУ. Та версия ЗАМЕЧАЛА импульс
(ход >= imp_atr·ATR за imp_bars баров) и входила рынком по close следующего бара.
Так шпиль не поймать: к моменту входа прокол уже отыгран, и робот покупал остаток
движения по средней цене вместо экстремума. Теперь наоборот — заявка ВИСИТ на
уровне заранее, и её исполняет сам прокол. Уровень и есть детектор импульса:
обычная торговля до него не доходит.

МЕХАНИЗМ (M1):
  ЯКОРЬ     EMA(mean_n) — «справедливая» цена, от которой меряется отклонение.
            Якорь плывёт за рынком, поэтому медленный ход его НЕ обгоняет: дистанция
            до уровня не набирается, и тренд заявку не задевает.
  УРОВНИ    anchor ± (lvl_atr + k·step_atr)·ATR, k=0..step_count-1. Дистанция в ATR,
            а не в пунктах: одна сетка живёт и на RI, и на Si.
  ВХОД      прокол уровня = исполнение заявки ПРОТИВ хода (вверх → шорт, вниз →
            лонг). invert=1 зеркалит и торгует ПО проколу — гейт «механизм, а не
            сторона». Один бар может прошить несколько ступеней — исполняются ВСЕ.
  СКОРОСТЬ  imp_frac: до уровня цена обязана дойти БЫСТРО — за последние imp_bars
            баров пройти не меньше imp_frac% дистанции якорь→уровень. Шпиль это
            проходит, доползание — нет. 0 = выключено.
  БОКОВИК   торгуем только в боковике (flat_only=1). Прокол в тренде — не прокол,
            а продолжение движения, и фейд в нём это ставка против рынка. Режим
            окна классифицирует trend_detector (drift + Kaufman ER), НЕ поминутно:
            ответ кэшируется на _REG_EVERY баров, режим за минуту не меняется.
  ТЕЙК      ВОЗВРАТ на ret_pct% импульса: цель = средняя ∓ ret_pct%·(средняя −
            якорь на момент первого филла). 100% = полный возврат к якорю,
            50-67% = постановка оператора «1/2-2/3».
  СТОП      stop_atr·ATR ЗА последней ступенью лестницы (не от средней): стоп внутри
            лестницы обрывал бы набор и делал step_count бутафорией.
  ВРЕМЯ     max_hold баров. Идея живёт 1-240 минут; что не вернулось — не прокол.
  ПАУЗА     cooldown баров после выхода: один прокол = одна сделка.

Режим «Ралли» четвёртым состоянием рынка — отдельная сессия, здесь его нет.
"""
from trader.lab.runtime import STLRuntime
from trader.lab.trend_detector import FLAT, detect_regime

# Как часто пересчитывать режим рынка. Окно режима — дни, ответ за минуту не
# меняется, а detect_regime на каждом баре стоит дороже всей остальной стратегии.
# ponytail: 30 баров захардкожены, отдельной осью перебора это быть не должно.
_REG_EVERY = 30
# До скольки точек прореживать окно режима перед классификацией. Сам detect_regime
# тоже прореживает (POINTS=120), но сначала строит список закрытий целиком —
# на 14400 барах это и есть вся цена вызова. Режем до вызова.
_REG_POINTS = 240


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"ImpulseFade started | lvl={params.get('lvl_atr', 60)}/10ATR "
        f"×{params.get('step_count', 1)} шаг {params.get('step_atr', 20)}/10 "
        f"mean={params.get('mean_n', 60)} imp={params.get('imp_frac', 50)}%/"
        f"{params.get('imp_bars', 5)}бар ret={params.get('ret_pct', 50)}% "
        f"stop={params.get('stop_atr', 20)}/10 hold={params.get('max_hold', 60)} "
        f"flat_only={params.get('flat_only', 1)} invert={params.get('invert', 0)} "
        f"symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    mean_n = max(5, int(params.get("mean_n", 60)))
    atr_n = max(5, int(params.get("atr_n", 200)))
    lvl_atr = float(params.get("lvl_atr", 60)) / 10.0
    step_atr = float(params.get("step_atr", 20)) / 10.0
    step_count = max(1, int(params.get("step_count", 1)))
    imp_bars = max(1, int(params.get("imp_bars", 5)))
    imp_frac = float(params.get("imp_frac", 50)) / 100.0
    ret_pct = float(params.get("ret_pct", 50)) / 100.0
    stop_atr = float(params.get("stop_atr", 20)) / 10.0
    max_hold = max(1, int(params.get("max_hold", 60)))
    cooldown = max(0, int(params.get("cooldown", 5)))
    flat_only = int(params.get("flat_only", 1))
    reg_win = max(200, int(params.get("reg_win", 14400)))
    reg_drift = float(params.get("reg_drift", 300)) / 10000.0
    invert = int(params.get("invert", 0))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))

    bars = await stl.get_bars(symbol, tf=1, n=imp_bars + 2)
    if len(bars) < 2:
        return
    prev, cur = bars[-2], bars[-1]

    # --- running EMA(mean_n) и ATR(atr_n) ---------------------------------------
    n_seen = int(stl.get_state("n_seen", 0) or 0)
    m_prev = float(stl.get_state("ema_mean", 0) or 0)
    atr_prev = float(stl.get_state("atr", 0) or 0)
    if n_seen == 0:
        anchor = cur.close
        atr = cur.high - cur.low
    else:
        anchor = m_prev + (2.0 / (mean_n + 1)) * (cur.close - m_prev)
        tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        atr = atr_prev + (tr - atr_prev) / atr_n
    n_seen += 1
    stl.set_state("n_seen", n_seen)
    stl.set_state("ema_mean", anchor)
    stl.set_state("atr", atr)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
    dirn = int(stl.get_state("dir", 0) or 0)

    # Пояс безопасности: позиция есть, а направления нет (перезапуск, потеря
    # состояния) — закрыть, а не доливать вслепую.
    if cur_qty != 0 and dirn == 0:
        await stl.place_order_at(symbol, "sell" if cur_qty > 0 else "buy",
                                 abs(cur_qty), cur.close, cur.time)
        _flat(stl, cooldown)
        return

    # ── 1. Ведение позиции: стоп за лестницей / возврат на ret_pct / время ──────
    if cur_qty != 0:
        levels = [float(x) for x in (stl.get_state("levels") or [])]
        anchor0 = float(stl.get_state("anchor0", 0) or 0)
        atr0 = float(stl.get_state("atr0", 0) or 0) or atr
        avg = float(pos.avg_price) or float(stl.get_state("entry", 0) or 0)
        held = int(stl.get_state("held", 0) or 0) + 1
        stl.set_state("held", held)
        side = "sell" if dirn > 0 else "buy"

        # Стоп — ЗА последней ступенью, а не от средней: иначе он выбивал бы
        # позицию раньше, чем лестница успела добрать.
        far = (min([avg] + levels) if dirn > 0 else max([avg] + levels))
        sl_px = far - atr0 * stop_atr if dirn > 0 else far + atr0 * stop_atr
        gap = cur.open <= sl_px if dirn > 0 else cur.open >= sl_px
        hit_sl = cur.low <= sl_px if dirn > 0 else cur.high >= sl_px
        if gap or hit_sl:
            # Гэп сквозь стоп исполняется по открытию (проскальзывание), иначе по уровню.
            await stl.place_order_at(symbol, side, abs(cur_qty),
                                     cur.open if gap else sl_px, cur.time)
            _flat(stl, cooldown)
            return

        # ТЕЙК: возврат на ret_pct импульса. Импульс = |средняя − якорь на первом филле|.
        imp = abs(avg - anchor0)
        if imp > 0:
            tp_px = avg + imp * ret_pct if dirn > 0 else avg - imp * ret_pct
            if (cur.high >= tp_px) if dirn > 0 else (cur.low <= tp_px):
                await stl.place_order_at(symbol, side, abs(cur_qty), tp_px, cur.time)
                _flat(stl, cooldown)
                return

        if held >= max_hold:
            await stl.place_order_at(symbol, side, abs(cur_qty), cur.close, cur.time)
            _flat(stl, cooldown)
            return

        # Позиция жива — лестница заморожена, добираем следующие ступени.
        # Ступени лежат в сторону ПРОКОЛА (spike), а сторона сделки — dirn. При
        # invert=1 это разные знаки, и путать их нельзя: лестница уходит вверх, а
        # робот покупает.
        spike = int(stl.get_state("spike", 0) or 0)
        hit = int(stl.get_state("hit", 0) or 0)
        while spike and hit < len(levels):
            lv = levels[hit]
            touched = cur.high >= lv if spike > 0 else cur.low <= lv
            if not touched:
                break
            await stl.place_order_at(symbol, "buy" if dirn > 0 else "sell",
                                     qty, lv, cur.time)
            hit += 1
        stl.set_state("hit", hit)
        return

    # ── 2. Пауза после сделки ──────────────────────────────────────────────────
    cd = int(stl.get_state("cooldown_left", 0) or 0)
    if cd > 0:
        stl.set_state("cooldown_left", cd - 1)
        return

    if n_seen < max(mean_n, atr_n) or atr <= 0:
        return

    # ── 3. Гейт боковика ───────────────────────────────────────────────────────
    # Прокол в тренде — не прокол, а продолжение хода. Ответ кэшируется: режим
    # считается по окну в дни и за минуту не меняется.
    if flat_only:
        if n_seen % _REG_EVERY == 0 or stl.get_state("flat") is None:
            hist = await stl.get_bars(symbol, tf=1, n=reg_win)
            if len(hist) < reg_win:
                # Окна ещё нет. `enough` у детектора считает ТОЧКИ, а не календарь:
                # три часа, прореженные до 240 точек, прошли бы как полноценный
                # ответ. Пока истории на окно не набралось — не торгуем.
                stl.set_state("flat", 0)
            else:
                stride = max(1, len(hist) // _REG_POINTS)
                reg = detect_regime([b.close for b in hist[::stride]], min_drift=reg_drift)
                stl.set_state("flat", 1 if (reg.state == FLAT and reg.enough) else 0)
        if not int(stl.get_state("flat", 0) or 0):
            return

    # ── 4. Заявки стоят далеко от цены; исполняет их сам прокол ────────────────
    offs = [(lvl_atr + k * step_atr) * atr for k in range(step_count)]
    up = [anchor + o for o in offs]
    dn = [anchor - o for o in offs]
    fire = 0
    if cur.high >= up[0]:
        fire = 1
    elif cur.low <= dn[0]:
        fire = -1
    if not fire:
        return

    # СКОРОСТЬ: до уровня надо дойти рывком, а не доползти. Меряем ход от закрытия
    # imp_bars баров назад до экстремума текущего бара против дистанции якорь→уровень.
    if imp_frac > 0:
        if len(bars) < imp_bars + 1:
            return
        was = bars[-1 - imp_bars].close
        travelled = (cur.high - was) if fire > 0 else (was - cur.low)
        if travelled < imp_frac * offs[0]:
            return

    trade_dir = -fire if invert == 0 else fire      # фейд: вверх → шорт
    if (trade_dir > 0 and not allow_long) or (trade_dir < 0 and not allow_short):
        return

    levels = up if fire > 0 else dn
    hit = 0
    while hit < step_count:
        lv = levels[hit]
        touched = cur.high >= lv if fire > 0 else cur.low <= lv
        if not touched:
            break
        # Филл РОВНО по уровню, даже если бар открылся за ним: реальный лимитник
        # в таком гэпе исполнился бы по открытию, то есть ЛУЧШЕ. Считаем хуже.
        await stl.place_order_at(symbol, "buy" if trade_dir > 0 else "sell",
                                 qty, lv, cur.time)
        hit += 1
    stl.set_state("dir", trade_dir)
    stl.set_state("spike", fire)
    stl.set_state("hit", hit)
    stl.set_state("levels", levels)
    stl.set_state("anchor0", anchor)
    stl.set_state("atr0", atr)
    stl.set_state("entry", levels[0])
    stl.set_state("held", 0)


def _flat(stl: STLRuntime, cooldown: int) -> None:
    for k in ("dir", "spike", "hit", "entry", "anchor0", "atr0", "held"):
        stl.set_state(k, 0)
    stl.set_state("levels", None)
    stl.set_state("cooldown_left", cooldown)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("ImpulseFade stopped")


STRATEGY_META = {
    "name": "Impulse Fade — заявки далеко от цены, фейд «волосяного прокола»",
    "description": (
        "Заявки ВИСЯТ на расстоянии lvl_atr·ATR от EMA(mean_n) весь торговый день и "
        "исполняются только резким проколом — новостью, паникой, входом кита. Уровень "
        "и есть детектор импульса: обычная торговля до него не доходит, а медленный "
        "тренд тянет якорь за собой и дистанцию не набирает. Дополнительно: гейт "
        "скорости (imp_frac% дистанции за imp_bars баров) и гейт боковика "
        "(trend_detector). Тейк — возврат на ret_pct% импульса (постановка: 1/2-2/3), "
        "стоп за последней ступенью лестницы, выход по времени max_hold (1-240 мин). "
        "invert=1 торгует ПО проколу — зеркальный гейт."
    ),
    "source": "спецификация оператора 12.09.2026: импульсы весь день, заявки далеко от цены, только в боковике",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "ТОЛЬКО контракт, не сшитая серия"},
        {"key": "lvl_atr", "label": "Дистанция до заявки ×10 ATR (60=6.0)", "type": "number", "default": 60, "min": 20, "max": 300,
         "hint": "Как далеко от якоря висит первая заявка. В ATR, чтобы одна сетка жила на разных инструментах"},
        {"key": "step_count", "label": "Ступеней лестницы", "type": "number", "default": 1, "min": 1, "max": 5,
         "hint": "Одна заявка или лестница вглубь прокола"},
        {"key": "step_atr", "label": "Шаг лестницы ×10 ATR (20=2.0)", "type": "number", "default": 20, "min": 5, "max": 100},
        {"key": "mean_n", "label": "Период якоря EMA (баров)", "type": "number", "default": 60, "min": 10, "max": 480,
         "hint": "От него меряется дистанция и к нему возвращается цена"},
        {"key": "atr_n", "label": "Период ATR (баров)", "type": "number", "default": 200, "min": 20, "max": 1000},
        {"key": "imp_bars", "label": "Окно скорости (баров)", "type": "number", "default": 5, "min": 1, "max": 30,
         "hint": "За сколько минут прокол обязан состояться"},
        {"key": "imp_frac", "label": "Скорость: % дистанции за окно", "type": "number", "default": 50, "min": 0, "max": 100,
         "hint": "0 = выключено. Отсекает доползание до уровня вместо рывка"},
        {"key": "ret_pct", "label": "Тейк: возврат % импульса", "type": "number", "default": 50, "min": 20, "max": 100,
         "hint": "Постановка оператора — 1/2-2/3 хода назад. 100 = полный возврат к якорю"},
        {"key": "stop_atr", "label": "Стоп за лестницей ×10 ATR (20=2.0)", "type": "number", "default": 20, "min": 5, "max": 100,
         "hint": "Отсчитывается от ПОСЛЕДНЕЙ ступени, не от средней"},
        {"key": "max_hold", "label": "Держать не дольше (баров)", "type": "number", "default": 60, "min": 3, "max": 240,
         "hint": "Возврат случается за 1-240 минут"},
        {"key": "cooldown", "label": "Пауза после сделки (баров)", "type": "number", "default": 5, "min": 0, "max": 240},
        {"key": "flat_only", "label": "Только в боковике (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Прокол в тренде — продолжение хода, а не прокол"},
        {"key": "reg_win", "label": "Окно режима рынка (баров M1)", "type": "number", "default": 14400, "min": 1440, "max": 60000,
         "hint": "14400 ~ 10 торговых суток"},
        {"key": "reg_drift", "label": "Порог тренда ×10000 (300=3%)", "type": "number", "default": 300, "min": 50, "max": 1500,
         "hint": "Ход меньше порога = боковик. Калибруется по инструменту"},
        {"key": "qty", "label": "Контрактов на ступень", "type": "number", "default": 1, "min": 1, "max": 10},
        {"key": "invert", "label": "Инверсия (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "1 = вход ПО проколу вместо фейда"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
    ],
}
