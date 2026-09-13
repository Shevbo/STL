"""
Valley Spike — дальняя лестница от долины смерти до средней, какой она была ДО долины.

ПОСТАНОВКА ОПЕРАТОРА 13.09.2026. Работать только в «долине смерти» — узком боковике,
где размах ЗАКРЫТИЙ за dv_bars минут уже коридора. Как только долина включилась,
ставить дальние заявки на уровне последнего значения медленной средней ДО перехода в
долину — очень далеко от середины долины — и лестницу за ним. Ставка на выброс из
долины (новость, кит, сбор стопов), который дотягивается до старой средней и
возвращается в коридор.

ПОЧЕМУ ЭТО ЧЕСТНЕЕ ПРЕДШЕСТВЕННИКА. impulse_fade пал от исполнения: филл внутри бара
вместе с гейтом по экстремуму того же бара — заглядывание в будущее. Здесь всё, что
решает о заявке, известно ДО бара, на котором она исполняется: долина — по закрытиям,
уровень заморожен в момент входа в долину, лестница действует со СЛЕДУЮЩЕГО бара.
Заявки — лимитники против выброса (продажа выше рынка), налив по своему уровню.

МЕХАНИЗМ (M1):
  ДОЛИНА    размах закрытий за dv_bars < dv_amp% средней дневной свечи. Детектор тот
            же, что у фильтра реестра (library.in_death_valley); порог в долях дневной
            свечи, а не в пунктах — одна сетка живёт и на RI, и на Si.
  УРОВЕНЬ   в баре, где долина включилась: середина = среднее закрытий окна долины,
            медленная средняя ДО долины = SMA(slow_n) закрытий перед окном. Разрыв
            меньше min_gap_amp% дневной свечи — средняя не «дальняя», долина пропускается.
  ЛЕСТНИЦА  price(0) = средняя до долины, price(n) = price(n-1) + D/(n+1) дальше от
            долины, D = d_coef%·|разрыв| (формула rich_fool). qty(n) = qty·vol_mult^n,
            потолок max_contracts. mirror=1 — та же лестница зеркально с другой стороны.
  СТОРОНА   фейд: лестница выше долины продаёт, ниже — покупает. Пробой (покупка выше
            рынка) не реализуем: у живого раннера нет стоп-заявок.
  ТЕЙК      возврат на ret_pct% пути от средней позиции к середине долины.
  СТОП      stop_amp% дневной свечи за ПОСЛЕДНЕЙ ступенью; гэп — по открытию, иначе
            по уровню плюс slip_amp%.
  ВРЕМЯ     max_hold баров с первого налива.
  ЖИЗНЬ     одна долина — одна лестница: кончилась без налива — заявки сняты; после
            выхода из позиции до конца той же долины новая не ставится.

ЖИВАЯ ТОРГОВЛЯ. Бэктест считает, что лестница СТОИТ в стакане. Живой раннер сейчас
отправляет заявку ступени только после касания на закрытом баре, и лимитник после
отыгранного выброса скорее всего не нальётся. До реала раннер должен уметь выставлять
лестницу заранее и снимать её (зона real-trade).
"""
from trader.lab.indicators import sma
from trader.lab.runtime import STLRuntime
from trader.lab.strategies.library import in_death_valley


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(
        f"ValleySpike started | dv={params.get('dv_bars', 60)}бар<{params.get('dv_amp', 25)}%свечи "
        f"slow={params.get('slow_n', 400)} gap>={params.get('min_gap_amp', 50)}% "
        f"steps={params.get('step_count', 3)} D={params.get('d_coef', 50)}% "
        f"vol×{float(params.get('vol_mult', 10)) / 10:.1f} mirror={params.get('mirror', 0)} "
        f"ret={params.get('ret_pct', 50)}% stop={params.get('stop_amp', 25)}% "
        f"hold={params.get('max_hold', 240)} symbol={params.get('symbol')}"
    )


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    dv_bars = max(2, int(params.get("dv_bars", 60)))
    dv_amp = float(params.get("dv_amp", 25)) / 100.0
    amp_days = max(1, int(params.get("amp_days", 5)))
    slow_n = max(2, int(params.get("slow_n", 400)))
    min_gap_amp = float(params.get("min_gap_amp", 50)) / 100.0

    bars = await stl.get_bars(symbol, tf=1, n=dv_bars + 1)
    if not bars:
        return
    cur = bars[-1]
    amp = _daily_amp(stl, cur, amp_days)

    pos = await stl.get_position(symbol)
    cur_qty = pos.quantity if pos.side == "long" else (-pos.quantity if pos.side == "short" else 0)

    # Пояс безопасности: позиция есть, лестницы в состоянии нет — закрыть, а не гадать.
    if cur_qty != 0 and not stl.get_state("ladder"):
        await stl.place_order_at(symbol, "sell" if cur_qty > 0 else "buy",
                                 abs(cur_qty), cur.close, cur.time)
        _disarm(stl)
        return

    # ── 1. Ведение позиции ────────────────────────────────────────────────────
    if cur_qty != 0:
        await _manage(stl, params, symbol, cur, pos, cur_qty)
        return

    # ── 2. Налив стоящей лестницы. Она выставлена по информации ДО этого бара ──
    if stl.get_state("ladders") and await _fill_ladder(stl, params, symbol, cur):
        return

    # ── 3. Долина по закрытию ЭТОГО бара — лестница действует со следующего ────
    in_valley = amp > 0 and in_death_valley([b.close for b in bars], dv_bars, dv_amp * amp)
    if not in_valley:
        if stl.get_state("valley_on"):
            stl.set_state("valley_on", 0)
            _disarm(stl)                       # долина кончилась без налива
        return
    if stl.get_state("valley_on"):
        return                                 # эта долина уже обработана
    stl.set_state("valley_on", 1)

    hist = await stl.get_bars(symbol, tf=1, n=slow_n + dv_bars)
    if len(hist) < slow_n + dv_bars:
        return
    closes = [b.close for b in hist]
    mid = sum(closes[-dv_bars:]) / dv_bars
    gap = sma(closes[:-dv_bars], slow_n) - mid
    if abs(gap) < min_gap_amp * amp:
        return

    qty = max(1, int(params.get("qty", 1)))
    step_count = max(1, int(params.get("step_count", 3)))
    d = float(params.get("d_coef", 50)) / 100.0 * abs(gap)
    vol_mult = float(params.get("vol_mult", 10)) / 10.0
    s0 = 1 if gap > 0 else -1                  # сторона, где осталась средняя
    ladders = []
    for s in ([s0, -s0] if int(params.get("mirror", 0)) else [s0]):
        trade_dir = -s                         # фейд: выше долины продаём
        if (trade_dir > 0 and not int(params.get("allow_long", 1))) or \
           (trade_dir < 0 and not int(params.get("allow_short", 1))):
            continue
        levels, acc = [mid + s * abs(gap)], 0.0
        for n in range(1, step_count):
            acc += d / (n + 1)
            levels.append(levels[0] + s * acc)
        qtys = [max(1, round(qty * vol_mult ** n)) for n in range(step_count)]
        ladders.append({"s": s, "levels": levels, "qtys": qtys, "hit": 0})
    if not ladders:
        return
    stl.set_state("ladders", ladders)
    stl.set_state("mid", mid)
    stl.set_state("amp0", amp)
    stl.log(f"armed: долина {mid:.0f}, средняя до долины {mid + gap:.0f}, "
            f"лестницы {[[round(x) for x in lad['levels']] for lad in ladders]}")


