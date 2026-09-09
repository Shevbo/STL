"""
Rich Fool — предоткрытийная лестница пробойных заявок.

Гипотеза оператора: за 10 минут до открытия сессии выставить серию СТОП-заявок
вверх и вниз ДАЛЕКО от вчерашнего закрытия (на долю амплитуды за N дней). Держать
заявки `hold_min` минут после открытия, неисполненные снять. Что исполнилось —
вести по классике: стоп = R, тейк = rr·R (по умолчанию 2:1). Позицию НЕ закрывать
в конце дня — только по TP/SL (носим овернайт).

Один сетап за раз: пока позиция открыта, новых лестниц не ставим. Первый пробой
запирает сторону; следующие ступени той же стороны в пределах окна доливают
(усреднение по лестнице). invert=1 → пробой вверх торгуем В ШОРТ, вниз — В ЛОНГ.

Бары бэктеста/ISS проштампованы МСК-стенным временем как UTC → bar_offset_min=0
(историческое поведение). Раннер агента строит истинно-UTC бары из ленты QUIK —
там deploy обязан передать bar_offset_min=180, иначе окно «07:00» встанет на 04:00.

Standalone-модуль (свой per-day конечный автомат) — фреймворку make_on_bar
состояние «сегодня вооружён / сторона заперта / ступеней набрано» держать негде.
Каждая развилка — параметр под перебор.
"""
from datetime import datetime, timezone

from trader.lab.runtime import STLRuntime


def _hm_day(t: int, offset_min: int = 0):
    """(минуты от полуночи МСК, YYYYMMDD) для эпохи бара."""
    d = datetime.fromtimestamp(t + offset_min * 60, tz=timezone.utc)
    return d.hour * 60 + d.minute, d.year * 10000 + d.month * 100 + d.day


