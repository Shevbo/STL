"""
Retest — вход к пику импульса после подтверждённого отката (пререгистрация
docs/retest-strategy-2026.md, заказ оператора 01.10.2026).

ДОПУЩЕНИЕ. После импульса в 3-10 ATR цена через 3-120 баров второй раз приходит к пику
импульса. Стратегия покупает откат (импульс вверх) или продаёт его (вниз) и ждёт возврата к
пику P; стоп на sl_k·d против входа, где d = расстояние от P до закрытия подтверждения.

ЧИСТЫЕ ФУНКЦИИ (без движка, тестируются на списке баров):
  find_impulse(bars, i, params, sgn)    импульс, чей пик = бар i (sgn=-1: вниз)
  track_step(trk, bars, i, k, params, sgn)  один шаг трекера пика: обновление пика,
                                        подтверждение (d, зеркальный уровень M)
Всё считается по барам до i включительно.

ИСПОЛНЕНИЕ. BacktestRuntime.place_order исполняет заявку, поставленную в on_bar бара t, по
ОТКРЫТИЮ бара t+1; place_order_at исполняет по заданной цене в баре t. Вход, стоп, время и
тейк tp_mode=0: решение по закрытию, филл place_order (открытие следующего бара). Лимитных
заявок в движке нет.

tp_mode=1 (справочный): лимит на уровне тейка стоит со следующего бара после входа; он
считается налитым по цене уровня, если high/low бара, уже после постановки, прошёл уровень
на fill_pen шагов цены (параметр price_step, умолчание 1; у RI = 10). Живой раннер лимитных
заявок вперёд не ставит, режим только справочный.

ЗАПРЕТ ПЕРЕНОСА. Позиция закрывается на баре с временем >= 23:40 по ЕГО закрытию
(place_order_at по close бара): place_order исполнил бы по открытию следующего бара, то есть
следующего утра, и купил бы ночёвку. Новые входы с 23:00 запрещены. Импульсы и подтверждения
не переходят через границу дня.

ОТСТУПЛЕНИЯ ОТ ПРЕРЕГИСТРАЦИИ (минимальное толкование):
  1. Пик P = максимум high окна от минимума low (старт s) до бара i; бар i должен строго
     превышать high баров s..i-1. Старт s строго РАНЬШЕ бара пика (порядок high/low внутри одного бара неизвестен: один
     большой бар не импульс), в пределах imp_bars баров и того же дня, ATR
     (mean high-low) по atr_n барам ДО s; история короче atr_n = импульса нет.
  2. Пик обновляется новым максимумом, но если на этом баре импульс уже вне
     [imp_min, imp_max] ATR, трекер сбрасывается (импульс 12 ATR не торгуется). Подтверждение
     на баре, обновившем пик, невозможно (нужен бар строго после t_p).
  3. Трекер живёт wait_max баров от t_p; без подтверждения за это время сбрасывается.
  4. Вход: решение на баре k, филл на k+1; нужно k+1-t_p >= wait_min и k+1-t_p < wait_max.
     Пока ждём wait_min, close обязан оставаться на отходе >= pb ATR, а high не касаться
     P, иначе сетап снят.
  5. invert=1 зеркалит «один в один» от фактической цены входа E: тейк на E + dir·sl_k·d
     (при sl_k=1 это M с поправкой на открытие следующего бара вместо close подтверждения),
     стоп на P - sgn·tp_off·ATR. Вход в ту же минуту и тот же бар, что у прямой.
  6. Дробные величины в десятых: imp_min, imp_max, pb, tp_off, sl_k (30 = 3.0 ATR).
  7. Не торгуются стороны при allow_long/allow_short=0 (для invert считается сторона сделки).
  8. Пик, по которому был вход или снятие, повторно не торгуется: трекеры сбрасываются, а
     трекинг во время позиции и ожидания не ведётся.
"""
from trader.lab.commission import is_weekend
from trader.lab.footprints.common import SESSION_END_MIN, minute_of_day
from trader.lab.runtime import STLRuntime

_FLAT_MIN = SESSION_END_MIN - 10      # 23:40, закрытие по факту баров
_NO_ENTRY_MIN = 23 * 60               # входы с 23:00 запрещены
_WK_FLAT_MIN = 18 * 60 + 50           # выходная сессия 10:00-19:00: флэт за 10 минут до конца
_WK_NO_ENTRY_MIN = 18 * 60 + 30


def _hl(bars, k: int, sgn: int):
    """high/low в «нормированной» оси: sgn=-1 зеркалит цену, импульс всегда вверх."""
    b = bars[k]
    return (b.high, b.low) if sgn > 0 else (-b.low, -b.high)