async def _fill_ladder(stl: STLRuntime, params: dict, symbol: str, cur) -> bool:
    """Налить ступени, которых коснулся бар. Первая сработавшая сторона запирает
    вторую: одновременно быть и в лонге, и в шорте нельзя."""
    for lad in sorted(stl.get_state("ladders") or [], key=lambda x: -x["s"]):
        if await _fill_steps(stl, params, symbol, cur, lad, 0):
            stl.set_state("ladder", lad)
            stl.set_state("ladders", None)
            stl.set_state("held", 0)
            return True
    return False


async def _fill_steps(stl: STLRuntime, params: dict, symbol: str, cur, lad: dict,
                      have: int) -> bool:
    """Лимитники ступеней по своим уровням; потолок позиции max_contracts."""
    max_contracts = max(1, int(params.get("max_contracts", 10)))
    s, filled = lad["s"], False
    while lad["hit"] < len(lad["levels"]):
        lv, q = lad["levels"][lad["hit"]], lad["qtys"][lad["hit"]]
        touched = cur.high >= lv if s > 0 else cur.low <= lv
        if not touched or have + q > max_contracts:
            break
        await stl.place_order_at(symbol, "sell" if s > 0 else "buy", q, lv, cur.time)
        have += q
        lad["hit"] += 1
        filled = True
    return filled