def _reset_position_state(stl: STLRuntime) -> None:
    for k in ("dir", "R", "hit", "side_locked"):
        stl.set_state(k, 0)
    stl.set_state("day_done", 1)          # больше в этот день не входим


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"Rich Fool started | open={params.get('open_hour', 7)}:{params.get('open_min', 0):02d} "
        f"lead={params.get('place_lead_min', 10)}m hold={params.get('hold_min', 30)}m "
        f"N={params.get('n_days', 5)}d dist={params.get('dist_pct', 50)}% steps={params.get('step_count', 3)} "
        f"gap={params.get('step_gap_pct', 25)}% sl={params.get('sl_pct', 30)}% "
        f"rr={float(params.get('rr_x10', 20)) / 10:.1f}:1 invert={params.get('invert', 0)} symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    open_hm = int(params.get("open_hour", 7)) * 60 + int(params.get("open_min", 0))
    lead = int(params.get("place_lead_min", 10))
    hold = int(params.get("hold_min", 30))
    n_days = max(1, int(params.get("n_days", 5)))
    dist_pct = float(params.get("dist_pct", 50)) / 100.0        # 1-я ступень = amp·dist_pct
    step_count = max(1, int(params.get("step_count", 3)))
    step_gap_pct = float(params.get("step_gap_pct", 25)) / 100.0  # равный шаг (spacing=0) = amp·gap
    spacing = int(params.get("spacing", 0))                       # 0=лин, 1=убыв.шаг, 2=нараст.шаг
    span_pct = float(params.get("span_pct", 100)) / 100.0         # D = размах 1-й..последней ступени (spacing!=0)
    sl_pct = float(params.get("sl_pct", 30)) / 100.0             # риск R = amp·sl_pct
    rr = float(params.get("rr_x10", 20)) / 10.0
    vol_mult = float(params.get("vol_mult", 10)) / 10.0          # рост объёма ступени ×hit (10=1.0=ровно qty)
    max_contracts = max(1, int(params.get("max_contracts", 100)))  # жёсткий потолок позиции (защита от vol_mult^step_count)
    invert = int(params.get("invert", 0))
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
        # dir / R / hit / side_locked НЕ трогаем — позиция носится овернайт

    # Пояс безопасности: позиция есть, а состояния направления нет (перезапуск,
    # потеря state) — закрыть по рынку, а не доливать вслепую.
    if cur_qty != 0 and dirn == 0:
        await stl.place_order(symbol, "sell" if cur_qty > 0 else "buy", abs(cur_qty), cur.close)
        _reset_position_state(stl)
        return

    # 1) Ведение открытой позиции: только TP/SL, каждый бар, БЕЗ флэта в конце дня.
    if cur_qty != 0 and dirn != 0:
        avg = float(pos.avg_price)
        R = float(stl.get_state("R", 0) or 0)
        if R > 0 and avg > 0:
            if dirn > 0:
                if cur.low <= avg - R:
                    await stl.place_order(symbol, "sell", abs(cur_qty), avg - R)
                    _reset_position_state(stl)
                    return
                if cur.high >= avg + rr * R:
                    await stl.place_order(symbol, "sell", abs(cur_qty), avg + rr * R)
                    _reset_position_state(stl)
                    return
            else:
                if cur.high >= avg + R:
                    await stl.place_order(symbol, "buy", abs(cur_qty), avg + R)
                    _reset_position_state(stl)
                    return
                if cur.low <= avg - rr * R:
                    await stl.place_order(symbol, "buy", abs(cur_qty), avg - rr * R)
                    _reset_position_state(stl)
                    return
        # позиция открыта — долив по лестнице делаем ниже (только в окне)

    in_window = open_hm <= hm <= open_hm + hold

    # 2) Вооружение: один раз за день, на первом баре от (открытие − lead). Считаем
    #    вчерашнее закрытие и амплитуду за N завершённых дней по большому хвосту.
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
        # Смещения ступеней от вчерашнего закрытия. base = 1-я ступень.
        # spacing=0: равный шаг amp·step_gap_pct (прежнее поведение).
        # spacing!=0: НЕЛИНЕЙНАЯ прогрессия. D = полный размах 1-й..последней ступени
        #   = amp·span_pct. Веса промежутков w_k (k=1..step_count-1): 1 = убывающий шаг
        #   (w_k=1/k, плотнее ДАЛЬШЕ от цены), 2 = нарастающий (w_k=1/(N-k), реже дальше).
        #   gap_k = D·w_k/Σw — сумма промежутков ровно D.
        base = amp * dist_pct
        n = step_count
        if spacing == 0 or n <= 1:
            offs = [base + i * amp * step_gap_pct for i in range(n)]
        else:
            D = amp * span_pct
            w = [1.0 / k for k in range(1, n)] if spacing == 1 else [1.0 / (n - k) for k in range(1, n)]
            sw = sum(w) or 1.0
            gaps = [D * x / sw for x in w]
            offs, acc = [base], base
            for g in gaps:
                acc += g
                offs.append(acc)
        stl.set_state("levels_up", [prev_close + o for o in offs])
        stl.set_state("levels_dn", [prev_close - o for o in offs])
        stl.set_state("R", amp * sl_pct)
        stl.set_state("hit", 0)
        stl.set_state("side_locked", 0)
        stl.set_state("armed", 1)
        stl.log(f"armed {day}: prev_close={prev_close:.0f} amp={amp:.0f} "
                f"up={stl.get_state('levels_up')[0]:.0f}.. dn={stl.get_state('levels_dn')[0]:.0f}.. R={amp * sl_pct:.0f}")

    # 3) Исполнение ступеней внутри окна. Первый пробой запирает сторону; следующие
    #    ступени той же стороны доливают. Противоположная сторона после запирания
    #    мертва до конца дня.
    if stl.get_state("armed") and in_window:
        up = stl.get_state("levels_up") or []
        dn = stl.get_state("levels_dn") or []
        hit = int(stl.get_state("hit", 0) or 0)
        locked = int(stl.get_state("side_locked", 0) or 0)
        if hit < step_count:
            fire = 0
            if locked in (0, 1) and cur.high >= up[hit]:
                fire = 1
            elif locked in (0, -1) and cur.low <= dn[hit]:
                fire = -1
            if fire:
                trade_dir = -fire if invert else fire
                fresh = cur_qty == 0
                add = cur_qty != 0 and dirn == trade_dir
                allowed = (trade_dir > 0 and allow_long) or (trade_dir < 0 and allow_short)
                if allowed and (fresh or add):
                    px = up[hit] if fire > 0 else dn[hit]
                    step_qty = max(1, round(qty * (vol_mult ** hit)))   # ступень hit: qty·vol_mult^hit
                    step_qty = min(step_qty, max_contracts - abs(cur_qty))   # жёсткий потолок
                    if step_qty <= 0:
                        stl.set_state("hit", step_count)                # лестница упёрлась в потолок
                        return
                    await stl.place_order(symbol, "buy" if trade_dir > 0 else "sell", step_qty, px)
                    stl.set_state("hit", hit + 1)
                    stl.set_state("side_locked", fire)
                    stl.set_state("dir", trade_dir)
                else:
                    stl.set_state("side_locked", fire)   # сторона запрещена — заперли, не спамим
                return

    # 4) Окно ПРОШЛО, позиции нет — «снимаем заявки», день закрыт. Именно
    #    hm > open_hm+hold, а не "вне окна": арм-бар стоит ДО открытия и тоже вне
    #    окна — по "not in_window" он бы разоружился в ту же секунду.
    if stl.get_state("armed") and hm > open_hm + hold and cur_qty == 0:
        stl.set_state("armed", 0)
        stl.set_state("day_done", 1)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Rich Fool stopped")