def find_impulse(bars, i: int, params: dict, sgn: int = 1):
    """Импульс, чей пик = бар i. {'P','i','s','atr','move'} или None."""
    imp_bars = max(1, int(params.get("imp_bars", 10)))
    atr_n = max(1, int(params.get("atr_n", 60)))
    lo_r = float(params.get("imp_min", 30)) / 10.0
    hi_r = float(params.get("imp_max", 100)) / 10.0
    w0 = max(0, i - imp_bars + 1)
    if w0 >= i:
        return None
    s = min(range(w0, i), key=lambda k: (_hl(bars, k, sgn)[1], k))
    if s < atr_n or bars[s].time // 86400 != bars[i].time // 86400:
        return None
    hi_i = _hl(bars, i, sgn)[0]
    if any(_hl(bars, k, sgn)[0] >= hi_i for k in range(s, i)):
        return None
    atr = sum(_hl(bars, k, sgn)[0] - _hl(bars, k, sgn)[1] for k in range(s - atr_n, s)) / atr_n
    if atr <= 0:
        return None
    move = hi_i - _hl(bars, s, sgn)[1]
    if not (lo_r <= move / atr <= hi_r):
        return None
    return {"P": sgn * hi_i, "i": i, "s": s, "atr": atr, "move": move}


def track_step(trk, bars, i: int, k: int, params: dict, sgn: int = 1):
    """Один бар трекера. k = абсолютный номер бара i. -> (трекер, событие|None).
    Событие: {'sgn','P','kp','atr','d','close'} на баре подтверждения."""
    wait_max = int(params.get("wait_max", 120))
    pb = float(params.get("pb", 10)) / 10.0
    if trk is not None and k - trk["k"] > wait_max:
        trk = None
    hi_i = _hl(bars, i, sgn)[0]
    if trk is None or hi_i > sgn * trk["P"]:
        imp = find_impulse(bars, i, params, sgn)
        return ({**imp, "k": k} if imp else None), None
    d = sgn * (trk["P"] - bars[i].close)
    if d >= pb * trk["atr"]:
        return None, {"sgn": sgn, "P": trk["P"], "kp": trk["k"], "atr": trk["atr"],
                      "d": d, "close": bars[i].close}
    return trk, None


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"Retest started | imp {params.get('imp_min', 30)}-{params.get('imp_max', 100)}/10 ATR "
            f"pb={params.get('pb', 10)} wait {params.get('wait_min', 3)}-{params.get('wait_max', 120)} "
            f"sl_k={params.get('sl_k', 10)} tp_mode={params.get('tp_mode', 0)} "
            f"invert={params.get('invert', 0)} symbol={params.get('symbol')}")


