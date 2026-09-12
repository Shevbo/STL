"""
Rich Fool — предоткрытийная лестница фейда импульса ОТКРЫТИЯ биржи.

СУТЬ (спецификация оператора, уточнена 12.09.2026). Смысл строго в открытии: за
place_lead_min минут ДО открытия сессии выставляется лестница заявок по обе стороны
от вчерашнего закрытия. Импульс открытия фейдится — цена ускакала вверх, робот
продаёт; ускакала вниз — покупает, добирая позицию на каждой следующей ступени.

Уровни:  price(0) = вчерашнее закрытие,  price(n) = price(n-1) + D/(n+1),
         D = amp × d_coef,  amp = средний дневной размах high-low за n_days.
         Зазоры убывают (D/2, D/3, D/4 …) — ступени гуще по мере удаления.
         d_coef ∈ [0.2, 1.0] сужает лестницу: при 0.2 вся она укладывается в
         пятую часть амплитуды, при 1.0 растягивается на всю.

Объём:   ОДИН множитель с дробной частью к ПРЕДЫДУЩЕЙ заявке —
         qty(n) = qty(n-1) × vol_mult. Потолок позиции max_contracts.

Стоп:    от СРЕДНЕЙ цены позиции и ОБЯЗАТЕЛЬНО за пределами всех заявок лестницы
         (дальше последней ступени), иначе стоп выбивал бы позицию раньше, чем
         лестница успевает добрать, и step_count становился бы бутафорией.
         sl_price_pct — запас В ПРОЦЕНТАХ ОТ ЦЕНЫ за последней ступенью.

Тейк:    ТРЕЙЛИНГ — откат на trail_tp_pct % от цены от экстремума в нашу пользу.
         Только в прибыли: откат не имеет права закрыть позицию хуже входа.

Снятие заявок: через hold_min минут после открытия неисполненные заявки снимаются
         ТОЛЬКО если за это время не было НИ ОДНОЙ сделки. Если лестница начала
         набирать — она продолжает работать, пока позиция жива.

ОВЕРНАЙТ ЗАПРЕЩЁН. Каждое утро нужна новая лестница, поэтому позиция не может
         ночевать. За exit_lead_min минут до закрытия включается выход по сигналу
         двух EMA (ema_fast/ema_slow): сигнал против позиции — выходим. На закрытии
         сессии позиция закрывается принудительно в любом случае.

Сессии выводятся ИЗ ИСТОРИИ, а не прописаны числом: расписание FORTS менялось
         внутри периода прогона (у RIM6 в марте-июне НИ ОДНОГО дня с баром 07:00,
         будни открывались в 09:00; у RIU6 с июня — 06:59/07:00). Поэтому
         ОТКРЫТИЕ = первый бар дня, ЗАКРЫТИЕ = последний бар предыдущего дня того
         же типа. Запасные значения, если истории нет: будни 23:50, выходные 19:00
         (FORTS торгует в выходные с 10:00 до 19:00). Час открытия НЕ параметр —
         выставить его неверно невозможно.

Бары бэктеста/ISS проштампованы МСК-стенным временем как UTC → bar_offset_min=0.
Раннер агента строит истинно-UTC бары из ленты QUIK — там deploy обязан передать
bar_offset_min=180, иначе окно «07:00» встанет на 04:00.

Прогон ТОЛЬКО ПО КОНТРАКТАМ (RIU6, а не RI): непрерывная серия сшита через перекат
без выравнивания базиса, и позиция, пережившая шов, даёт фантомный результат —
01.07.2026 шов RI составил −13.01%, и на нём одном «лидер» сделал 96% итога.

Standalone-модуль: фреймворку make_on_bar состояние «вооружён / сторона заперта /
ступеней набрано / экстремум» держать негде.
"""
from datetime import datetime, timezone

from trader.lab.runtime import STLRuntime

