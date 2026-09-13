"""Rich Fool HOLD — та же предоткрытийная лестница фейда, но позиция живёт до тейка или стопа.

Модификация оператора 13.09.2026: ни выхода по времени, ни 2 EMA, ни закрытия перед
ночью. Каждое утро ставится НОВАЯ лестница, как ни в чём не бывало, а позиции прошлых
дней живут своими КНИГАМИ: у каждой своя средняя, свой стоп (sl_beyond_pts за последней
ступенью СВОЕЙ лестницы) и свой трейлинг-тейк по закрытию. Доливает книга только в
свой день. После тейка или стопа книги ТЕКУЩЕГО дня лестница снимается до конца дня.

На счёте одна чистая позиция — сумма книг. P&L линеен по цене, поэтому итог счёта
равен сумме итогов книг, даже когда книги разных дней смотрят в разные стороны.

В состоянии: max_gross — пик суммы контрактов по всем книгам, max_net — пик чистой
позиции (её и держит биржа), max_books — пик числа одновременно живых книг.
"""
from trader.lab.runtime import STLRuntime
from trader.lab.strategies.rich_fool import _bar_clock, _day_ladder, _step_sizes


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"Rich Fool HOLD started | symbol={params.get('symbol')} "
            f"steps={params.get('step_count', 20)} bud={params.get('max_contracts', 40)} "
            f"sl_beyond={params.get('sl_beyond_pts', 150)} "
            f"tp={params.get('tp_arm_pts', 700)}/{params.get('tp_back_pts', 150)}")


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    hold = int(params.get("hold_min", 30))
    n_days = max(1, int(params.get("n_days", 5)))
    d_coef = float(params.get("d_coef", 100)) / 100.0
    f_shift = float(params.get("f_shift", 0)) / 10.0
    step_count = max(1, int(params.get("step_count", 20)))
    qty_first = max(1, int(params.get("qty_first", 1)))
    max_contracts = max(1, int(params.get("max_contracts", 40)))
    sl_beyond = float(params.get("sl_beyond_pts", 150))
    tp_arm = float(params.get("tp_arm_pts", 700))
    tp_back = float(params.get("tp_back_pts", 150))
    guard_pts = float(params.get("slip_guard_pts", 50))
    slip_pct = float(params.get("slip_pct", 0)) / 10000.0
    invert = int(params.get("invert", 0))
    allow_long = int(params.get("allow_long", 1))
    allow_short = int(params.get("allow_short", 1))
    bar_off = int(params.get("bar_offset_min", 0))

    bars = await stl.get_bars(symbol, tf=1, n=2)
    if not bars:
        return
    cur = bars[-1]
    hm, day, is_weekend = _bar_clock(cur.time, bar_off)
    books = stl.get_state("books") or []

    if stl.get_state("day") != day:                     # новый день: только лестница заново
        stl.set_state("day", day)
        for k in ("armed", "day_done", "hit", "side_locked", "win_end"):
            stl.set_state(k, 0)

    # ── 1. Книги: стоп, затем трейлинг-тейк ─────────────────────────────────
    keep = []
    for b in books:
        d = b["dir"]
        px = None
        gap = cur.open <= b["sl"] if d > 0 else cur.open >= b["sl"]
        hit_sl = cur.low <= b["sl"] if d > 0 else cur.high >= b["sl"]
        if gap or hit_sl:
            base = cur.open if gap else b["sl"]
            px = base - d * base * slip_pct
        else:
            fav = (cur.close - b["cost"] / b["q"]) * d
            if not b["arm"] and fav >= tp_arm:
                b["arm"], b["best"] = 1, cur.close
            elif b["arm"]:
                if (b["best"] - cur.close) * d >= tp_back:
                    px = cur.close - d * cur.close * slip_pct
                else:
                    b["best"] = max(b["best"], cur.close) if d > 0 else min(b["best"], cur.close)
        if px is None:
            keep.append(b)
            continue
        await stl.place_order_at(symbol, "sell" if d > 0 else "buy", b["q"], px, cur.time)
        if b["day"] == day:
            stl.set_state("day_done", 1)                # тейк/стоп сегодняшней книги
    books = keep

    # ── 2. Вооружение на первом баре дня — независимо от старых книг ────────
    if not stl.get_state("armed") and not stl.get_state("day_done"):
        big = await stl.get_bars(symbol, tf=1, n=(n_days + 2) * 1500)
        lad = _day_ladder(big, day, is_weekend, bar_off, n_days, d_coef, f_shift, step_count)
        if lad is None:
            stl.set_state("day_done", 1)
        else:
            prev_close, _amp, offs, _close = lad
            stl.set_state("levels_up", [prev_close + o for o in offs])
            stl.set_state("levels_dn", [prev_close - o for o in offs])
            stl.set_state("win_end", hm + hold)
            stl.set_state("armed", 1)

    # ── 3. Ступени сегодняшней лестницы ─────────────────────────────────────
    hit = int(stl.get_state("hit", 0) or 0)
    today = next((b for b in books if b["day"] == day), None)
    if (stl.get_state("armed") and not stl.get_state("day_done")
            and (hm <= int(stl.get_state("win_end") or 0) or hit > 0)):
        up, dn = stl.get_state("levels_up"), stl.get_state("levels_dn")
        locked = int(stl.get_state("side_locked", 0) or 0)
        sizes = _step_sizes(max_contracts, step_count, qty_first)
        guard = guard_pts if invert == 0 else 0.0
        while hit < step_count:
            fire = 0
            if locked in (0, 1) and cur.high >= up[hit] + guard:
                fire = 1
            elif locked in (0, -1) and cur.low <= dn[hit] - guard:
                fire = -1
            if not fire:
                break
            trade_dir = -fire if invert == 0 else fire
            allowed = (trade_dir > 0 and allow_long) or (trade_dir < 0 and allow_short)
            if not allowed or (today and today["dir"] != trade_dir):
                stl.set_state("side_locked", fire)
                break
            qty = sizes[hit] if hit < len(sizes) else 0
            qty = min(qty, max_contracts - (today["q"] if today else 0))
            if qty <= 0:
                hit = step_count
                break
            level = up[hit] if fire > 0 else dn[hit]
            fill = max(level, cur.open) if fire > 0 else min(level, cur.open)
            if invert:
                fill += trade_dir * fill * slip_pct
            await stl.place_order_at(symbol, "buy" if trade_dir > 0 else "sell", qty, fill, cur.time)
            if today is None:
                today = {"day": day, "dir": trade_dir, "q": 0, "cost": 0.0, "arm": 0, "best": 0.0,
                         "sl": dn[-1] - sl_beyond if trade_dir > 0 else up[-1] + sl_beyond}
                books.append(today)
            today["q"] += qty
            today["cost"] += qty * fill
            hit += 1
            locked = fire
            stl.set_state("side_locked", fire)
        stl.set_state("hit", hit)

    # ── 4. Окно прошло без единой сделки — снимаем лестницу ─────────────────
    if stl.get_state("armed") and hm > int(stl.get_state("win_end") or 0) and hit == 0:
        stl.set_state("armed", 0)
        stl.set_state("day_done", 1)

    stl.set_state("books", books)
    gross = sum(b["q"] for b in books)
    net = abs(sum(b["q"] * b["dir"] for b in books))
    stl.set_state("max_gross", max(gross, int(stl.get_state("max_gross", 0) or 0)))
    stl.set_state("max_net", max(net, int(stl.get_state("max_net", 0) or 0)))
    stl.set_state("max_books", max(len(books), int(stl.get_state("max_books", 0) or 0)))


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("Rich Fool HOLD stopped")
