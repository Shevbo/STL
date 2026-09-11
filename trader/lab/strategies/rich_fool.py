"""
Rich Fool — предоткрытийная лестница контр-движения (фейд резкого импульса).

Гипотеза оператора 09.09.2026: за 10 минут до открытия сессии выставить серию
СТОП-заявок вверх и вниз от вчерашнего закрытия по формуле
  price(n) = price(n-1) + D/(n+1),  где D = amp (средний дневной размах за N дней).
Зазоры убывают: D/2, D/3, D/4, … Объём ступени растёт ×vol_mult.

Базовое направление (invert=0, рабочий режим):
  - Цена УСКАКАЛА ВВЕРХ (пересекла уровни up[]) → открываем ШОРТ и набираем
    позицию по уровням лестницы вверх (добавляем шорт на каждой следующей ступени).
  - Цена УСКАКАЛА ВНИЗ (пересекла уровни dn[]) → открываем ЛОНГ и набираем
    позицию по уровням лестницы вниз.

invert=1 — КОНТРОЛЬ обратной гипотезы (прямой пробой): вверх → лонг, вниз → шорт.
В переборе идёт как зеркальная ось: фейд обязан бить прямой пробой, иначе преимущества нет.

Стоп (SL): СТАНДАРТНЫЙ, в % ОТ ЦЕНЫ входа — sl_price_pct от средней (avg_price).
Тейк (TP): ТРЕЙЛИНГ. Следим за экстремумом в нашу пользу (MIN для шорта, MAX для
лонга) и выходим при откате на trail_tp_pct % от цены. Трейлинг срабатывает ТОЛЬКО
в прибыли: откат от экстремума не должен резать позицию в убыток — убыток это дело
стопа. Оба уровня в % от цены, поэтому сетка переносится между инструментами.

Позиция НЕ закрывается принудительно в конце дня — держится до TP/SL, в том
числе овернайт. Неисполненные заявки лестницы снимаются через hold_min минут
после открытия. Усреднения ПРОТИВ движения нет — лестница добирает только по
стороне пробоя.
"""
from datetime import datetime, timezone

from trader.lab.runtime import STLRuntime


def _hm_day(t: int, offset_min: int = 0):
    """(минуты от полуночи МСК, YYYYMMDD) для эпохи бара."""
    d = datetime.fromtimestamp(t + offset_min * 60, tz=timezone.utc)
    return d.hour * 60 + d.minute, d.year * 10000 + d.month * 100 + d.day


def _reset_position_state(stl: STLRuntime) -> None:
    for k in ("dir", "hit", "side_locked", "tp_peak"):
        stl.set_state(k, 0)
    stl.set_state("day_done", 1)          # больше в этот день не входим