STRATEGY_META = {
    "name": "Rich Fool — предоткрытийная лестница пробоя",
    "description": (
        "За place_lead_min минут до открытия сессии — серия стоп-заявок вверх и вниз "
        "на amp·dist_pct от вчерашнего закрытия, где amp = средний дневной размах за n_days. "
        "step_count ступеней с шагом amp·step_gap_pct. Заявки живут hold_min минут после "
        "открытия. Что исполнилось — стоп R=amp·sl_pct, тейк rr·R (2:1). Позиция носится "
        "овернайт до TP/SL, в конце дня не закрывается. invert=1 — фейд пробоя."
    ),
    "source": "гипотеза оператора 09.09.2026",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "FORTS тикер"},
        {"key": "n_days", "label": "N дней для амплитуды", "type": "number", "default": 5, "min": 1, "max": 30,
         "hint": "Сколько завершённых дней усредняем в дневной размах (high-low)"},
        {"key": "dist_pct", "label": "Отдаление 1-й ступени, % амплитуды", "type": "number", "default": 50, "min": 5, "max": 200,
         "hint": "Первый уровень = вчерашнее закрытие ± amp·dist_pct/100"},
        {"key": "step_count", "label": "Ступеней в лестнице", "type": "number", "default": 3, "min": 1, "max": 25,
         "hint": "Сколько стоп-заявок с каждой стороны"},
        {"key": "step_gap_pct", "label": "Шаг между ступенями, % амплитуды (при spacing=0)", "type": "number", "default": 25, "min": 0, "max": 100,
         "hint": "Равный шаг между соседними ступенями = amp·step_gap_pct/100. Игнорируется при spacing!=0"},
        {"key": "spacing", "label": "Прогрессия ступеней 0/1/2", "type": "number", "default": 0, "min": 0, "max": 2,
         "hint": "0=равный шаг. 1=убывающий шаг (ступени плотнее ДАЛЬШЕ от цены). 2=нарастающий шаг (реже дальше). При 1/2 общий размах задаёт span_pct"},
        {"key": "span_pct", "label": "Полный размах лестницы D, % амплитуды (при spacing!=0)", "type": "number", "default": 100, "min": 20, "max": 300,
         "hint": "Расстояние от 1-й до последней ступени = amp·span_pct/100. Промежутки делятся по прогрессии spacing"},
        {"key": "qty", "label": "Контрактов на ступень", "type": "number", "default": 1, "min": 1, "max": 10,
         "hint": "Объём одной сработавшей заявки"},
        {"key": "sl_pct", "label": "Риск R, % амплитуды", "type": "number", "default": 30, "min": 5, "max": 100,
         "hint": "Стоп-лосс от средней входа = amp·sl_pct/100"},
        {"key": "vol_mult", "label": "Рост объёма ступени ×10 (10=ровно, 20=×2)", "type": "number", "default": 10, "min": 10, "max": 40,
         "hint": "Объём ступени hit = qty·(vol_mult/10)^hit. 20 = каждая следующая ступень вдвое крупнее"},
        {"key": "max_contracts", "label": "Жёсткий потолок позиции", "type": "number", "default": 100, "min": 1, "max": 500,
         "hint": "Лестница перестаёт доливать при достижении этого числа контрактов (защита от vol_mult^step_count)"},
        {"key": "rr_x10", "label": "R:R ×10 (20=2:1)", "type": "number", "default": 20, "min": 5, "max": 50,
         "hint": "Тейк = rr × R от средней входа"},
        {"key": "invert", "label": "Инверсия (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "1 = пробой вверх торгуем в ШОРТ, пробой вниз в ЛОНГ (фейд)"},
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