async def _manage(stl: STLRuntime, params: dict, symbol: str, cur, pos, cur_qty: int) -> None:
    lad = stl.get_state("ladder")
    s = lad["s"]
    mid = float(stl.get_state("mid"))
    amp0 = float(stl.get_state("amp0"))
    held = int(stl.get_state("held", 0) or 0) + 1
    stl.set_state("held", held)
    side = "buy" if cur_qty < 0 else "sell"
    avg = float(pos.avg_price)

    # Стоп — за ПОСЛЕДНЕЙ ступенью: внутри лестницы он обрывал бы набор.
    sl = lad["levels"][-1] + s * float(params.get("stop_amp", 25)) / 100.0 * amp0
    if (cur.high >= sl) if s > 0 else (cur.low <= sl):
        slip = float(params.get("slip_amp", 3)) / 100.0 * amp0
        await stl.place_order_at(symbol, side, abs(cur_qty),
                                 _stop_fill(sl, cur, worse=s > 0, slip=slip), cur.time)
        _disarm(stl)
        return

    # Тейк — возврат на ret_pct% пути от средней позиции к середине долины.
    tp = avg - float(params.get("ret_pct", 50)) / 100.0 * (avg - mid)
    if (cur.low <= tp) if s > 0 else (cur.high >= tp):
        await stl.place_order_at(symbol, side, abs(cur_qty), tp, cur.time)
        _disarm(stl)
        return

    if held >= max(1, int(params.get("max_hold", 240))):
        await stl.place_order_at(symbol, side, abs(cur_qty), cur.close, cur.time)
        _disarm(stl)
        return

    if await _fill_steps(stl, params, symbol, cur, lad, abs(cur_qty)):
        stl.set_state("ladder", lad)


def _daily_amp(stl: STLRuntime, cur, amp_days: int) -> float:
    """Средняя дневная свеча по ЗАВЕРШЁННЫМ дням, кольцо hi/lo O(1) на бар (приём
    library.py / impulse_fade). День = UTC-сутки: сессия FORTS укладывается в них."""
    day = cur.time // 86400
    ring = [list(r) for r in (stl.get_state("amp_ring", None) or [])]
    if ring and int(ring[-1][0]) == day:
        ring[-1][1] = max(float(ring[-1][1]), cur.high)
        ring[-1][2] = min(float(ring[-1][2]), cur.low)
    else:
        ring.append([day, cur.high, cur.low])
        ring = ring[-(amp_days + 1):]
    stl.set_state("amp_ring", ring)
    done = ring[:-1]
    return (sum(r[1] - r[2] for r in done) / len(done)) if done else 0.0