async def on_start(stl: STLRuntime, params: dict) -> None:
    tp_mode = f"trail_tp={float(params.get('trail_tp_pct', 50)) / 100:.2f}%"
    stl.log(
        f"Rich Fool started | open={params.get('open_hour', 7)}:{params.get('open_min', 0):02d} "
        f"lead={params.get('place_lead_min', 10)}m hold={params.get('hold_min', 30)}m "
        f"N={params.get('n_days', 5)}d steps={params.get('step_count', 3)} "
        f"sl={float(params.get('sl_price_pct', 100)) / 100:.2f}% tp={tp_mode} "
        f"symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    open_hm = int(params.get("open_hour", 7)) * 60 + int(params.get("open_min", 0))
    lead = int(params.get("place_lead_min", 10))
    hold = int(params.get("hold_min", 30))
    n_days = max(1, int(params.get("n_days", 5)))
    step_count = max(1, int(params.get("step_count", 3)))
    sl_price_pct = float(params.get("sl_price_pct", 100)) / 10000.0   # СТОП = % от цены (100 = 1.00%)
    trail_tp_pct = float(params.get("trail_tp_pct", 50)) / 10000.0    # ТРЕЙЛИНГ-ТЕЙК = % от цены (50 = 0.50%)
    vol_mult = float(params.get("vol_mult", 10)) / 10.0          # рост объёма ступени ×hit (10=1.0=ровно qty)
    max_contracts = max(1, int(params.get("max_contracts", 100)))  # жёсткий потолок позиции
    invert = int(params.get("invert", 0))                        # 1 = прямой пробой (тест), 0 = фейд (рабочий)
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))
    bar_off = int(params.get("bar_offset_min", 0))

    bars = await stl.get_bars(symbol, tf=1, n=max(5, hold + 5))
    if len(bars) < 3:
        return
    cur = bars[-1]
    hm, day = _hm_day(cur.time, bar_off)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
    dirn = int(stl.get_state("dir", 0) or 0)

    if stl.get_state("day") != day:                     # новый торговый день
        stl.set_state("day", day)
        stl.set_state("armed", 0)
        stl.set_state("day_done", 0)
        stl.set_state("levels_up", None)
        stl.set_state("levels_dn", None)
        # dir / R / hit / side_locked / tp_peak НЕ трогаем — позиция носится овернайт

    # Пояс безопасности: позиция есть, а состояния направления нет (перезапуск,
    # потеря state) — закрыть по рынку, а не доливать вслепую.
    if cur_qty != 0 and dirn == 0:
        await stl.place_order(symbol, "sell" if cur_qty > 0 else "buy", abs(cur_qty), cur.close)
        _reset_position_state(stl)
        return

    # 1) Ведение открытой позиции: TP/SL, каждый бар. Филл ТОЧНЫЙ (place_order_at).
    #    Гэп сквозь ФИКСИРОВАННЫЙ уровень исполняем по open (проскальзывание).
    #    Trail TP — движущийся уровень, гэп-логика к нему НЕ применяется.
    #    Внутри бара порядок пессимистичный: сперва стоп.
    if cur_qty != 0 and dirn != 0:
        avg = float(pos.avg_price)
        if avg > 0:
            R = avg * sl_price_pct                  # стоп в пунктах = % от цены входа
            sl_px = avg - R if dirn > 0 else avg + R
            gap_sl = cur.open <= sl_px if dirn > 0 else cur.open >= sl_px
            hit_sl = cur.low <= sl_px if dirn > 0 else cur.high >= sl_px

            # Трейлинг-тейк: экстремум В НАШУ ПОЛЬЗУ с момента входа. Стартует от
            # средней (а не от нуля-сентинела) — иначе первый же бар задал бы peak
            # хуже входа и тейк поехал бы в убыток.
            peak = float(stl.get_state("tp_peak", 0) or 0) or avg
            tp_px = peak - avg * trail_tp_pct if dirn > 0 else peak + avg * trail_tp_pct
            # ТОЛЬКО в прибыли: иначе трейлинг превратился бы во второй стоп.
            armed = (tp_px > avg) if dirn > 0 else (tp_px < avg)
            hit_tp = armed and (cur.low <= tp_px if dirn > 0 else cur.high >= tp_px)
            # Экстремум обновляем ПОСЛЕ проверки: иначе один и тот же бар и задавал бы
            # пик своим low/high, и выбивал бы тейк другим концом — заглядывание внутрь
            # бара, порядок тиков в котором неизвестен.
            stl.set_state("tp_peak", max(peak, cur.high) if dirn > 0 else min(peak, cur.low))

            # Пессимистично: стоп раньше тейка. Гэп сквозь СТОП — по open (скольжение);
            # к движущемуся трейлингу гэп-логика не применяется.
            exit_px = cur.open if gap_sl else (sl_px if hit_sl else (tp_px if hit_tp else None))
            if exit_px is not None:
                await stl.place_order_at(symbol, "sell" if dirn > 0 else "buy",
                                         abs(cur_qty), exit_px, cur.time)
                _reset_position_state(stl)
                return
        # позиция открыта — долив по лестнице делаем ниже (только в окне)

    in_window = open_hm <= hm <= open_hm + hold

    # 2) Вооружение: один раз за день, на первом баре от (открытие − lead).
    #    Считаем вчерашнее закрытие и амплитуду за N завершённых дней.
    if (not stl.get_state("armed") and not stl.get_state("day_done")
            and cur_qty == 0 and hm >= open_hm - lead):
        big = await stl.get_bars(symbol, tf=1, n=(n_days + 2) * 1500)
        by_day: dict[int, list] = {}
        for b in big:
            _, bd = _hm_day(b.time, bar_off)
            by_day.setdefault(bd, []).append(b)
        prior = sorted(d for d in by_day if d < day)
        if len(prior) < n_days:
            stl.set_state("day_done", 1)                # истории мало — пропускаем день
            return
        prev_close = by_day[prior[-1]][-1].close
        rngs = [max(x.high for x in by_day[d]) - min(x.low for x in by_day[d])
                for d in prior[-n_days:]]
        amp = sum(rngs) / len(rngs)
        if amp <= 0 or prev_close <= 0:
            stl.set_state("day_done", 1)
            return
        # Смещения ступеней от вчерашнего закрытия. Формула оператора:
        #   price(0) = prev_close, price(n) = price(n-1) + amp/(n+1).
        #   Зазоры убывают: amp/2, amp/3, amp/4, … Смещение n = amp·Σ 1/(k+1).
        offs, acc = [], 0.0
        for j in range(1, step_count + 1):
            acc += amp * (1.0 / (j + 1))
            offs.append(acc)
        stl.set_state("levels_up", [prev_close + o for o in offs])
        stl.set_state("levels_dn", [prev_close - o for o in offs])
        stl.set_state("hit", 0)
        stl.set_state("side_locked", 0)
        stl.set_state("tp_peak", 0)
        stl.set_state("armed", 1)
        stl.log(f"armed {day}: prev_close={prev_close:.0f} amp={amp:.0f} "
                f"шорт-лестница от {stl.get_state('levels_up')[0]:.0f}, "
                f"лонг-лестница от {stl.get_state('levels_dn')[0]:.0f}")

    # 3) Исполнение ступеней внутри окна. Первый пробой запирает сторону; следующие
    #    ступени той же стороны доливают. Противоположная сторона после запирания
    #    мертва до конца дня.
    if stl.get_state("armed") and not stl.get_state("day_done") and in_window:
        up = stl.get_state("levels_up") or []
        dn = stl.get_state("levels_dn") or []
        hit = int(stl.get_state("hit", 0) or 0)
        locked = int(stl.get_state("side_locked", 0) or 0)
        # Один бар может пересечь сразу несколько ступеней — исполняем ВСЕ, а не первую.
        while hit < step_count:
            fire = 0
            if locked in (0, 1) and cur.high >= up[hit]:
                fire = 1
            elif locked in (0, -1) and cur.low <= dn[hit]:
                fire = -1
            if not fire:
                break
            # Базовое направление: invert=0 -> fire=1(верх) -> ШОРТ (trade_dir=-1)
            #                   fire=-1(низ)  -> ЛОНГ (trade_dir=+1)
            # invert=1 реверсирует (прямой пробой)
            trade_dir = -fire if invert == 0 else fire
            fresh = cur_qty == 0
            add = cur_qty != 0 and dirn == trade_dir
            allowed = (trade_dir > 0 and allow_long) or (trade_dir < 0 and allow_short)
            if not (allowed and (fresh or add)):
                stl.set_state("side_locked", fire)   # сторона запрещена/чужая — заперли
                break
            step_qty = max(1, round(qty * (vol_mult ** hit)))   # ступень hit: qty·vol_mult^hit
            step_qty = min(step_qty, max_contracts - abs(cur_qty))   # жёсткий потолок
            if step_qty <= 0:
                stl.set_state("hit", step_count)                # лестница упёрлась в потолок
                break
            level = up[hit] if fire > 0 else dn[hit]
            # Стоп-филл: по цене уровня; при гэпе через уровень — по цене открытия бара.
            fill_px = max(level, cur.open) if fire > 0 else min(level, cur.open)
            await stl.place_order_at(symbol, "buy" if trade_dir > 0 else "sell",
                                     step_qty, fill_px, cur.time)
            stl.set_state("hit", hit + 1)
            stl.set_state("side_locked", fire)
            stl.set_state("dir", trade_dir)
            dirn = trade_dir
            cur_qty += step_qty if trade_dir > 0 else -step_qty
            hit += 1

    # 4) Окно ПРОШЛО, позиции нет — «снимаем заявки», день закрыт.
    if stl.get_state("armed") and hm > open_hm + hold and cur_qty == 0:
        stl.set_state("armed", 0)
        stl.set_state("day_done", 1)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Rich Fool stopped")