# ЗАПАСНОЕ время закрытия сессии (минуты от полуночи МСК) — используется только
# если в истории нет ни одного предыдущего дня того же типа. Рабочее значение
# всегда берётся из данных.
_FALLBACK_CLOSE_WEEKDAY = 23 * 60 + 50          # 23:50, вечерний клиринг
_FALLBACK_CLOSE_WEEKEND = 19 * 60               # 19:00


def _bar_clock(t: int, offset_min: int = 0):
    """(минуты от полуночи МСК, YYYYMMDD, выходной?) для эпохи бара."""
    d = datetime.fromtimestamp(t + offset_min * 60, tz=timezone.utc)
    return (d.hour * 60 + d.minute,
            d.year * 10000 + d.month * 100 + d.day,
            d.weekday() >= 5)


def _ema(values: list[float], period: int) -> float | None:
    """Классическая EMA по списку закрытий. None, если истории мало."""
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period           # seed простым средним
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


def _reset_position_state(stl: STLRuntime) -> None:
    """Позиция закрыта: гасим направление, лестницу и экстремум трейлинга."""
    for k in ("dir", "hit", "side_locked", "tp_peak"):
        stl.set_state(k, 0)
    stl.set_state("day_done", 1)          # в этот день новую лестницу не ставим


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"Rich Fool started | lead={params.get('place_lead_min', 10)}m "
        f"hold={params.get('hold_min', 30)}m N={params.get('n_days', 5)}d "
        f"D={float(params.get('d_coef', 100)) / 100:.2f}×amp "
        f"steps={params.get('step_count', 3)} vol×{float(params.get('vol_mult', 10)) / 10:.1f} "
        f"sl_buf={float(params.get('sl_price_pct', 100)) / 100:.2f}% "
        f"trail={float(params.get('trail_tp_pct', 50)) / 100:.2f}% "
        f"ema={params.get('ema_fast', 9)}/{params.get('ema_slow', 21)} "
        f"exit_lead={params.get('exit_lead_min', 120)}m "
        f"invert={params.get('invert', 0)} symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    qty = max(1, int(params.get("qty", 1)))
    hold = int(params.get("hold_min", 30))
    n_days = max(1, int(params.get("n_days", 5)))
    d_coef = float(params.get("d_coef", 100)) / 100.0          # 20..100 -> 0.2..1.0
    step_count = max(1, int(params.get("step_count", 3)))
    vol_mult = float(params.get("vol_mult", 10)) / 10.0        # множитель к ПРЕДЫДУЩЕЙ заявке
    sl_price_pct = float(params.get("sl_price_pct", 100)) / 10000.0   # запас за лестницей
    trail_tp_pct = float(params.get("trail_tp_pct", 50)) / 10000.0
    max_contracts = max(1, int(params.get("max_contracts", 100)))
    ema_fast = max(2, int(params.get("ema_fast", 9)))
    ema_slow = max(ema_fast + 1, int(params.get("ema_slow", 21)))
    exit_lead = max(0, int(params.get("exit_lead_min", 120)))
    invert = int(params.get("invert", 0))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))
    bar_off = int(params.get("bar_offset_min", 0))

    # Хвост: хватает и на окно удержания, и на обе EMA.
    need = max(hold + 5, ema_slow * 3 + 5)
    bars = await stl.get_bars(symbol, tf=1, n=need)
    if len(bars) < 3:
        return
    cur = bars[-1]
    hm, day, is_weekend = _bar_clock(cur.time, bar_off)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)
    dirn = int(stl.get_state("dir", 0) or 0)

    if stl.get_state("day") != day:                     # новый торговый день
        stl.set_state("day", day)
        stl.set_state("armed", 0)
        stl.set_state("day_done", 0)
        stl.set_state("levels_up", None)
        stl.set_state("levels_dn", None)
        stl.set_state("win_end", 0)          # конец окна набора, минуты МСК
        stl.set_state("close_hm", 0)         # ожидаемое закрытие сессии
        # ЖЁСТКАЯ ГАРАНТИЯ ЗАПРЕТА ОВЕРНАЙТА. Ожидаемое закрытие — оценка по
        # истории, и она может оказаться ПОЗЖЕ фактического последнего бара (день
        # закончился раньше обычного). Тогда позиция дожила бы до утра и заблокировала
        # новую лестницу. Поэтому на первом же баре нового дня остаток закрывается
        # по цене открытия — это самый ранний момент, когда это вообще возможно.
        if cur_qty != 0:
            await stl.place_order_at(symbol, "sell" if cur_qty > 0 else "buy",
                                     abs(cur_qty), cur.open, cur.time)
            _reset_position_state(stl)
            stl.set_state("day_done", 0)     # день только начался: лестницу ставим
            return
        # dir / hit / side_locked / tp_peak не трогаем: позиция может быть открыта
        # внутри этого же дня, а через ночь её не бывает (овернайт запрещён).

    # Пояс безопасности: позиция есть, а состояния направления нет (перезапуск,
    # потеря state) — закрыть по рынку, а не доливать вслепую.
    if cur_qty != 0 and dirn == 0:
        await stl.place_order(symbol, "sell" if cur_qty > 0 else "buy", abs(cur_qty), cur.close)
        _reset_position_state(stl)
        return

    # ── 1. Ведение открытой позиции ───────────────────────────────────────────
    # Порядок проверок — от самого жёсткого к самому мягкому: принудительное
    # закрытие сессии, стоп, сигнал двух EMA, трейлинг-тейк.
    if cur_qty != 0 and dirn != 0:
        avg = float(pos.avg_price)
        side = "sell" if dirn > 0 else "buy"
        close_hm = int(stl.get_state("close_hm", 0) or 0) or (
            _FALLBACK_CLOSE_WEEKEND if is_weekend else _FALLBACK_CLOSE_WEEKDAY)

        # 1a. ОВЕРНАЙТ ЗАПРЕЩЁН: на закрытии сессии выходим безусловно.
        if hm >= close_hm:
            await stl.place_order_at(symbol, side, abs(cur_qty), cur.close, cur.time)
            _reset_position_state(stl)
            return

        if avg > 0:
            # 1b. СТОП — от средней, но ЗА ПРЕДЕЛАМИ последней ступени лестницы.
            #     Иначе стоп срабатывал бы внутри лестницы и обрывал набор.
            up = stl.get_state("levels_up") or []
            dn = stl.get_state("levels_dn") or []
            buf = avg * sl_price_pct
            if dirn > 0:                                 # ЛОНГ: лестница вниз
                far = min([avg] + [float(x) for x in dn])
                sl_px = far - buf
            else:                                        # ШОРТ: лестница вверх
                far = max([avg] + [float(x) for x in up])
                sl_px = far + buf
            gap_sl = cur.open <= sl_px if dirn > 0 else cur.open >= sl_px
            hit_sl = cur.low <= sl_px if dirn > 0 else cur.high >= sl_px
            if gap_sl or hit_sl:
                # Гэп сквозь стоп — по открытию бара (проскальзывание), иначе по уровню.
                await stl.place_order_at(symbol, side, abs(cur_qty),
                                         cur.open if gap_sl else sl_px, cur.time)
                _reset_position_state(stl)
                return

            # 1c. ВЫХОД ПО ДВУМ EMA в последние exit_lead минут сессии.
            if exit_lead and hm >= close_hm - exit_lead:
                closes = [b.close for b in bars]
                ef, es = _ema(closes, ema_fast), _ema(closes, ema_slow)
                if ef is not None and es is not None:
                    against = (ef < es) if dirn > 0 else (ef > es)
                    if against:
                        await stl.place_order_at(symbol, side, abs(cur_qty), cur.close, cur.time)
                        _reset_position_state(stl)
                        return

            # 1d. ТРЕЙЛИНГ-ТЕЙК от экстремума в нашу пользу, только в прибыли.
            peak = float(stl.get_state("tp_peak", 0) or 0) or avg
            tp_px = peak - avg * trail_tp_pct if dirn > 0 else peak + avg * trail_tp_pct
            armed = (tp_px > avg) if dirn > 0 else (tp_px < avg)
            if armed and (cur.low <= tp_px if dirn > 0 else cur.high >= tp_px):
                await stl.place_order_at(symbol, side, abs(cur_qty), tp_px, cur.time)
                _reset_position_state(stl)
                return
            # Экстремум обновляем ПОСЛЕ проверки: иначе один бар и задавал бы пик
            # своим low, и выбивал бы тейк своим high — заглядывание внутрь бара,
            # порядок тиков в котором неизвестен.
            stl.set_state("tp_peak", max(peak, cur.high) if dirn > 0 else min(peak, cur.low))

    # ── 2. Вооружение: на ПЕРВОМ баре дня ────────────────────────────────────
    # Лестница считается от ВЧЕРАШНЕГО закрытия, значит её можно выставить до
    # открытия — и она имеет право исполниться уже на первом баре сессии. Именно
    # это и означает «заявки стоят за place_lead_min минут до открытия»: сам
    # place_lead_min на результат бэктеста не влияет, он нужен живому раннеру.
    if (not stl.get_state("armed") and not stl.get_state("day_done")
            and cur_qty == 0):
        big = await stl.get_bars(symbol, tf=1, n=(n_days + 2) * 1500)
        by_day: dict[int, list] = {}
        day_kind: dict[int, bool] = {}
        for b in big:
            _, bd, bw = _bar_clock(b.time, bar_off)
            by_day.setdefault(bd, []).append(b)
            day_kind[bd] = bw
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
        # D сужается коэффициентом: D = amp × d_coef. Смещение ступени n = D·Σ1/(k+1).
        D = amp * d_coef
        offs, acc = [], 0.0
        for j in range(1, step_count + 1):
            acc += D / (j + 1)
            offs.append(acc)
        stl.set_state("levels_up", [prev_close + o for o in offs])
        stl.set_state("levels_dn", [prev_close - o for o in offs])
        stl.set_state("hit", 0)
        stl.set_state("side_locked", 0)
        stl.set_state("tp_peak", 0)
        # Границы сессии ИЗ ИСТОРИИ: закрытие = последний бар предыдущего дня того
        # же типа. Расписание FORTS менялось внутри периода, прописанный час дал бы
        # «открытие» в середине дня на половине истории.
        cl = 0
        for d in reversed(prior):
            if day_kind.get(d) == is_weekend:
                cl = max(_bar_clock(b.time, bar_off)[0] for b in by_day[d])
                break
        stl.set_state("close_hm", cl or (_FALLBACK_CLOSE_WEEKEND if is_weekend
                                        else _FALLBACK_CLOSE_WEEKDAY))
        stl.set_state("win_end", hm + hold)     # окно набора от первого бара дня
        stl.set_state("armed", 1)
        stl.log(f"armed {day} ({'вых' if is_weekend else 'буд'}, первый бар "
                f"{hm // 60:02d}:{hm % 60:02d}, закрытие сессии "
                f"{int(stl.get_state('close_hm')) // 60:02d}:"
                f"{int(stl.get_state('close_hm')) % 60:02d}): вчера={prev_close:.0f} "
                f"amp={amp:.0f} D={D:.0f} шорт-лестница от {prev_close + offs[0]:.0f}, "
                f"лонг-лестница от {prev_close - offs[0]:.0f}")

    # ── 3. Исполнение ступеней ────────────────────────────────────────────────
    # Окно живёт hold_min минут после открытия. НО если лестница уже что-то
    # набрала, она продолжает работать и после окна — снимаются только заявки,
    # по которым не было НИ ОДНОЙ сделки.
    hit = int(stl.get_state("hit", 0) or 0)
    in_window = hm <= int(stl.get_state("win_end", 0) or 0)
    if stl.get_state("armed") and not stl.get_state("day_done") and (in_window or hit > 0):
        up = stl.get_state("levels_up") or []
        dn = stl.get_state("levels_dn") or []
        locked = int(stl.get_state("side_locked", 0) or 0)
        # Один бар может пересечь сразу несколько ступеней — исполняем ВСЕ.
        while hit < step_count:
            fire = 0
            if locked in (0, 1) and cur.high >= up[hit]:
                fire = 1
            elif locked in (0, -1) and cur.low <= dn[hit]:
                fire = -1
            if not fire:
                break
            # Фейд: цена вверх (fire=+1) -> ШОРТ. invert=1 — контрольный пробой.
            trade_dir = -fire if invert == 0 else fire
            fresh = cur_qty == 0
            add = cur_qty != 0 and dirn == trade_dir
            allowed = (trade_dir > 0 and allow_long) or (trade_dir < 0 and allow_short)
            if not (allowed and (fresh or add)):
                stl.set_state("side_locked", fire)   # сторона запрещена/чужая — заперли
                break
            # Объём: ОДИН дробный множитель к предыдущей заявке.
            step_qty = max(1, round(qty * (vol_mult ** hit)))
            step_qty = min(step_qty, max_contracts - abs(cur_qty))
            if step_qty <= 0:
                stl.set_state("hit", step_count)     # лестница упёрлась в потолок
                break
            level = up[hit] if fire > 0 else dn[hit]
            # Заявка лимитная: встречает цену на уровне. Гэп сквозь уровень —
            # исполнение по открытию бара (цена лучше уровня, это честно для лимитки).
            fill_px = max(level, cur.open) if fire > 0 else min(level, cur.open)
            await stl.place_order_at(symbol, "buy" if trade_dir > 0 else "sell",
                                     step_qty, fill_px, cur.time)
            hit += 1
            stl.set_state("hit", hit)
            stl.set_state("side_locked", fire)
            stl.set_state("dir", trade_dir)
            dirn = trade_dir
            cur_qty += step_qty if trade_dir > 0 else -step_qty

    # ── 4. Снятие заявок: окно прошло И НИ ОДНОЙ сделки не было ───────────────
    if (stl.get_state("armed") and hm > int(stl.get_state("win_end", 0) or 0)
            and cur_qty == 0 and int(stl.get_state("hit", 0) or 0) == 0):
        stl.set_state("armed", 0)
        stl.set_state("day_done", 1)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Rich Fool stopped")