def _stop_fill(level: float, bar, worse: bool, slip: float) -> float:
    """Стоп-лосс: уровень плюс проскальзывание в худшую сторону; бар, открывшийся за
    уровнем, даёт открытие — оно и есть худшая из двух цен.
    ponytail: копия из impulse_fade — три строки дешевле зависимости от закрытой стратегии."""
    px = level + slip if worse else level - slip
    return max(px, bar.open) if worse else min(px, bar.open)


def _disarm(stl: STLRuntime) -> None:
    for k in ("ladders", "ladder", "mid", "amp0"):
        stl.set_state(k, None)
    stl.set_state("held", 0)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("ValleySpike stopped")


STRATEGY_META = {
    "name": "Valley Spike — дальняя лестница от долины смерти до прежней средней",
    "description": (
        "Работает только в долине смерти (размах закрытий за dv_bars < dv_amp% дневной "
        "свечи). В момент входа в долину замораживает медленную среднюю SMA(slow_n), какой "
        "она была ДО долины, и ставит на её уровне лимитную лестницу против выброса (формула "
        "rich_fool D/(n+1)). Тейк — возврат к середине долины, стоп за последней ступенью. "
        "Одна долина — одна лестница. Лестница действует со следующего бара после входа в "
        "долину: всё, что решает о заявке, известно до бара её налива."
    ),
    "source": "постановка оператора 13.09.2026: только долина смерти, заявки на средней до долины, лестница",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "ТОЛЬКО контракт, не сшитая серия"},
        {"key": "dv_bars", "label": "Долина: окно (баров M1)", "type": "number", "default": 60, "min": 10, "max": 480},
        {"key": "dv_amp", "label": "Долина: коридор, % дневной свечи", "type": "number", "default": 25, "min": 5, "max": 100,
         "hint": "Размах закрытий за окно меньше этой доли средней дневной свечи = долина"},
        {"key": "amp_days", "label": "Дней для средней дневной свечи", "type": "number", "default": 5, "min": 1, "max": 30},
        {"key": "slow_n", "label": "Медленная средняя (баров)", "type": "number", "default": 400, "min": 50, "max": 2880,
         "hint": "Замораживается на баре перед окном долины"},
        {"key": "min_gap_amp", "label": "Мин. разрыв средняя↔долина, % свечи", "type": "number", "default": 50, "min": 0, "max": 300,
         "hint": "Средняя ближе — не «дальняя», долина пропускается"},
        {"key": "step_count", "label": "Ступеней лестницы", "type": "number", "default": 3, "min": 1, "max": 10},
        {"key": "d_coef", "label": "Ширина лестницы D, % разрыва", "type": "number", "default": 50, "min": 5, "max": 300,
         "hint": "price(n) = price(n-1) + D/(n+1)"},
        {"key": "vol_mult", "label": "Множитель объёма ×10 (10=1.0)", "type": "number", "default": 10, "min": 10, "max": 30},
        {"key": "qty", "label": "Контрактов на первой ступени", "type": "number", "default": 1, "min": 1, "max": 10},
        {"key": "max_contracts", "label": "Потолок позиции", "type": "number", "default": 10, "min": 1, "max": 100},
        {"key": "mirror", "label": "Зеркальная лестница (0/1)", "type": "number", "default": 0, "min": 0, "max": 1,
         "hint": "1 = такая же лестница с другой стороны долины"},
        {"key": "ret_pct", "label": "Тейк: % пути к середине долины", "type": "number", "default": 50, "min": 10, "max": 100},
        {"key": "stop_amp", "label": "Стоп за последней ступенью, % свечи", "type": "number", "default": 25, "min": 5, "max": 200},
        {"key": "slip_amp", "label": "Проскальзывание стопа, % свечи", "type": "number", "default": 3, "min": 0, "max": 50},
        {"key": "max_hold", "label": "Держать не дольше (баров)", "type": "number", "default": 240, "min": 10, "max": 1440},
        {"key": "allow_long", "label": "Лонги (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
        {"key": "allow_short", "label": "Шорты (0/1)", "type": "number", "default": 1, "min": 0, "max": 1},
    ],
}