STRATEGY_META = {
    "name": "Rich Fool — предоткрытийная лестница фейда импульса",
    "description": (
        "За place_lead_min минут до открытия сессии — серия стоп-заявок вверх и вниз "
        "от вчерашнего закрытия по формуле price(n)=price(n-1)+D/(n+1), D=amp (средний "
        "дневной размах за n_days). Зазоры убывают: D/2, D/3, D/4, … Объём ступени растёт "
        "×vol_mult. Заявки живут hold_min минут после открытия. Базовое направление (invert=0): "
        "цена ускакала ВВЕРХ → ШОРТ и набор позиции по уровням лестницы вверх; ускакала ВНИЗ → "
        "ЛОНГ и набор по уровням вниз (фейд импульса с возвратом к средней). "
        "Стоп — СТАНДАРТНЫЙ, sl_price_pct % ОТ ЦЕНЫ входа. Тейк — ТРЕЙЛИНГ: экстремум в нашу "
        "пользу минус trail_tp_pct % от цены, срабатывает только в прибыли. "
        "Позиция носится овернайт до TP/SL. invert=1 — контроль обратной гипотезы (прямой пробой)."
    ),
    "source": "гипотеза оператора 09.09.2026",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "n_days", "label": "N дней для амплитуды", "type": "number", "default": 5, "min": 1, "max": 30,
         "hint": "Сколько завершённых дней усредняем в дневной размах (high-low)"},
        {"key": "step_count", "label": "Ступеней в лестнице", "type": "number", "default": 3, "min": 1, "max": 25,
         "hint": "Сколько стоп-заявок с каждой стороны. Уровни: prev_close ± amp·(1/2+1/3+…)"},
        {"key": "qty", "label": "Контрактов на ступень", "type": "number", "default": 1, "min": 1, "max": 10,
         "hint": "Объём одной сработавшей заявки"},
        {"key": "sl_price_pct", "label": "Стоп-лосс, % от цены ×100 (100 = 1.00%)", "type": "number",
         "default": 100, "min": 10, "max": 500,
         "hint": "Стандартный стоп: расстояние = средняя цена входа × sl_price_pct/10000. 100 = 1.00% от цены"},
        {"key": "trail_tp_pct", "label": "Трейлинг-тейк, % от цены ×100 (50 = 0.50%)", "type": "number",
         "default": 50, "min": 10, "max": 500,
         "hint": "Выход при откате от экстремума в нашу пользу на trail_tp_pct/10000 от цены. Срабатывает только в прибыли"},
        {"key": "vol_mult", "label": "Рост объёма ступени ×10 (10=ровно, 20=×2)", "type": "number", "default": 10, "min": 10, "max": 40,
         "hint": "Объём ступени hit = qty·(vol_mult/10)^hit. 20 = каждая следующая ступень вдвое крупнее"},
        {"key": "max_contracts", "label": "Жёсткий потолок позиции", "type": "number", "default": 100, "min": 1, "max": 500,
         "hint": "Лестница перестаёт доливать при достижении этого числа контрактов (защита от vol_mult^step_count)"},
        {"key": "invert", "label": "Инверсия (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "0 = фейд (рабочий): вверх=шорт, вниз=лонг. 1 = прямой пробой (только для теста)"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить сделки в лонг"},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить сделки в шорт"},
        {"key": "open_hour", "label": "Час открытия (МСК)", "type": "number", "default": 7, "min": 0, "max": 23,
         "hint": "Якорь открытия сессии. FORTS утро = 07:00 МСК"},
        {"key": "open_min", "label": "Минута открытия", "type": "number", "default": 0, "min": 0, "max": 59, "hint": "Обычно 0"},
        {"key": "place_lead_min", "label": "За сколько минут до открытия ставим", "type": "number", "default": 10, "min": 0, "max": 60,
         "hint": "Инфраструктурный: в бэктесте баров раньше 07:00 нет, вооружаемся первым баром сессии"},
        {"key": "hold_min", "label": "Держим заявки, мин после открытия", "type": "number", "default": 30, "min": 5, "max": 180,
         "hint": "Окно, в котором заявки живые. После — неисполненные снимаем"},
    ],
}