def _clear(stl: STLRuntime) -> None:
    for key in ("trk_up", "trk_dn", "pend"):
        stl.set_state(key, None)


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    atr_n = max(1, int(params.get("atr_n", 60)))
    imp_bars = max(1, int(params.get("imp_bars", 10)))
    bars = await stl.get_bars(symbol, tf=1, n=atr_n + imp_bars + 1)
    if not bars:
        return
    i = len(bars) - 1
    cur = bars[i]
    k = int(stl.get_state("k", 0) or 0) + 1
    stl.set_state("k", k)
    minute = minute_of_day(cur.time)
    day = cur.time // 86400
    new_day = stl.get_state("day") != day
    if new_day:
        stl.set_state("day", day)
        _clear(stl)
    wk = is_weekend(cur.time)
    flat_min = _WK_FLAT_MIN if wk else _FLAT_MIN
    noent_min = _WK_NO_ENTRY_MIN if wk else _NO_ENTRY_MIN
    qty = max(1, int(params.get("qty", 1)))
    wait_min = int(params.get("wait_min", 3))
    wait_max = int(params.get("wait_max", 120))
    invert = int(params.get("invert", 0))

    pos = await stl.get_position(symbol)
    trade = stl.get_state("trade")
    if pos.quantity == 0:
        stl.set_state("trade", None)
        trade = None

    # ── Позиция ──
    if pos.quantity != 0:
        dirn = 1 if pos.side == "long" else -1
        exit_side = "sell" if dirn > 0 else "buy"
        if new_day:                                     # короткая сессия: позиция не живёт через границу дня
            await stl.place_order_at(symbol, exit_side, pos.quantity, cur.open, cur.time)
            stl.set_state("trade", None)
            return
        if trade is None or minute >= flat_min:        # без записи о сделке или конец дня
            await stl.place_order_at(symbol, exit_side, pos.quantity, cur.close, cur.time)
            stl.set_state("trade", None)
            return
        e, d, atr = float(pos.avg_price), trade["d"], trade["atr"]
        edge = trade["P"] - trade["sgn"] * float(params.get("tp_off", 0)) / 10.0 * atr
        sl_d = float(params.get("sl_k", 10)) / 10.0 * d
        tp_lvl, sl_lvl = (e + dirn * sl_d, edge) if invert else (edge, e - dirn * sl_d)
        if int(params.get("tp_mode", 0)) == 1 and k > trade["entry_k"]:
            pen = float(params.get("fill_pen", 2)) * float(params.get("price_step", 1))
            if (cur.high >= tp_lvl + pen) if dirn > 0 else (cur.low <= tp_lvl - pen):
                await stl.place_order_at(symbol, exit_side, pos.quantity, tp_lvl, cur.time)
                stl.set_state("trade", None)
                return
        hit_tp = int(params.get("tp_mode", 0)) == 0 and dirn * (cur.close - tp_lvl) >= 0
        hit_sl = dirn * (cur.close - sl_lvl) <= 0
        if hit_tp or hit_sl or k - trade["kp"] >= wait_max:
            await stl.place_order(symbol, exit_side, pos.quantity, cur.close)
            stl.set_state("trade", None)
        return

    # ── Ожидание входа / поиск сетапа ──
    pend = stl.get_state("pend")
    if pend is None:
        trk_up, ev_up = track_step(stl.get_state("trk_up"), bars, i, k, params, 1)
        trk_dn, ev_dn = track_step(stl.get_state("trk_dn"), bars, i, k, params, -1)
        stl.set_state("trk_up", trk_up)
        stl.set_state("trk_dn", trk_dn)
        for ev in (ev_up, ev_dn):
            side_dir = -ev["sgn"] if (ev and invert) else (ev["sgn"] if ev else 0)
            if ev and int(params.get("allow_long" if side_dir > 0 else "allow_short", 1)):
                pend = ev
                break
        if pend is None:
            return
    else:
        s, atr = pend["sgn"], pend["atr"]
        touched = (cur.high >= pend["P"]) if s > 0 else (cur.low <= pend["P"])
        gone = s * (pend["P"] - cur.close) < float(params.get("pb", 10)) / 10.0 * atr
        if touched or gone:
            _clear(stl)
            return
    waited = k + 1 - pend["kp"]
    if waited >= wait_max or minute >= noent_min:
        _clear(stl)
        return
    if waited < wait_min:
        stl.set_state("pend", pend)
        return
    side_dir = -pend["sgn"] if invert else pend["sgn"]
    await stl.place_order(symbol, "buy" if side_dir > 0 else "sell", qty, cur.close)
    stl.set_state("trade", {"sgn": pend["sgn"], "P": pend["P"], "kp": pend["kp"],
                            "atr": pend["atr"], "d": pend["d"], "entry_k": k + 1})
    _clear(stl)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Retest stopped")


def _p(key, label, default, lo, hi, hint=None):
    d = {"key": key, "label": label, "type": "number", "default": default, "min": lo, "max": hi}
    if hint:
        d["hint"] = hint
    return d


STRATEGY_META = {
    "name": "Retest — возврат к пику импульса",
    "description": (
        "После импульса 3-10 ATR за imp_bars баров и отхода на pb ATR от пика входит в сторону "
        "пика (invert=1: от пика). Тейк на пике, стоп sl_k*d, выход по времени wait_max от пика, "
        "флэт к 23:40. Решения по закрытым барам, филл на открытии следующего."
    ),
    "source": "заказ оператора 01.10.2026, docs/retest-strategy-2026.md",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "ТОЛЬКО контракт"},
        _p("atr_n", "ATR (баров)", 60, 10, 400),
        _p("imp_bars", "Импульс: окно (баров)", 10, 3, 60),
        _p("imp_min", "Импульс мин, x0.1 ATR", 30, 10, 100),
        _p("imp_max", "Импульс макс, x0.1 ATR", 100, 30, 300),
        _p("pb", "Подтверждение отхода, x0.1 ATR", 10, 3, 30),
        _p("wait_min", "Не раньше (баров от пика)", 3, 1, 30),
        _p("wait_max", "Не позже (баров от пика)", 120, 20, 480),
        _p("tp_off", "Тейк: отступ от пика, x0.1 ATR", 0, -10, 20),
        _p("sl_k", "Стоп, x0.1 d", 10, 3, 30),
        _p("tp_mode", "Тейк: 0 по close, 1 лимит (справ.)", 0, 0, 1),
        _p("fill_pen", "Лимит: проход уровня (шагов)", 2, 0, 10),
        _p("price_step", "Шаг цены (для fill_pen)", 1, 0.01, 100),
        _p("allow_long", "Лонги (0/1)", 1, 0, 1),
        _p("allow_short", "Шорты (0/1)", 1, 0, 1),
        _p("invert", "Зеркало (0/1)", 0, 0, 1),
        _p("qty", "Контрактов", 1, 1, 10),
    ],
}
