"""
Rich Fool — предоткрытийная лестница фейда импульса ОТКРЫТИЯ биржи.

СУТЬ (спецификация оператора, уточнена 12.09.2026). Смысл строго в открытии: за
place_lead_min минут ДО открытия сессии выставляется лестница заявок по обе стороны
от вчерашнего закрытия. Импульс открытия фейдится — цена ускакала вверх, робот
продаёт; ускакала вниз — покупает, добирая позицию на каждой следующей ступени.

Уровни:  price(0) = вчерашнее закрытие,  price(n) = price(n-1) + D/(n+1+F),
         D = amp × d_coef,  amp = средний дневной размах high-low за n_days.
         Зазоры убывают (D/2, D/3, D/4 …) — ступени гуще по мере удаления.
         d_coef ∈ [0.2, 1.0] сужает лестницу: при 0.2 вся она укладывается в
         пятую часть амплитуды, при 1.0 растягивается на всю.

Объём:   заданы объём ПЕРВОЙ ступени (qty_first) и БЮДЖЕТ позиции
         (max_contracts). Множитель роста не задаётся, а выводится — при
         фиксированных first/steps/budget он единственный. Бюджет выбирается РОВНО
         на последней ступени: если ступеней двадцать, работают все двадцать, а не
         первые пять до потолка.

Стоп:    sl_beyond_pts ПУНКТОВ за последней ступенью лестницы. Смысл — поймать
         поклёвку в заявку последней ступени, а если цена пошла дальше, выйти
         почти сразу. Ставится от уровня, а не от средней, поэтому не зависит от
         того, сколько ступеней успело набраться.

Тейк:    ТРЕЙЛИНГ с двумя порогами, оба в ПУНКТАХ:
           tp_arm_pts  — насколько закрытие обязано уйти в нашу пользу от
                         средней, чтобы слежение включилось;
           tp_back_pts — допустимый откат от лучшего закрытия.
         Слежение по ЦЕНЕ ЗАКРЫТИЯ: один бар не может и задать пик, и выбить по
         нему тейк. После тейка лестница снимается до конца дня.

ИСПОЛНЕНИЕ РАЗНЫМИ ТИПАМИ ЗАЯВОК — иначе зеркало сравнивает несравнимое.
         Вход ФЕЙДА (invert=0) это ЛИМИТНАЯ заявка: продажа ВЫШЕ рынка, покупка
         НИЖЕ. Проскальзывания не имеет, но и касания фитилём недостаточно: на
         цене заявки стоит ОЧЕРЕДЬ, и мгновенный отскок наливает тех, кто впереди.
         Гарантированный филл — когда цена ПРОШЛА сквозь уровень и вымела очередь,
         поэтому требуется проход slip_guard_pts ПУНКТОВ за уровень (защита от
         проскальзывания).
         Вход ПРОБОЯ (invert=1), СТОП-ЛОСС и ТРЕЙЛИНГ-ТЕЙК — СТОПОВЫЕ исполнения:
         срабатывают по уровню, а наливаются по рынку, то есть всегда ХУЖЕ уровня.
         На все три накладывается slip_pct. Трейлинг именно стоп: он следует за
         ценой и срабатывает на ОТКАТЕ.

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


def _step_sizes(budget: int, steps: int, first: int) -> list[int]:
    """Объёмы ступеней: первая равна `first`, дальше геометрический рост, а СУММА
    равна `budget` ровно — потолок достигается на последней ступени и ни одна
    ступень не остаётся декорацией.

    Множитель не задаётся, а ВЫВОДИТСЯ: при фиксированных first/steps/budget он
    единственный. Ищем делением отрезка, затем остаток округления кладём на дальние
    ступени — они и должны быть крупнее.

    Если бюджета не хватает даже на `first` контрактов в каждой ступени, ступеней
    физически меньше: возвращаем столько, сколько влезает, по `first` в каждой.
    """
    steps = max(1, int(steps))
    first = max(1, int(first))
    budget = max(int(budget), first)
    if budget < first * steps:                  # бюджет не вмещает все ступени
        k = max(1, budget // first)
        out = [first] * k
        out[-1] += budget - sum(out)
        return out

    def total(v: float) -> float:
        if abs(v - 1.0) < 1e-9:
            return first * steps
        return first * (v ** steps - 1.0) / (v - 1.0)

    lo, hi = 1.0, 2.0
    while total(hi) < budget and hi < 64.0:     # раздвигаем, пока бюджет не накрыт
        hi *= 2.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if total(mid) < budget:
            lo = mid
        else:
            hi = mid
    v = (lo + hi) / 2.0

    out = [max(1, int(round(first * v ** k))) for k in range(steps)]
    out[0] = first                              # первая ступень ровно как задано
    left = budget - sum(out)
    i = steps - 1
    guard = 0
    while left and guard < 10 * budget + 100:
        guard += 1
        if left > 0:
            out[i] += 1
            left -= 1
        elif i and out[i] > 1:                  # первую ступень не трогаем
            out[i] -= 1
            left += 1
        i = i - 1 if i > 1 else steps - 1
    return out


def _reset_position_state(stl: STLRuntime) -> None:
    """Позиция закрыта: гасим направление, лестницу и экстремум трейлинга."""
    for k in ("dir", "hit", "side_locked", "tp_armed", "tp_best"):
        stl.set_state(k, 0)
    stl.set_state("day_done", 1)          # в этот день новую лестницу не ставим


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"Rich Fool started | lead={params.get('place_lead_min', 10)}m "
        f"hold={params.get('hold_min', 30)}m N={params.get('n_days', 5)}d "
        f"D={float(params.get('d_coef', 100)) / 100:.2f}×amp "
        f"steps={params.get('step_count', 3)} first={params.get('qty_first', 1)} "
        f"sl_beyond={params.get('sl_beyond_pts', 50)}пт "
        f"tp={params.get('tp_arm_pts', 300)}/{params.get('tp_back_pts', 100)}пт "
        f"ema={params.get('ema_fast', 9)}/{params.get('ema_slow', 21)} "
        f"exit_lead={params.get('exit_lead_min', 120)}m "
        f"invert={params.get('invert', 0)} symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    hold = int(params.get("hold_min", 30))
    n_days = max(1, int(params.get("n_days", 5)))
    d_coef = float(params.get("d_coef", 100)) / 100.0          # 20..100 -> 0.2..1.0
    f_shift = float(params.get("f_shift", 0)) / 10.0           # сдвиг знаменателя, 0..10
    step_count = max(1, int(params.get("step_count", 3)))
    qty_first = max(1, int(params.get("qty_first", 1)))       # объём ПЕРВОЙ ступени
    sl_beyond_pts = float(params.get("sl_beyond_pts", 50))   # стоп: ПУНКТЫ за лестницей
    tp_arm_pts = float(params.get("tp_arm_pts", 300))        # тейк: активация слежения
    tp_back_pts = float(params.get("tp_back_pts", 100))      # тейк: допустимый откат
    slip_guard_pts = float(params.get("slip_guard_pts", 50))        # проход за лимитный уровень, ПУНКТЫ
    slip_pct = float(params.get("slip_pct", 0)) / 10000.0           # проскальзывание СТОПОВ
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
        # dir / hit / side_locked / состояние тейка не трогаем: позиция может быть открыта
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
            up = [float(x) for x in (stl.get_state("levels_up") or [])]
            dn = [float(x) for x in (stl.get_state("levels_dn") or [])]

            # 1b. СТОП — sl_beyond_pts ПУНКТОВ за последней ступенью лестницы.
            #     Ловим поклёвку в последнюю заявку: пошла цена дальше — выходим
            #     почти сразу. От уровня, а не от средней, поэтому расстояние не
            #     зависит от того, сколько ступеней успело набраться.
            if dirn > 0:
                sl_px = min([avg] + dn) - sl_beyond_pts
            else:
                sl_px = max([avg] + up) + sl_beyond_pts
            gap_sl = cur.open <= sl_px if dirn > 0 else cur.open >= sl_px
            hit_sl = cur.low <= sl_px if dirn > 0 else cur.high >= sl_px
            if gap_sl or hit_sl:
                base = cur.open if gap_sl else sl_px
                slip = base * slip_pct
                await stl.place_order_at(symbol, side, abs(cur_qty),
                                         base - slip if dirn > 0 else base + slip,
                                         cur.time)
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

            # 1d. ТРЕЙЛИНГ-ТЕЙК: два порога, оба в пунктах, слежение по ЗАКРЫТИЮ.
            #     Сначала активация — закрытие ушло в нашу пользу на tp_arm_pts от
            #     средней. Потом следим за лучшим закрытием и выходим при откате на
            #     tp_back_pts. По закрытию, а не по экстремуму: один бар не должен
            #     одновременно задавать пик и выбивать по нему тейк.
            tp_on = int(stl.get_state("tp_armed", 0) or 0)
            best = float(stl.get_state("tp_best", 0) or 0)
            fav = (cur.close - avg) if dirn > 0 else (avg - cur.close)
            if not tp_on and fav >= tp_arm_pts:
                tp_on, best = 1, cur.close
                stl.set_state("tp_armed", 1)
                stl.set_state("tp_best", best)
            elif tp_on:
                back = (best - cur.close) if dirn > 0 else (cur.close - best)
                if back >= tp_back_pts:
                    # Тейк по механике СТОП: наливается по рынку, хуже уровня.
                    slip = cur.close * slip_pct
                    await stl.place_order_at(symbol, side, abs(cur_qty),
                                             cur.close - slip if dirn > 0 else cur.close + slip,
                                             cur.time)
                    _reset_position_state(stl)
                    return
                stl.set_state("tp_best", max(best, cur.close) if dirn > 0
                              else min(best, cur.close))
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
        # D сужается коэффициентом: D = amp × d_coef.
        # Зазор ступени n = D/(n+1+F). F сдвигает знаменатель: при F=0 это исходная
        # формула оператора с быстро убывающими зазорами, при большом F зазоры
        # выравниваются (1/(n+1+F) слабее зависит от n) и вся лестница поджимается.
        D = amp * d_coef
        offs, acc = [], 0.0
        for j in range(1, step_count + 1):
            acc += D / (j + 1 + f_shift)
            offs.append(acc)
        stl.set_state("levels_up", [prev_close + o for o in offs])
        stl.set_state("levels_dn", [prev_close - o for o in offs])
        stl.set_state("hit", 0)
        stl.set_state("side_locked", 0)
        stl.set_state("tp_armed", 0)
        stl.set_state("tp_best", 0)
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
        sizes = _step_sizes(max_contracts, step_count, qty_first)
        # Один бар может пересечь сразу несколько ступеней — исполняем ВСЕ.
        # Вход фейда — ЛИМИТНЫЙ: нужен проход slip_guard_pts пунктов ЗА уровень,
        # иначе очередь на этой цене не вымело и филла могло не быть. Вход пробоя —
        # СТОПОВЫЙ: достаточно касания, но с проскальзыванием ниже. guard=0
        # возвращает прежнее оптимистичное «коснулся = налит».
        guard = slip_guard_pts if invert == 0 else 0.0
        while hit < step_count:
            fire = 0
            if locked in (0, 1) and cur.high >= up[hit] + guard:
                fire = 1
            elif locked in (0, -1) and cur.low <= dn[hit] - guard:
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
            # Объём ступени из РАСПРЕДЕЛЁННОГО бюджета: последняя ступень
            # выбирает потолок ровно, декораций нет.
            step_qty = sizes[hit] if hit < len(sizes) else 0
            step_qty = min(step_qty, max_contracts - abs(cur_qty))
            if step_qty <= 0:
                stl.set_state("hit", step_count)
                break
            level = up[hit] if fire > 0 else dn[hit]
            # Гэп сквозь уровень — по открытию бара. Для лимитки это цена ЛУЧШЕ
            # уровня (честно), для стопа — ХУЖЕ (тоже честно): одна и та же формула
            # потому, что обе стороны хотят один и тот же конец диапазона.
            fill_px = max(level, cur.open) if fire > 0 else min(level, cur.open)
            if invert:
                # СТОП наливается по рынку: покупка дороже, продажа дешевле.
                slip = fill_px * slip_pct
                fill_px += slip if trade_dir > 0 else -slip
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
        "вчерашнего закрытия: price(n)=price(n-1)+D/(n+1+F), D=amp·d_coef, F=f_shift/10 управляет равномерностью зазоров, amp — средний "
        "дневной размах за n_days. Зазоры убывают (D/2, D/3, D/4 …). Цена ускакала ВВЕРХ — "
        "ШОРТ с добором по ступеням вверх, ВНИЗ — ЛОНГ с добором вниз (фейд импульса "
        "открытия). Объём: задан qty_first на первую ступень, бюджет max_contracts "
        "выбирается РОВНО на последней ступени, множитель роста выводится. Стоп — "
        "sl_beyond_pts ПУНКТОВ за последней ступенью: ловим поклёвку в последнюю заявку, "
        "пошла цена дальше — выходим почти сразу. Тейк — трейлинг с двумя порогами в "
        "пунктах: tp_arm_pts включает слежение за ценой ЗАКРЫТИЯ, tp_back_pts закрывает на "
        "откате от лучшего закрытия; после тейка лестница снимается до конца дня. "
        "Через hold_min минут неисполненные заявки снимаются, только если сделок "
        "не было вовсе. ОВЕРНАЙТ ЗАПРЕЩЁН: за exit_lead_min до закрытия выход по сигналу "
        "двух EMA, на закрытии — принудительно. Границы сессии берутся ИЗ ИСТОРИИ: открытие = "
        "первый бар дня, закрытие = последний бар предыдущего дня того же типа (расписание "
        "FORTS менялось внутри периода). Исполнение РАЗНЫМИ типами заявок: вход фейда "
        "лимитный (нужен проход slip_guard_pts пунктов за уровень, проскальзывания нет), вход "
        "пробоя, стоп-лосс и трейлинг-тейк стоповые (проскальзывание slip_pct). "
        "invert=1 — контроль (прямой пробой)."
    ),
    "source": "гипотеза оператора 09.09.2026, спецификация уточнена 12.09.2026",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6",
         "hint": "ТОЛЬКО конкретный контракт (RIU6), не базовый код: на непрерывной серии шов переката даёт фантомный результат"},
        {"key": "n_days", "label": "N дней для амплитуды", "type": "number", "default": 5, "min": 1, "max": 30,
         "hint": "Сколько завершённых дней усредняем в дневной размах (high-low)"},
        {"key": "d_coef", "label": "Коэффициент к D, % ×100 (100 = 1.00×amp)", "type": "number",
         "default": 100, "min": 5, "max": 100,
         "hint": "Сужает лестницу: D = amp × d_coef/100. При 5 первая ступень стоит в 2.5% амплитуды от вчерашнего закрытия (для RI это ~60 пунктов), при 100 — на половине размаха"},
        {"key": "f_shift", "label": "Сдвиг знаменателя F ×10 (0 = формула D/(n+1))", "type": "number",
         "default": 0, "min": 0, "max": 100,
         "hint": "Зазор ступени n = D/(n+1+F/10). При F=0 зазоры убывают быстро (D/2, D/3, D/4), при большом F выравниваются и вся лестница поджимается к цене"},
        {"key": "step_count", "label": "Ступеней в лестнице", "type": "number", "default": 3, "min": 1, "max": 25,
         "hint": "Сколько заявок с каждой стороны. Смещение ступени n = D·(1/2+1/3+…+1/(n+1))"},
        {"key": "qty_first", "label": "Контрактов на ПЕРВУЮ ступень", "type": "number",
         "default": 1, "min": 1, "max": 50,
         "hint": "Объём первой сработавшей заявки. Множитель роста НЕ задаётся — он выводится из qty_first, step_count и max_contracts, потому что при них он единственный"},
        {"key": "max_contracts", "label": "Жёсткий потолок позиции", "type": "number", "default": 100, "min": 1, "max": 500,
         "hint": "Лестница перестаёт доливать на этом числе контрактов"},
        {"key": "sl_beyond_pts", "label": "Стоп: пунктов ЗА последней ступенью", "type": "number",
         "default": 50, "min": 5, "max": 1000,
         "hint": "Стоп ставится в sl_beyond_pts пунктах за последней ступенью лестницы. Смысл — поймать поклёвку в заявку последней ступени, а если цена пошла дальше, выйти почти сразу"},
        {"key": "tp_arm_pts", "label": "Тейк: активация слежения, пункты", "type": "number",
         "default": 300, "min": 10, "max": 5000,
         "hint": "Насколько цена ЗАКРЫТИЯ обязана уйти в нашу пользу от средней цены позиции, чтобы трейлинг включился"},
        {"key": "tp_back_pts", "label": "Тейк: допустимый откат, пункты", "type": "number",
         "default": 100, "min": 5, "max": 2000,
         "hint": "Откат от лучшего ЗАКРЫТИЯ, на котором позиция закрывается. После тейка лестница снимается и в этот день не ставится"},
        {"key": "slip_guard_pts", "label": "Защита от проскальзывания, пункты", "type": "number",
         "default": 50, "min": 0, "max": 500,
         "hint": "Сколько ПУНКТОВ цена обязана пройти ЗА уровень, чтобы лимитная заявка фейда считалась налитой: на цене заявки стоит очередь, и касание с отскоком наливает тех, кто впереди. Стоповых заявок не касается. ВНИМАНИЕ: пункт у инструментов разный — 50 пунктов это 5 тиков на RI и 50 тиков на Si"},
        {"key": "slip_pct", "label": "Проскальзывание стопов, % ×100", "type": "number",
         "default": 0, "min": 0, "max": 50,
         "hint": "Накладывается на СТОПОВЫЕ исполнения: вход по пробою (invert=1), стоп-лосс и трейлинг-тейк. Лимитные заявки фейда проскальзывания не имеют"},
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