STRATEGY_META = {
    "name": "Rich Fool — предоткрытийная лестница фейда импульса",
    "description": (
        "За place_lead_min минут ДО открытия биржи — лестница заявок по обе стороны от "
        "вчерашнего закрытия: price(n)=price(n-1)+D/(n+1), D=amp·d_coef, amp — средний "
        "дневной размах за n_days. Зазоры убывают (D/2, D/3, D/4 …). Цена ускакала ВВЕРХ — "
        "ШОРТ с добором по ступеням вверх, ВНИЗ — ЛОНГ с добором вниз (фейд импульса "
        "открытия). Объём: один дробный множитель vol_mult к предыдущей заявке, потолок "
        "max_contracts. Стоп от средней и ЗА последней ступенью лестницы (запас "
        "sl_price_pct % от цены). Тейк — трейлинг trail_tp_pct % от экстремума, только в "
        "прибыли. Через hold_min минут неисполненные заявки снимаются, только если сделок "
        "не было вовсе. ОВЕРНАЙТ ЗАПРЕЩЁН: за exit_lead_min до закрытия выход по сигналу "
        "двух EMA, на закрытии — принудительно. Границы сессии берутся ИЗ ИСТОРИИ: открытие = "
        "первый бар дня, закрытие = последний бар предыдущего дня того же типа (расписание "
        "FORTS менялось внутри периода). invert=1 — контроль (прямой пробой)."
    ),
    "source": "гипотеза оператора 09.09.2026, спецификация уточнена 12.09.2026",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6",
         "hint": "ТОЛЬКО конкретный контракт (RIU6), не базовый код: на непрерывной серии шов переката даёт фантомный результат"},
        {"key": "n_days", "label": "N дней для амплитуды", "type": "number", "default": 5, "min": 1, "max": 30,
         "hint": "Сколько завершённых дней усредняем в дневной размах (high-low)"},
        {"key": "d_coef", "label": "Коэффициент к D, % ×100 (100 = 1.00×amp)", "type": "number",
         "default": 100, "min": 20, "max": 100,
         "hint": "Сужает лестницу: D = amp × d_coef/100. При 20 вся лестница укладывается в пятую часть амплитуды"},
        {"key": "step_count", "label": "Ступеней в лестнице", "type": "number", "default": 3, "min": 1, "max": 25,
         "hint": "Сколько заявок с каждой стороны. Смещение ступени n = D·(1/2+1/3+…+1/(n+1))"},
        {"key": "qty", "label": "Контрактов на первую ступень", "type": "number", "default": 1, "min": 1, "max": 50,
         "hint": "Объём первой сработавшей заявки"},
        {"key": "vol_mult", "label": "Множитель объёма ×10 (10=1.0, 13=1.3)", "type": "number",
         "default": 10, "min": 10, "max": 30,
         "hint": "Объём каждой следующей заявки = предыдущая × vol_mult/10. Один дробный множитель, без прогрессий"},
        {"key": "max_contracts", "label": "Жёсткий потолок позиции", "type": "number", "default": 100, "min": 1, "max": 500,
         "hint": "Лестница перестаёт доливать на этом числе контрактов"},
        {"key": "sl_price_pct", "label": "Запас стопа за лестницей, % ×100", "type": "number",
         "default": 100, "min": 10, "max": 500,
         "hint": "Стоп ставится ЗА последней ступенью лестницы плюс средняя цена × sl_price_pct/10000"},
        {"key": "trail_tp_pct", "label": "Трейлинг-тейк, % от цены ×100", "type": "number",
         "default": 50, "min": 10, "max": 500,
         "hint": "Выход при откате от экстремума в нашу пользу на trail_tp_pct/10000 от цены. Только в прибыли"},
        {"key": "place_lead_min", "label": "За сколько минут до открытия ставим", "type": "number",
         "default": 10, "min": 0, "max": 60,
         "hint": "Инфраструктурный: заявки выставляются ДО открытия биржи. В бэктесте лестница вооружается на первом баре дня и может исполниться уже на нём, поэтому на результат не влияет — значение нужно живому раннеру"},
        {"key": "hold_min", "label": "Окно набора, мин после открытия", "type": "number",
         "default": 30, "min": 5, "max": 720,
         "hint": "Окно считается от первого бара дня. Неисполненные заявки снимаются через hold_min, НО только если сделок не было ни одной"},
        {"key": "ema_fast", "label": "Быстрая EMA (выход)", "type": "number", "default": 9, "min": 2, "max": 100,
         "hint": "Сигнал выхода в конце сессии: пересечение двух EMA против позиции"},
        {"key": "ema_slow", "label": "Медленная EMA (выход)", "type": "number", "default": 21, "min": 3, "max": 300,
         "hint": "Должна быть больше быстрой"},
        {"key": "exit_lead_min", "label": "За сколько минут до закрытия включать выход по EMA", "type": "number",
         "default": 120, "min": 0, "max": 480,
         "hint": "Овернайт запрещён: в этом окне позиция закрывается по сигналу двух EMA, а на закрытии — принудительно"},
        {"key": "invert", "label": "Инверсия (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "0 = фейд (рабочий): вверх=шорт, вниз=лонг. 1 = прямой пробой (контроль)"},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить сделки в лонг"},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1,
         "hint": "Разрешить сделки в шорт"},
        {"key": "bar_offset_min", "label": "Сдвиг метки бара, мин", "type": "number", "default": 0, "min": 0, "max": 360,
         "hint": "0 для баров ISS/бэктеста (МСК-стенка как UTC), 180 для истинно-UTC баров раннера агента"},
    ],
}
