"""Симулятор «радиации» (сетки умной заявки). Пререгистрация: docs/grid-radiation-backtest-2026.md.

Уровни, сторона по рынку, стоп берутся из trader/quik/smart_orders.py (grid_price, grid_levels,
grid_side_for, grid_consumed, grid_places_here, grid_stop_levels, grid_stop_hit): одна семантика с боем.
Гашение и возврат соседей повторены 1:1 с trader/api/quik_smart_orders.py:1655-1658 (_watch_grids):

    so.g_pos += filled * (1 if side_was == "buy" else -1)
    live[f"flip:{level}"] = True                       # этот уровень погас
    woke = [n for n in (level - 1, level + 1) if live.pop(f"flip:{n}", None)]   # соседи ожили

Тейка нет. База (уровень 0) тоже уровень, пока рынок стоит на ней заявка не выставляется (smart_orders.py:378-383).

Исполнение (док, раздел «Симуляция»):
- лимит на L исполняется, если отрезок пути ПЕРЕСЁК L на fill_pen шагов цены, по цене L. Отрезок вверх
  исполняет продажи с a < L+pen <= b, вниз покупки с b <= L-pen < a (порог пересечён заново, а не просто
  лежит позади);
- путь внутри бара: close >= open: open-low-high-close, иначе open-high-low-close; между барами отрезок
  от прошлого close до open (заявки стоят непрерывно);
- уровень, возвращённый внутри отрезка, исполняется не раньше следующего отрезка (список кандидатов
  снимается ДО применения филлов отрезка);
- сторона выставляемой заявки по рынку (grid_side_for): выше рынка продажа, ниже покупка, на уровне рынка
  не выставляется. Выставление идёт в начале каждого отрезка по цене его начала (живой сторож делает это
  на следующем проходе после филла); стоящая заявка сторону не меняет. Возвращённый сосед и уровень 0 на
  базе ждут выставления до начала следующего отрезка;
- стоп: stop_mode=0 close бара за уровнем стопа, выход всей позиции по open следующего бара с полспреда
  против себя; stop_mode=1 по уровню стопа при касании на пути. После стопа сетка завершена (restart="day")
  или в тот же день ставится новая от open следующего бара (restart="after_stop");
- день: сетка от open бара 10:00, до 23:40; в 23:40 позиция закрывается по close бара 23:40 (нет такого
  бара: по close последнего).

Издержки: gross в рублях (пункты * ₽/пт * лот), комиссия двух границ отдельно: мейкер только брокер,
тейкер commission_for(taker=True) без скидок; обе части на выходных удваиваются, как в commission.py.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from trader.quik import smart_orders as so_mod

PV_RI = 1.685138            # ₽/пт RI (у истёкших контрактов спецификации нет)
INST = {"RI": {"pv": PV_RI, "tick": 10.0, "half": 5.0},
        "Si": {"pv": 1.0, "tick": 1.0, "half": 0.5}}
DEFAULTS = {"step": 100.0, "buys": 5, "sells": 5, "lot": 1, "stop_pts": 0.0, "stop_mode": 0,
            "restart": "day", "fill_pen": 1, "tick": 10.0, "half": 5.0,
            "start_min": 600, "end_min": 23 * 60 + 40, "late_start_min": 10}


def _minute(ts) -> int:
    return int(ts) % 86400 // 60


def _day_iso(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


class _Grid:
    """Состояние одной сетки: стоящие заявки (сторона), погашенные уровни, позиция."""

    def __init__(self, base: float, p: dict):
        self.p = p
        self.so = so_mod.SmartOrder(so_id="sim", kind="grid", code="SIM", side="buy", qty=1,
                                    g_step=p["step"], g_buys=p["buys"], g_sells=p["sells"],
                                    g_lot=p["lot"], g_base=base, g_stop_pts=p["stop_pts"])
        self.levels = so_mod.grid_levels(self.so)
        self.px = {k: so_mod.grid_price(self.so, k) for k in self.levels}
        self.live: dict = {}
        # None = заявка не выставлена: ждёт выставления по рынку (ноль на цене постановки, возвращённые соседи)
        self.side = {k: None for k in self.levels}
        self.pending = True
        self.lo, self.hi = so_mod.grid_stop_levels(self.so)
        self.pen = p["fill_pen"] * p["tick"]
        self.place(base)

    def _rebuild(self) -> None:
        sells = sorted((self.px[k] + self.pen, k) for k in self.levels
                       if self.side[k] == "sell" and so_mod.grid_places_here(self.live, k))
        buys = sorted((self.px[k] - self.pen, k) for k in self.levels
                      if self.side[k] == "buy" and so_mod.grid_places_here(self.live, k))
        self.sells, self.buys = sells, buys
        self.sell_t = [t for t, _ in sells]
        self.buy_t = [t for t, _ in buys]

    def place(self, price: float) -> None:
        """Выставить невыставленные уровни по рынку (правило grid_side_for, quik_smart_orders.py:1676-1683)."""
        for k in self.levels:
            if self.side[k] is None and so_mod.grid_places_here(self.live, k):
                side = so_mod.grid_side_for(self.so, k, price)
                px = self.px[k]
                if not ((side == "sell" and px <= price) or (side == "buy" and px >= price)):
                    self.side[k] = side
        self.pending = any(v is None and so_mod.grid_places_here(self.live, k)
                           for k, v in self.side.items())
        self._rebuild()

    def fill(self, k: int) -> tuple[str, float]:
        """Исполнение уровня k, гашение и возврат соседей (1:1 с quik_smart_orders.py:1655-1658)."""
        side = self.side[k]
        self.live[f"flip:{k}"] = True
        for n in (k - 1, k + 1):
            if self.live.pop(f"flip:{n}", None):
                self.side[n] = None                       # выставится по рынку в начале следующего отрезка
                self.pending = True
        self.side[k] = None
        return side, self.px[k]


def _apply(st: dict, side: str, price: float, qty: int, ts: int, kind: str) -> None:
    s = 1 if side == "buy" else -1
    pos, avg = st["pos"], st["avg"]
    if pos == 0 or (pos > 0) == (s > 0):
        st["avg"] = (avg * abs(pos) + price * qty) / (abs(pos) + qty)
    else:
        closed = min(qty, abs(pos))
        st["trades_pnl"].append((price - avg) * closed * (1 if pos > 0 else -1))
        st["tk"].append(kind)
        st["tts"].append(ts)
        if qty > closed:
            st["avg"] = price
    st["pos"] = pos + s * qty
    st["fills"].append((ts, side, price, qty, kind))
    st["pos_path"].append((ts, st["pos"]))
    st["max_pos"] = max(st["max_pos"], abs(st["pos"]))


def _flat(st: dict, price: float, ts: int, kind: str) -> None:
    if st["pos"]:
        _apply(st, "sell" if st["pos"] > 0 else "buy", price, abs(st["pos"]), ts, kind)


def _segment(g: _Grid, st: dict, a: float, b: float, ts: int) -> None:
    if g.pending:
        g.place(a)
    if b == a:
        return
    if b > a:
        cand = [k for t, k in g.sells if a < t <= b]               # снимок ДО филлов отрезка
    else:
        cand = [k for t, k in reversed(g.buys) if b <= t < a]
    for k in cand:
        side, price = g.fill(k)
        _apply(st, side, price, g.p["lot"], ts, "level")
    if cand:
        g._rebuild()


def _new_state() -> dict:
    return {"pos": 0, "avg": 0.0, "fills": [], "trades_pnl": [], "pos_path": [], "max_pos": 0,
            "stops": [], "tk": [], "tts": []}


def simulate_day(bars_day: list, params: dict) -> dict | None:
    """Один торговый день. bars_day = бары [ts,o,h,l,c,v] одного дня МСК (любые минуты).
    None, если бара старта (10:00 +late_start_min) нет: день не торгуется.

    -> {fills, trades_pnl, pnl_pts, pos_path, max_pos, stop_time, n_stops, n_contracts}"""
    p = {**DEFAULTS, **params}
    rows = sorted(bars_day, key=lambda r: r[0])
    body = [r for r in rows if p["start_min"] <= _minute(r[0]) < p["end_min"]]
    if not body or _minute(body[0][0]) > p["start_min"] + p["late_start_min"]:
        return None
    tail = [r for r in rows if _minute(r[0]) >= p["end_min"]]
    st = _new_state()
    hs, mode, n = p["half"], p["stop_mode"], len(body)
    i, g, last = 0, None, None
    while i < n:
        ts, o, h, lw, c = body[i][:5]
        if g is None:
            g, last = _Grid(o, p), o
        # путь бара: от прошлого close до open, затем по правилу close >= open
        path = [o, lw, h, c] if c >= o else [o, h, lw, c]
        stopped = None
        if g.pending:
            g.place(last)
        # быстрый пропуск: стоящие продажи выше рынка, покупки ниже, поэтому без пересечения порогов филлов нет
        quiet = (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                 and lw > (g.buy_t[-1] if g.buy_t else -math.inf))
        if p.get("noskip") or mode == 1 and (g.lo and lw <= g.lo or g.hi and h >= g.hi):
            quiet = False
        if not quiet:
            a = last
            for b in [o] + path[1:]:
                _segment(g, st, a, b, ts)
                if mode == 1 and so_mod.grid_stop_hit(g.so, b):
                    _flat(st, g.lo if (g.lo and b <= g.lo) else g.hi, ts, "stop")
                    stopped = ts
                    break
                a = b
        last = c
        if stopped is None and mode == 0 and g.so.g_stop_pts > 0 and (
                (g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            assert so_mod.grid_stop_hit(g.so, c)
            if i + 1 < n:
                nts, no = body[i + 1][0], body[i + 1][1]
                _flat(st, no - hs if st["pos"] > 0 else no + hs, nts, "stop")
                stopped = nts
            else:
                stopped = ts
        if stopped is not None:
            st["stops"].append(stopped)
            g = None
            if p["restart"] == "after_stop":
                i += 1
                continue
            break
        i += 1
    if st["pos"]:
        end_bar = tail[0] if tail else body[-1]
        _flat(st, end_bar[4], end_bar[0], "eod")
    # ponytail: pos_path хранит позицию после каждого филла, не по барам
    return {"fills": st["fills"], "trades_pnl": st["trades_pnl"], "tk": st["tk"],
            "pnl_pts": sum(st["trades_pnl"]) * 1.0, "pos_path": st["pos_path"],
            "max_pos": st["max_pos"], "stop_time": st["stops"][0] if st["stops"] else None,
            "n_stops": len(st["stops"]),
            "n_contracts": sum(f[3] for f in st["fills"])}


def costs(fills: list, symbol: str, pv: float) -> tuple[float, float]:
    """(мейкер, тейкер) комиссия в рублях по списку филлов."""
    from trader.lab.commission import commission_for
    m = t = 0.0
    for ts, _side, price, qty, _k in fills:
        m += commission_for(symbol, price, qty, pv, taker=False, ts=ts)
        t += commission_for(symbol, price, qty, pv, taker=True, ts=ts)
    return m, t


def day_stats(bars_day: list, start_min: int = 600, end_min: int = 1420) -> dict | None:
    """Описатели дня по окну [start_min, end_min): open/high/low/close, ATR дня (средний размах
    минуты), число пересечений базы (open первого бара) по close, первые 30 и 60 минут."""
    rows = sorted((r for r in bars_day if start_min <= _minute(r[0]) < end_min), key=lambda r: r[0])
    if not rows:
        return None

    def ohlc(rs):
        return (rs[0][1], max(r[2] for r in rs), min(r[3] for r in rs), rs[-1][4]) if rs else (None,) * 4

    base = rows[0][1]
    sgn = [1 if r[4] > base else (-1 if r[4] < base else 0) for r in rows]
    prev, cross = 0, 0
    for s in sgn:
        if s and prev and s != prev:
            cross += 1
        if s:
            prev = s
    first = lambda m: [r for r in rows if _minute(r[0]) < start_min + m]   # noqa: E731
    o, h, lw, c = ohlc(rows)
    return {"date": _day_iso(rows[0][0]), "dow": datetime.fromtimestamp(int(rows[0][0]),
            tz=timezone.utc).weekday(), "nb": len(rows), "o": o, "h": h, "l": lw, "c": c,
            "atr": sum(r[2] - r[3] for r in rows) / len(rows), "cross": cross,
            "m30": ohlc(first(30)), "m60": ohlc(first(60))}


def _by_day(rows: list) -> dict:
    out: dict = {}
    for r in sorted(rows, key=lambda r: r[0]):
        out.setdefault(_day_iso(r[0]), []).append(r)
    return out


def variant_id(p: dict) -> str:
    return (f"s{p['step']:g}_b{p['buys']}_a{p['sells']}_t{p.get('stop_pts', 0):g}"
            f"_m{p.get('stop_mode', 0)}_r{'a' if p.get('restart') == 'after_stop' else 'd'}")


def analyze(rows_by_contract: dict, params_list: list, instrument: str | None = None) -> dict:
    """{ключ контракта: {"days": [описатели], "variants": [{id, p, gross, fee_m, fee_t, nf, st, mp}]}}.
    Ряды вариантов параллельны days. gross в рублях, комиссии отдельно (net = gross - fee)."""
    out = {}
    for key, rows in rows_by_contract.items():
        inst = INST[instrument or ("Si" if key[:2].lower() == "si" else "RI")]
        days = _by_day(rows)
        keep = []
        for d, b in sorted(days.items()):
            st = day_stats(b)
            first = min((r for r in b if 600 <= _minute(r[0]) < 1420), key=lambda r: r[0], default=None)
            if st and first and _minute(first[0]) <= 600 + DEFAULTS["late_start_min"]:
                keep.append((d, b, st))
        res = {"days": [s for _, _, s in keep], "variants": []}
        for params in params_list:
            p = {**inst, **params}
            cols = {"gross": [], "fee_m": [], "fee_t": [], "nf": [], "st": [], "mp": []}
            for _d, b, _s in keep:
                r = simulate_day(b, p)
                fm, ft = costs(r["fills"], key, inst["pv"])
                cols["gross"].append(round(r["pnl_pts"] * inst["pv"] * p.get("lot", 1), 2))
                cols["fee_m"].append(round(fm, 2))
                cols["fee_t"].append(round(ft, 2))
                cols["nf"].append(r["n_contracts"])
                cols["st"].append(_minute(r["stop_time"]) if r["stop_time"] else -1)
                cols["mp"].append(r["max_pos"])
            res["variants"].append({"id": variant_id({**DEFAULTS, **params}), "p": params, **cols})
        out[key] = res
    return out



# ── режим с фильтром «боковик / тренд» (docs/grid-regime-filter-2026.md) ─────────────────────────
SIGS = ("er30", "er60", "er120", "dir30", "dir60", "dir120", "adx")


def signals(rows: list) -> dict:
    """Сигналы тренда на закрытии каждого бара дня; rows отсортированы. Значение на баре i использует
    ТОЛЬКО бары <= i (окно w минут: бары с ts > ts_i - w*60, опорный close = последний бар с ts <= ts_i - w*60).
    Высокое значение = тренд. None, пока окно не покрыто данными дня.
    er*: |c_i - c_ref| / sum|dc|; dir*: |c_i - c_ref| / (max high - min low окна);
    adx: ADX(14) Вайлдера по закрытым M5 (бары M5 строго до M5 текущего бара)."""
    from collections import deque
    n = len(rows)
    ts = [r[0] for r in rows]
    c = [r[4] for r in rows]
    d = [0.0] * n
    for j in range(1, n):
        d[j] = d[j - 1] + abs(c[j] - c[j - 1])
    out = {}
    for w in (30, 60, 120):
        er, dr = [None] * n, [None] * n
        ref, qh, ql = -1, deque(), deque()
        for i in range(n):
            lim = ts[i] - w * 60
            while ref + 1 <= i and ts[ref + 1] <= lim:
                ref += 1
            while qh and rows[qh[-1]][2] <= rows[i][2]:
                qh.pop()
            qh.append(i)
            while ql and rows[ql[-1]][3] >= rows[i][3]:
                ql.pop()
            ql.append(i)
            while qh and qh[0] <= ref:
                qh.popleft()
            while ql and ql[0] <= ref:
                ql.popleft()
            if ref < 0:
                continue
            num = abs(c[i] - c[ref])
            den = d[i] - d[ref]
            rng = rows[qh[0]][2] - rows[ql[0]][3]
            er[i] = num / den if den > 0 else 0.0
            dr[i] = num / rng if rng > 0 else 0.0
        out[f"er{w}"], out[f"dir{w}"] = er, dr
    out["adx"] = _adx(rows)
    return out


def _adx(rows: list, n: int = 14) -> list:
    res = [None] * len(rows)
    cur, curb = None, None
    prev = None                  # прошлый закрытый M5: (h, l, c)
    cnt, trs, pdm, mdm, dxs, adx, val = 0, 0.0, 0.0, 0.0, [], None, None
    for i, r in enumerate(rows):
        b = r[0] // 300
        if curb is not None and b != curb:
            h, lo, cl = cur
            if prev is not None:
                tr = max(h - lo, abs(h - prev[2]), abs(lo - prev[2]))
                up, dn = h - prev[0], prev[1] - lo
                p_, m_ = (up if up > dn and up > 0 else 0.0), (dn if dn > up and dn > 0 else 0.0)
                cnt += 1
                if cnt <= n:
                    trs, pdm, mdm = trs + tr, pdm + p_, mdm + m_
                else:
                    trs, pdm, mdm = trs - trs / n + tr, pdm - pdm / n + p_, mdm - mdm / n + m_
                if cnt >= n and trs > 0:
                    dip, dim = 100 * pdm / trs, 100 * mdm / trs
                    dx = 100 * abs(dip - dim) / (dip + dim) if dip + dim > 0 else 0.0
                    if adx is None:
                        dxs.append(dx)
                        if len(dxs) == n:
                            adx = sum(dxs) / n
                    else:
                        adx = (adx * (n - 1) + dx) / n
                    val = adx
            prev = cur
            cur = None
        if cur is None:
            cur, curb = (r[2], r[3], r[4]), b
        else:
            cur = (max(cur[0], r[2]), min(cur[1], r[3]), r[4])
        res[i] = val
    return res


def simulate_regime(body: list, tail: list, p: dict, sig: list, cfg: dict, schedule: list | None = None) -> dict:
    """Один день с фильтром. body = бары [start_min, end_min), sig = сигнал на закрытии каждого бара body.
    cfg: on_th, off_th, dev_k (шагов), pos_k (0 = выкл), cool (минут).
    Запуск на закрытии бара i-1 при sig < on_th (10:00-21:00, не раньше cool после снятия), база = close бара i-1.
    Снятие на закрытии бара i при sig > off_th, DEV >= dev_k, |pos| >= pos_k или собственном стопе сетки:
    позиция закрывается по open бара i+1 плюс полспреда против себя. Флэт в end_min по close.
    schedule [(i, длина в барах)] вместо сигнала: контроль со случайным запуском (снятие по DEV/POS/стопу/длине)."""
    n, hs = len(body), p["half"]
    st = _new_state()
    sched = dict(schedule) if schedule is not None else None
    g, last, cool_until, plan_end, ep0 = None, None, -1, None, 0
    eps, work, nl = [], 0, 0
    for i in range(n):
        ts, o, h, lw, c = body[i][:5]
        if g is None:
            if i == 0 or (sched is None and ts < cool_until):
                continue
            if sched is not None:
                go = i in sched
            else:
                s, m = sig[i - 1], _minute(body[i - 1][0])
                go = s is not None and s < cfg["on_th"] and 600 <= m <= 1260
            if not go:
                continue
            base = body[i - 1][4]
            g, last, ep0, nl = _Grid(base, p), base, i, nl + 1
            plan_end = i + sched[i] if sched is not None else None
        if g.pending:
            g.place(last)
        if not (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                and lw > (g.buy_t[-1] if g.buy_t else -math.inf)):
            path = [o, lw, h, c] if c >= o else [o, h, lw, c]
            a = last
            for b in [o] + path[1:]:
                _segment(g, st, a, b, ts)
                a = b
        last = c
        work += 1
        why = None
        if sched is None and sig[i] is not None and sig[i] > cfg["off_th"]:
            why = "sig"
        elif abs(c - g.so.g_base) >= cfg["dev_k"] * p["step"]:
            why = "dev"
        elif cfg["pos_k"] and abs(st["pos"]) >= cfg["pos_k"]:
            why = "pos"
        elif plan_end is not None and i + 1 >= plan_end:
            why = "plan"
        elif g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            why = "stop"
        if why:
            if i + 1 < n:
                _flat(st, body[i + 1][1] - hs if st["pos"] > 0 else body[i + 1][1] + hs, body[i + 1][0], "regime")
            else:
                _flat(st, c, ts, "regime")
            eps.append((ep0, i, why))
            g, cool_until = None, ts + 60 + cfg["cool"] * 60
    if g is not None:
        eps.append((ep0, n - 1, "eod"))
    if st["pos"]:
        end_bar = tail[0] if tail else body[-1]
        _flat(st, end_bar[4], end_bar[0], "eod")
    return {"fills": st["fills"], "pnl_pts": sum(st["trades_pnl"]) * 1.0, "max_pos": st["max_pos"],
            "n_contracts": sum(f[3] for f in st["fills"]), "episodes": eps, "work": work, "n_launch": nl,
            "n_removals": sum(1 for e in eps if e[2] != "eod"), "n_body": n}


def _quant(xs: list, q: float) -> float:
    return xs[min(len(xs) - 1, max(0, int(q * (len(xs) - 1))))]


def prep_days(rows: list, p0: dict | None = None, with_sig: bool = True) -> list:
    """Годные дни контракта: описатели, body/tail, сигналы по body (сигнал считается по всем барам дня)."""
    p = {**DEFAULTS, **(p0 or {})}
    out = []
    for _d, b in sorted(_by_day(rows).items()):
        b = sorted(b, key=lambda r: r[0])
        st = day_stats(b)
        ix = [k for k, r in enumerate(b) if p["start_min"] <= _minute(r[0]) < p["end_min"]]
        if not st or not ix or _minute(b[ix[0]][0]) > p["start_min"] + p["late_start_min"]:
            continue
        sg = signals(b) if with_sig else {}
        out.append({"stats": st, "body": [b[k] for k in ix], "tail": [r for r in b if _minute(r[0]) >= p["end_min"]],
                    "sig": {k: [v[j] for j in ix] for k, v in sg.items()}, "full": b})
    return out


_TH_CACHE: dict = {}


def thresholds(days: list, sig: str, on_q: float, off_q: float, ntrain: int) -> tuple[float, float]:
    """Пороги по квантилям сигнала на первых ntrain днях (бары 10:00-21:00)."""
    k = (days[0]["body"][0][:5].__repr__(), days[-1]["body"][0][:5].__repr__(), len(days), sig, ntrain)
    if k not in _TH_CACHE:
        if len(_TH_CACHE) > 64:
            _TH_CACHE.clear()
        _TH_CACHE[k] = sorted(v for d in days[:ntrain] for r, v in zip(d["body"], d["sig"][sig])
                              if v is not None and _minute(r[0]) <= 1260)
    xs = _TH_CACHE[k]
    return _quant(xs, on_q / 100), _quant(xs, off_q / 100)


def build_regime_configs(spec: dict) -> list:
    """Декартово произведение сетки документа; off_q > on_q."""
    out = []
    for p in spec["params"]:
        for sg in spec["sigs"]:
            for on in spec["on_qs"]:
                for off in spec["off_qs"]:
                    if off <= on:
                        continue
                    for dk in spec["dev_ks"]:
                        for pk in spec["pos_ks"]:
                            for cl in spec["cools"]:
                                out.append({"p": p, "sig": sg, "on_q": on, "off_q": off, "dev_k": dk,
                                            "pos_k": pk, "cool": cl})
    return out


def cfg_id(c: dict) -> str:
    return (f"{variant_id({**DEFAULTS, **c['p']})}|{c['sig']}|on{c['on_q']}|off{c['off_q']}"
            f"|dev{c['dev_k']}|pos{c['pos_k']}|cool{c['cool']}")


def _regime_days(days, c, ntrain, inst, day_ix):
    p = {**DEFAULTS, **inst, **c["p"]}
    on_th, off_th = thresholds(days, c["sig"], c["on_q"], c["off_q"], ntrain)
    cfg = {"on_th": on_th, "off_th": off_th, "dev_k": c["dev_k"], "pos_k": c["pos_k"], "cool": c["cool"]}
    res = [simulate_regime(days[k]["body"], days[k]["tail"], p, days[k]["sig"][c["sig"]], cfg) for k in day_ix]
    return p, cfg, res


def _cols(res, key, inst, p):
    cols = {"gross": [], "fee_m": [], "fee_t": [], "nf": [], "work": [], "nl": [], "nr": [], "mp": []}
    for r in res:
        fm, ft = costs(r["fills"], key, inst["pv"])
        cols["gross"].append(round(r["pnl_pts"] * inst["pv"] * p["lot"], 2))
        cols["fee_m"].append(round(fm, 2))
        cols["fee_t"].append(round(ft, 2))
        cols["nf"].append(r["n_contracts"])
        cols["work"].append(r["work"])
        cols["nl"].append(r["n_launch"])
        cols["nr"].append(r["n_removals"])
        cols["mp"].append(r["max_pos"])
    return cols


def _random_schedule(rng, body, durs):
    """Контроль: те же длины эпизодов, старты случайные без перекрытия (индексы запуска i >= 1, минута
    предыдущего бара 10:00-21:00)."""
    ok = [i for i in range(1, len(body)) if _minute(body[i - 1][0]) <= 1260]
    taken, out = [], []
    for dur in durs:
        for _ in range(30):
            s = rng.choice(ok)
            e = min(len(body), s + dur)
            if all(e <= a or s >= b for a, b in taken):
                taken.append((s, e))
                out.append((s, dur))
                break
    return out


def _sum_net(cl):
    return (round(sum(cl["gross"]) - sum(cl["fee_m"]), 1), round(sum(cl["gross"]) - sum(cl["fee_t"]), 1))


def run_regime(arg: dict) -> dict:
    """mode=regime_scan: configs = [{"p", "sig", "on_q", "off_q", "dev_k", "pos_k", "cool"}], только первые 2/3
    дней (отбор), сводка обучения. mode=regime_days: те же configs, дневные ряды по всем дням + база без
    фильтра + контроль (arg["draws"] розыгрышей на отложенной трети). mode=regime_info: информативность:
    запуск каждые 30 минут на 120 минут без фильтра, сигнал на запуске и net эпизода (arg["params"]).
    arg["chunk"] = [i, n] берёт configs[i::n]."""
    import random
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "REGIME", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows)
    nt = len(days) * 2 // 3
    configs = arg.get("configs") or (build_regime_configs(arg["spec"]) if arg.get("spec") else [])
    if arg.get("chunk"):
        i, n = arg["chunk"]
        configs = configs[i::n]
    out = {"id": "REGIME", "mode": arg["mode"], "symbol": key, "n_days": len(days), "n_train": nt}
    if arg["mode"] == "regime_scan":
        res = []
        for c in configs:
            p, cfg, rr = _regime_days(days, c, nt, inst, range(nt))
            cl = _cols(rr, key, inst, p)
            nm, nt_ = _sum_net(cl)
            res.append({"cid": cfg_id(c), "net_m": nm, "net_t": nt_, "work": sum(cl["work"]),
                        "bars": sum(r["n_body"] for r in rr), "days_work": sum(1 for x in cl["nl"] if x),
                        "nl": sum(cl["nl"]), "nr": sum(cl["nr"]),
                        "worst": min(g - m for g, m in zip(cl["gross"], cl["fee_t"])), "th": [cfg["on_th"], cfg["off_th"]]})
        out["configs"] = res
    elif arg["mode"] == "regime_days":
        out["days"] = [d["stats"] for d in days]
        base = {}
        for c in configs:
            pk = variant_id({**DEFAULTS, **c["p"]})
            if pk not in base:
                p = {**DEFAULTS, **inst, **c["p"]}
                rr = [simulate_day(d["body"] + d["tail"], p) for d in days]
                for r in rr:
                    r["work"], r["n_launch"], r["n_removals"] = 1, 1, 0
                base[pk] = _cols(rr, key, inst, p)
        out["base"] = base
        out["configs"] = []
        for c in configs:
            p, cfg, rr = _regime_days(days, c, nt, inst, range(len(days)))
            item = {"cid": cfg_id(c), "c": c, "th": [cfg["on_th"], cfg["off_th"]], **_cols(rr, key, inst, p),
                    "eps": [[(e[1] - e[0] + 1, e[2]) for e in r["episodes"]] for r in rr]}
            ctrl = []
            for dd in range(arg.get("draws", 0)):
                rng = random.Random(1000 + dd)
                rc = []
                for k in range(nt, len(days)):
                    d = days[k]
                    sch = _random_schedule(rng, d["body"], [e[0] for e in item["eps"][k]])
                    rc.append(simulate_regime(d["body"], d["tail"], p, d["sig"][c["sig"]], cfg, schedule=sch))
                cl = _cols(rc, key, inst, p)
                nm, nt_ = _sum_net(cl)
                ctrl.append({"net_m": nm, "net_t": nt_, "work": sum(cl["work"])})
            item["ctrl"] = ctrl
            out["configs"].append(item)
    elif arg["mode"] == "regime_info":
        out["episodes"] = []
        cfg = {"on_th": 0, "off_th": 0, "dev_k": 1e9, "pos_k": 0, "cool": 0}
        for pi, p0 in enumerate(arg["params"]):
            p = {**DEFAULTS, **inst, **p0}
            for k, d in enumerate(days):
                body = d["body"]
                for i in range(1, len(body)):
                    m = _minute(body[i - 1][0])
                    if m % 30 or m > 1230:
                        continue
                    dur = sum(1 for b in body[i:] if b[0] < body[i][0] + 7200)        # 120 минут, бары разрежены
                    r = simulate_regime(body, d["tail"], p, d["sig"]["er30"], cfg, schedule=[(i, dur)])
                    fm, ft = costs(r["fills"], key, inst["pv"])
                    g = r["pnl_pts"] * inst["pv"]
                    out["episodes"].append({"p": pi, "day": k, "m": m, "net_m": round(g - fm, 1), "net_t": round(g - ft, 1),
                                            "sig": {s: d["sig"][s][i - 1] for s in SIGS}})
    return out


# ── третья редакция: опоздание на Z баров (docs/grid-regime-filter-2026.md, «Третья редакция») ───
def _segment_wait(g: _Grid, pend: list, a: float, b: float, i: int, z: int) -> None:
    """Как _segment, но прохождение уровня не исполняет заявку: уровень уходит в ожидание до закрытия
    бара i+z-1 (z=1: закрытие бара прохода). Заявки сетки держатся на стороне STL, не в стакане."""
    if g.pending:
        g.place(a)
    if b == a:
        return
    if b > a:
        cand = [k for t, k in g.sells if a < t <= b]
    else:
        cand = [k for t, k in reversed(g.buys) if b <= t < a]
    for k in cand:
        pend.append({"k": k, "side": g.side[k], "px": g.px[k], "due": i + z - 1})
        g.side[k] = "wait"
    if cand:
        g._rebuild()


def simulate_delay(body: list, tail: list, p: dict, z: int, k: int | None, own: bool = False,
                   skip_p: float | None = None, rng=None) -> dict:
    """День по третьей редакции. Сетка от open бара 10:00, до стопа или флэта в конце дня.
    На закрытии бара due: импульс (цена ушла от уровня дальше k шагов в сторону прохода; k=None = без
    переноса) -> входа нет, база переносится к close (новая сетка, гашения сброшены, позиция остаётся);
    иначе вход «от планки»: покупка по close + полспреда, продажа по close - полспреда (весь вход тейкер).
    own=True (справочно): «своя сторона»: покупка по close - полспреда, продажа по close + полспреда,
    исполняется, если СЛЕДУЮЩИЙ бар прошёл цену на 1 тик; иначе уровень снова в работе.
    skip_p: контроль, вместо проверки импульса пропуск входа с вероятностью skip_p (rng.random())."""
    n, hs, tick = len(body), p["half"], p["tick"]
    st = _new_state()
    g, last = _Grid(body[0][1], p), body[0][1]
    pend: list = []
    stat = {"dec": 0, "skip": 0, "entries": 0, "miss": 0}
    stopped = False
    for i in range(n):
        ts, o, h, lw, c = body[i][:5]
        if g.pending:
            g.place(last)
        quiet = (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                 and lw > (g.buy_t[-1] if g.buy_t else -math.inf))
        if not quiet:
            path = [o, lw, h, c] if c >= o else [o, h, lw, c]
            a = last
            for b in [o] + path[1:]:
                _segment_wait(g, pend, a, b, i, z)
                a = b
        last = c
        due = [e for e in pend if e["due"] <= i]
        if due:
            pend = [e for e in pend if e["due"] > i]
            for e in due:
                stat["dec"] += 1
                lvl, side, L = e["k"], e["side"], e["px"]
                if skip_p is not None:
                    imp = rng.random() < skip_p
                elif k is None:
                    imp = False
                else:
                    imp = (c - L > k * p["step"]) if side == "sell" else (L - c > k * p["step"])
                if imp:
                    stat["skip"] += 1
                    g, last, pend = _Grid(c, p), c, []
                    break
                if not own:
                    _apply(st, side, c + hs if side == "buy" else c - hs, p["lot"], ts, "delay")
                    g.fill(lvl)
                    stat["entries"] += 1
                else:
                    lim = c - hs if side == "buy" else c + hs
                    nb = body[i + 1] if i + 1 < n else None
                    if nb is not None and ((side == "buy" and nb[3] <= lim - tick)
                                           or (side == "sell" and nb[2] >= lim + tick)):
                        _apply(st, side, lim, p["lot"], nb[0], "own")
                        g.fill(lvl)
                        stat["entries"] += 1
                    else:
                        g.side[lvl] = None            # не исполнилась: уровень снова в работе по рынку
                        g.pending = True
                        stat["miss"] += 1
            g._rebuild()
        if g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            if i + 1 < n:
                _flat(st, body[i + 1][1] - hs if st["pos"] > 0 else body[i + 1][1] + hs, body[i + 1][0], "stop")
            else:
                _flat(st, c, ts, "stop")
            stopped = True
            break
    if st["pos"]:
        end_bar = tail[0] if tail else body[-1]
        _flat(st, end_bar[4], end_bar[0], "eod")
    return {"fills": st["fills"], "pnl_pts": sum(st["trades_pnl"]) * 1.0, "max_pos": st["max_pos"],
            "n_contracts": sum(f[3] for f in st["fills"]), "stat": stat, "stopped": stopped}


def costs_delay(fills: list, symbol: str, pv: float) -> tuple[float, float]:
    """(по видам, полный тейкер): вход «от планки», стоп и флэт тейкер; «своя сторона» мейкер.
    Вторая граница: все филлы тейкер."""
    from trader.lab.commission import commission_for
    a = b = 0.0
    for ts, _side, price, qty, kind in fills:
        t = commission_for(symbol, price, qty, pv, taker=True, ts=ts)
        a += commission_for(symbol, price, qty, pv, taker=False, ts=ts) if kind == "own" else t
        b += t
    return a, b


def run_delay(arg: dict) -> dict:
    """mode=delay_days: params, zs, ks (null = без переноса), owns ([false, true]), draws (контроль случайным
    пропуском той же доли входов на отложенной трети, только own=false), chunk [i, n] по списку конфигураций."""
    import random
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "DELAY", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows)
    nt = len(days) * 2 // 3
    cfgs = [(pi, z, k, ow) for pi in range(len(arg["params"])) for z in arg["zs"] for k in arg["ks"]
            for ow in arg.get("owns", [False])]
    if arg.get("chunk"):
        i, n = arg["chunk"]
        cfgs = cfgs[i::n]
    out = {"id": "DELAY", "symbol": key, "n_days": len(days), "n_train": nt, "days": [d["stats"] for d in days],
           "base": {}, "configs": []}

    def cols(rs, pk):
        c = {"gross": [], "fee_m": [], "fee_t": [], "nf": [], "dec": [], "skip": [], "ent": [], "mp": []}
        for r in rs:
            fm, ft = costs_delay(r["fills"], key, inst["pv"]) if "stat" in r else costs(r["fills"], key, inst["pv"])
            c["gross"].append(round(r["pnl_pts"] * inst["pv"] * pk["lot"], 2))
            c["fee_m"].append(round(fm, 2))
            c["fee_t"].append(round(ft, 2))
            c["nf"].append(r["n_contracts"])
            c["mp"].append(r["max_pos"])
            s = r.get("stat") or {"dec": 0, "skip": 0, "entries": 0}
            c["dec"].append(s["dec"])
            c["skip"].append(s["skip"])
            c["ent"].append(s["entries"])
        return c

    for pi in sorted({c[0] for c in cfgs}):
        p = {**DEFAULTS, **inst, **arg["params"][pi]}
        out["base"][str(pi)] = cols([simulate_day(d["body"] + d["tail"], p) for d in days], p)
    for pi, z, k, ow in cfgs:
        p = {**DEFAULTS, **inst, **arg["params"][pi]}
        rs = [simulate_delay(d["body"], d["tail"], p, z, k, own=ow) for d in days]
        item = {"pi": pi, "z": z, "k": k, "own": ow, **cols(rs, p)}
        if arg.get("draws") and not ow:
            dec = sum(item["dec"][nt:])
            share = sum(item["skip"][nt:]) / dec if dec else 0.0
            item["share"] = share
            ctrl = []
            for dd in range(arg["draws"]):
                rng = random.Random(1000 + dd)
                rc = cols([simulate_delay(days[j]["body"], days[j]["tail"], p, z, k, skip_p=share, rng=rng)
                           for j in range(nt, len(days))], p)
                ctrl.append({"net_t": round(sum(rc["gross"]) - sum(rc["fee_t"]), 1),
                             "daily_t": [round(g - f, 1) for g, f in zip(rc["gross"], rc["fee_t"])]})
            item["ctrl"] = ctrl
        out["configs"].append(item)
    return out


# ── широкая многодневная сетка (docs/grid-radiation-wide-2026.md) ────────────────────────────────
def _shift_grid(g: _Grid, d: float) -> None:
    """Смена контракта: уровни и стоп сдвигаются на разницу цен контрактов."""
    g.px = {k: v + d for k, v in g.px.items()}
    if g.lo:
        g.lo += d
    if g.hi:
        g.hi += d
    g.so.g_base += d
    g._rebuild()


def _fee_rows(fills: list, symbol: str, pv: float) -> list:
    """[(ts, fee_maker, fee_taker)] по филлам: мейкер-граница только на лимитных филлах уровней
    (брокер), прочие (перенос, стоп) тейкер; тейкер-граница все филлы тейкер."""
    from trader.lab.commission import commission_for
    out = []
    for ts, _s, price, qty, kind in fills:
        t = commission_for(symbol, price, qty, pv, taker=True, ts=ts)
        m = commission_for(symbol, price, qty, pv, taker=False, ts=ts) if kind == "level" else t
        out.append((ts, m, t))
    return out


def simulate_wide(bars: list, rolls: dict, specs: list, p: dict, center: float) -> dict:
    """Многодневная сетка без дневного флэта. bars = [ts,o,h,l,c,v,cidx] от бара старта до конца окна, rolls =
    {индекс первого бара нового контракта: разница цен new-old}, specs[cidx] = {key, pv, margin}.
    p: step (пт), buys, sells, stop_pts, tick, half, fill_pen, lot. Сетка стоит от бара 0 (цена = open) вокруг center.
    Смена контракта: позиция закрывается в старом по его последней цене и открывается в новом по цене нового
    контракта с полспреда против (стоимость переноса = полспреда + две комиссии), уровни и стоп сдвигаются на
    разницу цен. Стоп (close за краем стопа) закрывает позицию по open следующего бара с полспреда и завершает
    сетку; далее только учёт по дням."""
    n, hs = len(bars), p["half"]
    g = _Grid(center, p)
    g.px = {k: round(v / p["tick"]) * p["tick"] for k, v in g.px.items()}
    g.side = {k: None for k in g.levels}
    g.pending = True
    last = bars[0][1]
    g.place(last)
    st = _new_state()
    segs = [(st, specs[bars[0][6]])]
    cur, alive, stop_ts = bars[0][6], True, None
    daily, unit_cum, ref, go_max, sumpos, nb = [], 0.0, last, 0.0, 0.0, 0
    cur_day = _day_iso(bars[0][0])

    def gross():
        return sum(sum(s["trades_pnl"]) * sp["pv"] for s, sp in segs) + st["pos"] * (last - st["avg"]) * segs[-1][1]["pv"]

    for j in range(n):
        ts, o, h, lw, c, _v, ci = bars[j]
        if ci != cur:
            basis = rolls[j]
            pos = st["pos"]
            last_new = last + basis
            if alive:
                st_new = _new_state()
                if pos:
                    _apply(st, "sell" if pos > 0 else "buy", last, abs(pos), ts, "roll")
                    _apply(st_new, "buy" if pos > 0 else "sell", last_new + hs if pos > 0 else last_new - hs,
                           abs(pos), ts, "roll")
                st_new["max_pos"] = abs(pos)
                _shift_grid(g, basis)
            else:
                st_new = _new_state()
            segs.append((st_new, specs[ci]))
            st, cur, last, ref = st_new, ci, last_new, ref + basis
        d = _day_iso(ts)
        if d != cur_day:
            cur_day, ref = d, last
        if alive:
            if g.pending:
                g.place(last)
            if not (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                    and lw > (g.buy_t[-1] if g.buy_t else -math.inf)):
                path = [o, lw, h, c] if c >= o else [o, h, lw, c]
                a = last
                for b in [o] + path[1:]:
                    _segment(g, st, a, b, ts)
                    a = b
            go_max = max(go_max, st["max_pos"] * segs[-1][1]["margin"])
            last = c
            sumpos += abs(st["pos"])
            nb += 1
            if g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
                if st["pos"]:
                    nxt = bars[j + 1] if j + 1 < n and bars[j + 1][6] == cur else None
                    px = nxt[1] if nxt else c
                    _flat(st, px - hs if st["pos"] > 0 else px + hs, nxt[0] if nxt else ts, "stop")
                alive, stop_ts = False, ts
        else:
            last = c
            nb += 1
        if j == n - 1 or _day_iso(bars[j + 1][0]) != d:
            unit_cum += (c - ref) * segs[-1][1]["pv"]
            daily.append((d, gross(), st["pos"], unit_cum, ts))
    fills = []
    fee = []
    for s, sp in segs:
        fee += _fee_rows(s["fills"], sp["key"], sp["pv"])
        fills += s["fills"]
    cm, ct, di = [], [], 0
    fee.sort()
    acc_m = acc_t = 0.0
    for d, _g, _p, _u, tsd in daily:
        while di < len(fee) and fee[di][0] <= tsd:
            acc_m, acc_t, di = acc_m + fee[di][1], acc_t + fee[di][2], di + 1
        cm.append(round(acc_m, 2))
        ct.append(round(acc_t, 2))
    realized = sum(sum(s["trades_pnl"]) * sp["pv"] for s, sp in segs)
    return {"dates": [x[0] for x in daily], "eq_g": [round(x[1], 2) for x in daily], "pos": [x[2] for x in daily],
            "unit": [round(x[3], 2) for x in daily], "fee_m": cm, "fee_t": ct,
            "realized": round(realized, 2), "mtm": round(daily[-1][1], 2), "rounds": sum(len(s["trades_pnl"]) for s, _ in segs),
            "max_pos": max(s["max_pos"] for s, _ in segs), "go_max": round(go_max, 2), "stop_ts": stop_ts,
            "mean_pos": sumpos / max(1, nb), "n_fills": len(fills),
            "roll_fills": sum(1 for f in fills if f[4] == "roll"), "last_pos": st["pos"]}


def prep_wide(contracts: list, end_iso: str, load) -> dict:
    """Загрузка контрактов, расписание активного контракта по дням (монотонно, смена когда следующий не меньше
    по дневному объёму или у текущего нет баров), разницы цен контрактов на сменах. contracts = [{key, lo, hi, spec}]
    (lo/hi ISO-даты окна активности псевдоконтракта). load(key) -> бары [ts,o,h,l,c,v]."""
    from bisect import bisect_right
    cache, rows = {}, []
    for c in contracts:
        if c["key"] not in cache:
            cache[c["key"]] = load(c["key"])
        r = [x for x in cache[c["key"]] if _day_iso(x[0]) >= "2026-01-01" and _day_iso(x[0]) <= end_iso
             and (not c.get("lo") or _day_iso(x[0]) >= c["lo"]) and (not c.get("hi") or _day_iso(x[0]) < c["hi"])]
        rows.append(sorted(r, key=lambda x: x[0]))
    byday = []
    for r in rows:
        d: dict = {}
        for x in r:
            d.setdefault(_day_iso(x[0]), []).append(x)
        byday.append(d)
    days = sorted({d for bd in byday for d in bd})
    a, bars, rolls, notes = 0, [], {}, []
    prev_last = None
    for d in days:
        while a + 1 < len(rows) and d in byday[a + 1] and (d not in byday[a] or
                                                           sum(x[5] for x in byday[a + 1][d]) >= sum(x[5] for x in byday[a][d])):
            a += 1
        if d not in byday[a]:
            continue
        day_bars = byday[a][d]
        if prev_last is not None and prev_last[1] != a:
            oldts, olda, oldc = prev_last[0], prev_last[1], prev_last[2]
            full = cache[contracts[a]["key"]]
            tss = [x[0] for x in full]
            i = bisect_right(tss, oldts) - 1
            if i >= 0 and oldts - full[i][0] <= 1800:
                basis, mode = full[i][4] - oldc, "overlap"
            else:
                basis, mode = day_bars[0][1] - oldc, "jump"
            rolls[len(bars)] = basis
            notes.append({"date": d, "from": contracts[olda]["key"], "to": contracts[a]["key"], "basis": round(basis, 4), "mode": mode})
        for x in day_bars:
            bars.append([x[0], x[1], x[2], x[3], x[4], x[5], a])
        prev_last = (day_bars[-1][0], a, day_bars[-1][4])
    return {"bars": bars, "rolls": rolls, "notes": notes}


def run_wide(arg: dict) -> dict:
    """mode=wide: instrument, contracts [{key, lo, hi, spec{key,pv,margin,tick}}], starts [ISO], centers [1,2],
    ns, stops (доли H), end ISO. Возвращает по каждому старту H, расписание контрактов и конфигурации."""
    from datetime import date as _d, timedelta
    from trader.lab.footprints import common
    end = arg.get("end", "2026-09-30")
    contracts = arg["contracts"]
    specs = [c["spec"] for c in contracts]
    pw = prep_wide(contracts, end, lambda k: common.load_bars(k, "2026-01-01", end))
    bars, rolls = pw["bars"], pw["rolls"]
    if not bars:
        return {"id": "WIDE", "instrument": arg["instrument"], "error": "нет баров"}
    tick = specs[0]["tick"]
    out = {"id": "WIDE", "instrument": arg["instrument"], "notes": pw["notes"], "data_first": _day_iso(bars[0][0]),
           "data_last": _day_iso(bars[-1][0]), "starts": []}
    for sd in arg["starts"]:
        s = next((i for i, b in enumerate(bars) if _day_iso(b[0]) >= sd and _minute(b[0]) >= 600), None)
        item = {"start": sd}
        if s is None or _d.fromisoformat(_day_iso(bars[s][0])) - _d.fromisoformat(sd) > timedelta(days=5):
            item["error"] = "нет баров на старте"
            out["starts"].append(item)
            continue
        hist = bars[:s]
        if len(hist) < 500:
            item["error"] = "нет истории окна высоты"
            out["starts"].append(item)
            continue
        adj, acc = [0.0] * s, 0.0
        for j in range(s - 1, -1, -1):
            if j + 1 in rolls and j + 1 < s:
                acc += rolls[j + 1]
            adj[j] = acc
        # сдвиг контракта на стыке истории и старта (бар s может быть первым нового контракта)
        if s in rolls:
            adj = [a + rolls[s] for a in adj]
        hi_ = max(b[2] + a for b, a in zip(hist, adj))
        lo_ = min(b[3] + a for b, a in zip(hist, adj))
        H = hi_ - lo_
        item.update({"H": H, "hmax": hi_, "hmin": lo_, "hist_first": _day_iso(hist[0][0]), "hist_days": len({_day_iso(b[0]) for b in hist}),
                     "price0": bars[s][1], "start_bar_day": _day_iso(bars[s][0]), "configs": []})
        sub = bars[s:]
        sub_rolls = {j - s: v for j, v in rolls.items() if j > s}
        for cc in arg["centers"]:
            ctr = (hi_ + lo_) / 2 if cc == 1 else sub[0][1]
            for nn in arg["ns"]:
                for sf in arg["stops"]:
                    p = {**DEFAULTS, "step": H / (2 * nn), "buys": nn, "sells": nn, "stop_pts": sf * H, "tick": tick,
                         "half": tick / 2, "lot": 1, "fill_pen": 1}
                    r = simulate_wide(sub, sub_rolls, specs, p, ctr)
                    # пассивный контроль: средний объём сетки, цена от старта до конца
                    item["configs"].append({"c": cc, "n": nn, "s": sf, **r})
        out["starts"].append(item)
    return out


# ── четвёртая редакция: сброс позиции после импульса и перенос базы (docs/grid-regime-filter-2026.md) ─
def impulse_flags(full: list, body: list, imp_min: float, imp_bars: int, imp_max: float = 250) -> list:
    """Импульс на закрытии каждого бара body (детектор retest.find_impulse, оба направления; по барам <= i).
    full = все бары дня (ATR считается за 60 баров до старта импульса внутри дня)."""
    from trader.lab.runtime import Bar
    from trader.lab.strategies.retest import find_impulse
    bars = [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5] if len(r) > 5 else 0) for r in full]
    prm = {"imp_min": imp_min, "imp_max": imp_max, "imp_bars": imp_bars, "atr_n": 60}
    hit = {bars[i].time for i in range(len(bars)) if find_impulse(bars, i, prm, 1) or find_impulse(bars, i, prm, -1)}
    return [r[0] in hit for r in body]


def simulate_reset(body: list, tail: list, p: dict, imp: list, cool: int, schedule: set | None = None) -> dict:
    """День с дневной сеткой (от open бара 10:00, флэт в конце дня, стоп закрывает позицию и завершает сетку).
    На закрытии бара i с импульсом (imp[i], или i in schedule в контроле): заявки сняты, вся позиция закрывается
    рыночно по open бара i+1 плюс полспреда против (тейкер), база переносится к close бара i, гашения сброшены;
    сетка работает сразу с бара i+1 (cool=0) или с первого бара не раньше чем через cool минут."""
    n, hs = len(body), p["half"]
    st = _new_state()
    g, last = _Grid(body[0][1], p), body[0][1]
    pause_until, base = -1, None
    resets = resets_pos = 0
    for i in range(n):
        ts, o, h, lw, c = body[i][:5]
        if g is None:
            if ts < pause_until:
                continue
            g, last = _Grid(base, p), o
        if g.pending:
            g.place(last)
        if not (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                and lw > (g.buy_t[-1] if g.buy_t else -math.inf)):
            path = [o, lw, h, c] if c >= o else [o, h, lw, c]
            a = last
            for b in [o] + path[1:]:
                _segment(g, st, a, b, ts)
                a = b
        last = c
        if g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            if st["pos"]:
                if i + 1 < n:
                    _flat(st, body[i + 1][1] - hs if st["pos"] > 0 else body[i + 1][1] + hs, body[i + 1][0], "stop")
                else:
                    _flat(st, c, ts, "stop")
            st["stops"].append(ts)
            g = None
            break
        trig = (i in schedule) if schedule is not None else imp[i]
        if trig and i + 1 < n:
            resets += 1
            if st["pos"]:
                resets_pos += 1
                _flat(st, body[i + 1][1] - hs if st["pos"] > 0 else body[i + 1][1] + hs, body[i + 1][0], "reset")
            g, base, pause_until = None, c, ts + 60 + cool * 60
    if st["pos"]:
        end_bar = tail[0] if tail else body[-1]
        _flat(st, end_bar[4], end_bar[0], "eod")
    kinds = st["tk"]
    pn = st["trades_pnl"]
    return {"fills": st["fills"], "pnl_pts": sum(pn) * 1.0, "max_pos": st["max_pos"],
            "n_contracts": sum(f[3] for f in st["fills"]), "resets": resets, "resets_pos": resets_pos,
            "reset_pnl": sum(x for x, k in zip(pn, kinds) if k == "reset"),
            "stop_pnl": sum(x for x, k in zip(pn, kinds) if k == "stop"), "n_stops": len(st["stops"])}


def run_reset(arg: dict) -> dict:
    """mode=reset_days: params, imp_mins, imp_bars_list, cools, draws; chunk [i, n] по парам (imp_min, imp_bars).
    Возвращает дневные ряды конфигураций и базу (исходная сетка без сброса) по всем дням контракта."""
    import random
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "RESET", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows, with_sig=False)
    nt = len(days) * 2 // 3
    pairs = [(a, b) for a in arg["imp_mins"] for b in arg["imp_bars_list"]]
    if arg.get("chunk"):
        i, n = arg["chunk"]
        pairs = pairs[i::n]
    out = {"id": "RESET", "symbol": key, "n_days": len(days), "n_train": nt, "days": [d["stats"] for d in days],
           "base": {}, "configs": []}
    pv = inst["pv"]

    def cols(rs, p):
        c = {"gross": [], "fee_m": [], "fee_t": [], "rs": [], "rsp": [], "rpnl": [], "spnl": [], "ns": []}
        for r in rs:
            fee = _fee_rows(r["fills"], key, pv)
            c["gross"].append(round(r["pnl_pts"] * pv * p["lot"], 2))
            c["fee_m"].append(round(sum(x[1] for x in fee), 2))
            c["fee_t"].append(round(sum(x[2] for x in fee), 2))
            c["rs"].append(r.get("resets", 0))
            c["rsp"].append(r.get("resets_pos", 0))
            c["rpnl"].append(round(r.get("reset_pnl", 0.0) * pv * p["lot"], 2))
            c["spnl"].append(round(r.get("stop_pnl", 0.0) * pv * p["lot"], 2))
            c["ns"].append(r.get("n_stops", 0))
        return c

    for pi in range(len(arg["params"])):
        p = {**DEFAULTS, **inst, **arg["params"][pi]}
        rr = []
        for d in days:
            r = simulate_day(d["body"] + d["tail"], p)
            pn, kd = r["trades_pnl"], r["tk"]
            rr.append({**r, "stop_pnl": sum(x for x, k in zip(pn, kd) if k == "stop")})
        out["base"][str(pi)] = cols(rr, p)
    for imp_min, imp_bars in pairs:
        flags = [impulse_flags(d["full"], d["body"], imp_min, imp_bars) for d in days]
        for pi in range(len(arg["params"])):
            p = {**DEFAULTS, **inst, **arg["params"][pi]}
            for cool in arg["cools"]:
                rs = [simulate_reset(d["body"], d["tail"], p, flags[k], cool) for k, d in enumerate(days)]
                item = {"pi": pi, "imp_min": imp_min, "imp_bars": imp_bars, "cool": cool, **cols(rs, p)}
                ctrl = []
                for dd in range(arg.get("draws", 0)):
                    rng = random.Random(2000 + dd)
                    rc = []
                    for k in range(nt, len(days)):
                        d = days[k]
                        cnt = item["rs"][k]
                        sch = set(rng.sample(range(0, max(1, len(d["body"]) - 1)), min(cnt, max(1, len(d["body"]) - 1))))
                        rc.append(simulate_reset(d["body"], d["tail"], p, flags[k], cool, schedule=sch))
                    cl = cols(rc, p)
                    ctrl.append({"net_t": round(sum(cl["gross"]) - sum(cl["fee_t"]), 1), "rpnl": round(sum(cl["rpnl"]), 1)})
                item["ctrl"] = ctrl
                out["configs"].append(item)
    return out


# ── часть 2б flex-radiation: короткая сетка на волатильности после импульса ──────────────────────
def atr10_of(full: list, body: list) -> list:
    """Текущая волатильность на закрытии каждого бара body: средний размах 10 баров, оканчивающихся этим баром
    (бары дня с 07:00 включая утро; None пока меньше 10 баров)."""
    pos = {r[0]: k for k, r in enumerate(full)}
    out = []
    for r in body:
        k = pos[r[0]]
        out.append(sum(x[2] - x[3] for x in full[k - 9:k + 1]) / 10 if k >= 9 else None)
    return out


def short_events(full: list, body: list, imp_min: float) -> dict:
    """События r1.find_impulses (imp_min..250 ATR за <= 20 баров) -> {'conf': {i: H}, 'peak': {i: H}} по индексам body.
    Старт 'conf' на закрытии бара подтверждения (причинно), 'peak' на закрытии бара пика (известно только
    постфактум: справочно)."""
    from trader.lab.footprints.r1_retest import find_impulses
    pos = {r[0]: k for k, r in enumerate(body)}
    out: dict = {"conf": {}, "peak": {}}
    for e in find_impulses(full, {"atr_n": 60, "imp_bars": 20, "imp_min": imp_min, "imp_max": 250, "pb": 10}):
        for key, fi in (("conf", e["i_conf"]), ("peak", e["t_p"])):
            k = pos.get(full[fi][0])
            if k is not None and k < len(body) - 1 and k not in out[key]:
                out[key][k] = e["strength"]
    return out


def simulate_short(body: list, tail: list, p: dict, starts: dict, atr: list, s: float, n_lv: int, T: int, tf: int = 1) -> dict:
    """Короткие сетки после событий. starts = {индекс бара body: H}: на закрытии бара ставится сетка (база = close,
    шаг = s x ATR_тек, n_lv уровней в каждую сторону), живёт T баров, затем снимается с закрытием позиции
    рыночно по open следующего бара + полспреда; стоп за краем на 1 шаг (закрытие позиции, конец сетки);
    одна сетка за раз; события внутри жизни сетки пропускаются."""
    n, hs, tick = len(body), p["half"], p["tick"]
    st = _new_state()
    g, last, end_i, end_ts, grids, stops = None, None, -1, 0, 0, 0
    for i in range(n):
        ts, o, h, lw, c = body[i][:5]
        if g is not None:
            if g.pending:
                g.place(last)
            if not (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                    and lw > (g.buy_t[-1] if g.buy_t else -math.inf)):
                path = [o, lw, h, c] if c >= o else [o, h, lw, c]
                a = last
                for b in [o] + path[1:]:
                    _segment(g, st, a, b, ts)
                    a = b
            last = c
            why = None
            if (g.lo and c <= g.lo) or (g.hi and c >= g.hi):
                why = "stop"
            elif (i >= end_i) if tf == 1 else (ts + 60 >= end_ts):          # T баров M1 или T баров tf по времени
                why = "life"
            if why:
                if st["pos"]:
                    if i + 1 < n:
                        _flat(st, body[i + 1][1] - hs if st["pos"] > 0 else body[i + 1][1] + hs, body[i + 1][0], why)
                    else:
                        _flat(st, c, ts, why)
                stops += why == "stop"
                g = None
            continue
        if i in starts and atr[i]:
            step = max(s * atr[i], tick)
            q = {**p, "step": step, "buys": n_lv, "sells": n_lv, "stop_pts": step}
            g = _Grid(c, q)
            g.px = {k: round(v / tick) * tick for k, v in g.px.items()}
            g.side = {k: None for k in g.levels}
            g.pending = True
            last, end_i, end_ts, grids = c, i + T, ts + 60 + T * tf * 60, grids + 1
    if st["pos"]:
        end_bar = tail[0] if tail else body[-1]
        _flat(st, end_bar[4], end_bar[0], "eod")
    return {"fills": st["fills"], "pnl_pts": sum(st["trades_pnl"]) * 1.0, "max_pos": st["max_pos"],
            "n_contracts": sum(f[3] for f in st["fills"]), "grids": grids, "stops": stops}


def reversion_stats(body: list, i0: int, T: int) -> tuple | None:
    """(пересечения базы close[i0], чистый ход / путь) за T баров после бара i0 по закрытым барам."""
    if i0 + T >= len(body):
        return None
    base = body[i0][4]
    prev, cross, path = 0, 0, 0.0
    for j in range(i0 + 1, i0 + T + 1):
        d = body[j][4] - base
        sg = (d > 0) - (d < 0)
        if sg and prev and sg != prev:
            cross += 1
        if sg:
            prev = sg
        path += abs(body[j][4] - body[j - 1][4])
    return cross, (abs(body[i0 + T][4] - base) / path if path else 0.0)


def _decile_edges(vals: list) -> list:
    xs = sorted(vals)
    return [xs[min(len(xs) - 1, int(q * len(xs) / 10))] for q in range(1, 10)]


def _decile(edges: list, v: float) -> int:
    from bisect import bisect_right
    return bisect_right(edges, v)


def _hour(d: dict, i: int) -> int:
    return (d["body"][i][0] % 86400) // 3600


def short_pools(days: list) -> tuple:
    """Пулы случайных моментов (день, бар): час x дециль ATR_тек (подбор волатильности) и час (без подбора)."""
    byh: dict = {}
    for k, d in enumerate(days):
        for i in range(10, len(d["body"]) - 41):
            if d["atr"][i]:
                byh.setdefault(_hour(d, i), []).append((k, i, d["atr"][i]))
    edges = {h: _decile_edges([x[2] for x in v]) for h, v in byh.items()}
    pool_m: dict = {}
    for h, v in byh.items():
        for k, i, a in v:
            pool_m.setdefault((h, _decile(edges[h], a)), []).append((k, i))
    return edges, pool_m, {h: [(k, i) for k, i, _a in v] for h, v in byh.items()}


def draw_starts(days, edges, pool_m, pool_h, rng, evs_by_day, matched):
    """Для каждого реального события случайный момент: тот же час и (при подборе) тот же дециль ATR_тек."""
    sch: dict = {}
    for k, evs in evs_by_day.items():
        d = days[k]
        for i in evs:
            h = _hour(d, i)
            if matched and d["atr"][i] and h in edges:
                cand = pool_m.get((h, _decile(edges[h], d["atr"][i]))) or pool_h.get(h)
            else:
                cand = pool_h.get(h)
            if cand:
                ck, ci = rng.choice(cand)
                sch.setdefault(ck, {})[ci] = 1.0
    return sch


def tf_prepare(days: list, rows: list, tf: int) -> dict:
    """Ось tf: M1 агрегируются в корзины tf (flex_range.aggregate_tf), ATR_тек = средний размах 10 баров tf на
    закрытии корзины; выставляет days[k]['atr'] только на M1-баре, закрывающем корзину tf (по нему стартуют сетки
    и берутся случайные моменты). Возвращает {'agg': tf-бары, 'pos': {ts последней M1 корзины: (день, индекс body)}}."""
    from trader.lab.footprints.flex_range import aggregate_tf
    agg = aggregate_tf(rows, tf)
    where = {}
    for k, d in enumerate(days):
        d["atr"] = [None] * len(d["body"])
        for i, r in enumerate(d["body"]):
            where[r[0]] = (k, i)
    for j in range(9, len(agg)):
        w = where.get(agg[j][6])
        if w:
            days[w[0]]["atr"][w[1]] = sum(b[2] - b[3] for b in agg[j - 9:j + 1]) / 10
    return {"agg": agg, "where": where}


def tf_events(days: list, tfp: dict, imp_min: float) -> list:
    """События r1.find_impulses на непрерывном ряду tf-баров (ATR за 60 баров tf переходит через ночь), импульс и
    подтверждение в одном дне. -> по дням {'conf': {idx body: H}, 'peak': {...}} (idx = M1-бар, закрывающий корзину)."""
    from trader.lab.footprints.r1_retest import find_impulses
    agg, where = tfp["agg"], tfp["where"]
    out = [{"conf": {}, "peak": {}} for _ in days]
    for e in find_impulses(agg, {"atr_n": 60, "imp_bars": 20, "imp_min": imp_min, "imp_max": 250, "pb": 10}):
        if agg[e["i_start"]][6] // 86400 != agg[e["i_conf"]][6] // 86400:
            continue
        for key, fi in (("conf", e["i_conf"]), ("peak", e["t_p"])):
            w = where.get(agg[fi][6])
            if w and w[1] < len(days[w[0]]["body"]) - 1 and w[1] not in out[w[0]][key]:
                out[w[0]][key][w[1]] = e["strength"]
    return out


def run_short(arg: dict) -> dict:
    """mode=short_days: starts ['conf','peak'], imp_mins, ss, ns, Ts (или Ts_by_tf {tf: [...]}), tfs [1,5,15,60], draws,
    chunk [i, n] по парам (tf, start, imp_min). mode=short_diag: диагностика возвратности (события против случайных
    моментов с подбором ATR_тек по децилю в тот же час и без подбора; горизонт T x tf M1-баров)."""
    import random
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "SHORT", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows, with_sig=False)
    nt = len(days) * 2 // 3
    out = {"id": "SHORT", "mode": arg["mode"], "symbol": key, "n_days": len(days), "n_train": nt,
           "days": [d["stats"] for d in days]}
    pv = inst["pv"]
    p0 = {**DEFAULTS, **inst, "lot": 1, "fill_pen": 1}
    tfs = arg.get("tfs", [1])

    def setup(tf):
        """atr по барам body для оси tf и функция событий."""
        if tf == 1:
            for d in days:
                d["atr"] = atr10_of(d["full"], d["body"])
            return lambda im: [short_events(d["full"], d["body"], im) for d in days]
        tfp = tf_prepare(days, rows, tf)
        return lambda im: tf_events(days, tfp, im)

    if arg["mode"] == "short_diag":
        res: dict = {}
        for tf in tfs:
            events = setup(tf)
            edges, pool_m, pool_h = short_pools(days)
            for im in arg["imp_mins"]:
                evs = [e["conf"] for e in events(im)]
                evd = {k: list(e) for k, e in enumerate(evs) if e}
                for T in arg["Ts"]:
                    hz = T * tf
                    real = [reversion_stats(days[k]["body"], i, hz) for k, il in evd.items() for i in il]
                    real = [x for x in real if x]
                    r = {"n": len(real), "cross": sum(x[0] for x in real) / max(1, len(real)),
                         "eff": sum(x[1] for x in real) / max(1, len(real)), "m": [], "u": []}
                    for kind, matched in (("m", True), ("u", False)):
                        for dd in range(arg.get("draws", 20)):
                            rng = random.Random(3000 + dd)
                            sch = draw_starts(days, edges, pool_m, pool_h, rng, evd, matched)
                            st = [reversion_stats(days[k]["body"], i, hz) for k, dct in sch.items() for i in dct]
                            st = [x for x in st if x]
                            r[kind].append([sum(x[0] for x in st) / max(1, len(st)), sum(x[1] for x in st) / max(1, len(st))])
                    res[f"{tf}|{im}|{T}"] = r
        out["diag"] = res
        return out
    jobs = [(tf, a, b) for tf in tfs for a in arg["starts"] for b in arg["imp_mins"]]
    if arg.get("chunk"):
        i, n = arg["chunk"]
        jobs = jobs[i::n]
    out["configs"] = []
    cur_tf = None
    for tf, stype, im in jobs:
        if tf != cur_tf:
            events = setup(tf)
            edges, pool_m, pool_h = short_pools(days)
            cur_tf, cache = tf, {}
        if im not in cache:
            cache[im] = events(im)
        evs = [e[stype] for e in cache[im]]
        for s in arg["ss"]:
            for nn in arg["ns"]:
                for T in (arg.get("Ts_by_tf") or {}).get(str(tf), arg.get("Ts", [5, 10, 20, 40])):
                    def run_days(starts_by_day, ix):
                        rs = []
                        for k in ix:
                            d = days[k]
                            rs.append(simulate_short(d["body"], d["tail"], p0, starts_by_day.get(k, {}), d["atr"], s, nn, T, tf))
                        return rs

                    rs = run_days({k: {i: 1.0 for i in e} for k, e in enumerate(evs)}, range(len(days)))
                    fee = [_fee_rows(r["fills"], key, pv) for r in rs]
                    item = {"tf": tf, "start": stype, "imp_min": im, "s": s, "n": nn, "T": T,
                            "gross": [round(r["pnl_pts"] * pv, 2) for r in rs],
                            "fee_m": [round(sum(x[1] for x in f), 2) for f in fee],
                            "fee_t": [round(sum(x[2] for x in f), 2) for f in fee],
                            "grids": [r["grids"] for r in rs], "ev": [len(e) for e in evs], "stops": [r["stops"] for r in rs],
                            "mp": max([r["max_pos"] for r in rs] or [0])}
                    test_evs = {k: list(e) for k, e in enumerate(evs) if e and k >= nt}
                    for kind, matched in (("ca", True), ("cb", False)):
                        lst = []
                        for dd in range(arg.get("draws", 0)):
                            rng = random.Random(4000 + dd)
                            sch = draw_starts(days, edges, pool_m, pool_h, rng, test_evs, matched)
                            rc = run_days(sch, list(sch))
                            fc = [_fee_rows(r["fills"], key, pv) for r in rc]
                            g = sum(r["pnl_pts"] for r in rc) * pv
                            lst.append({"net_t": round(g - sum(x[2] for f in fc for x in f), 1),
                                        "grids": sum(r["grids"] for r in rc)})
                        item[kind] = lst
                    out["configs"].append(item)
    return out


# ── пятая редакция: «триггерная радиация» (docs/grid-regime-filter-2026.md) ─────────────────────
def simulate_trigger(body: list, tail: list, p: dict, x_pct: float | None, k_lv: int | None,
                     force_i: int | None = None, resume: str | None = None, imp: list | None = None,
                     k_mode: str = "fills", rearm_min: int | None = None) -> dict:
    """Дневная сетка (от open бара 10:00, флэт в конце дня, стоп сетки закрывает позицию). До триггера как simulate_day.
    Триггер на закрытии бара: T1 |close - база| >= x_pct% базы, T2 число исполненных уровней (с начала сетки) >= k_lv
    (оба = T3, любой из двух); force_i = индекс бара принудительного включения (контроль). После триггера входов нет,
    позиция закрывается ТОЛЬКО лимитом на средней входа (проход 1 тик, мейкер) или лучше (если на триггере цена уже
    лучше средней, закрытие по open следующего бара с полспреда, тейкер); стоп сетки и флэт остаются.
    resume: None = после закрытия день окончен; 'flat' = сетка заново сразу (база = close бара закрытия);
    'imp' = заново на первом импульсе imp[j] (на закрытии бара j > бара закрытия), база = close бара j;
    'time' = как в бою: через rearm_min минут после ЧИСТОГО выхода (безубыток) сетка заново с базой по текущей цене
    (после стопа не перевзводится). k_mode 'fills' = K считает все исполненные уровни, 'net' = боевая трактовка:
    набор в одну сторону |позиция|/лот >= K (чередование вокруг базы защиту не включает).
    Возобновлённая сетка снова работает до триггера. -> fills, pnl_pts, trig_i, kinds, eod_loss, resumes."""
    n, hs, tick = len(body), p["half"], p["tick"]
    st = _new_state()
    g = _Grid(body[0][1], p)
    base, last = body[0][1], body[0][1]
    phase, trig_i, lev0, resumes, closed_i, wait_until = "grid", None, 0, 0, None, 0
    for i in range(n):
        ts, o, h, lw, c = body[i][:5]
        if phase == "wait":
            if ((resume == "flat" and i == closed_i) or (resume == "imp" and imp and i > closed_i and imp[i] and i + 1 < n)
                    or (resume == "time" and i > closed_i and ts >= wait_until and i + 1 < n)):
                g, base, last, phase = _Grid(c, p), c, c, "grid"
                lev0, resumes = sum(1 for f in st["fills"] if f[4] == "level"), resumes + 1
            continue
        if phase == "grid":
            if g.pending:
                g.place(last)
            if not (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                    and lw > (g.buy_t[-1] if g.buy_t else -math.inf)):
                path = [o, lw, h, c] if c >= o else [o, h, lw, c]
                a = last
                for b in [o] + path[1:]:
                    _segment(g, st, a, b, ts)
                    a = b
        else:
            pos, avg = st["pos"], st["avg"]
            if pos:
                path = [o, lw, h, c] if c >= o else [o, h, lw, c]
                a = last
                for b in [o] + path[1:]:
                    hit = (max(a, b) >= avg + tick) if pos > 0 else (min(a, b) <= avg - tick)
                    if hit:
                        px = a if ((pos > 0 and a >= avg + tick) or (pos < 0 and a <= avg - tick)) else avg
                        _flat(st, px, ts, "be")
                        break
                    a = b
        last = c
        closed_at = i if not st["pos"] else None
        if st["pos"] and g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            if i + 1 < n:
                _flat(st, body[i + 1][1] - hs if st["pos"] > 0 else body[i + 1][1] + hs, body[i + 1][0], "stop")
                closed_at = i + 1
            else:
                _flat(st, c, ts, "stop")
                closed_at = i
            if phase == "grid":
                break                                               # стоп до триггера: сетка завершена
        if phase == "grid":
            if not st["pos"] and g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
                break
            nlev = abs(st["pos"]) if k_mode == "net" else sum(1 for f in st["fills"] if f[4] == "level") - lev0
            fire = (i == force_i) if force_i is not None else bool(
                (x_pct and abs(c - base) >= x_pct / 100 * base) or (k_lv and nlev >= k_lv))
            if fire and i + 1 < n:
                phase = "exit"
                trig_i = i if trig_i is None else trig_i
                pos, avg = st["pos"], st["avg"]
                if pos and ((pos > 0 and c > avg + tick) or (pos < 0 and c < avg - tick)):
                    _flat(st, body[i + 1][1] - hs if pos > 0 else body[i + 1][1] + hs, body[i + 1][0], "be")
                    closed_at = i + 1
                elif not pos:
                    closed_at = i
        if phase == "exit" and not st["pos"]:
            if resume == "time" and st["tk"] and st["tk"][-1] == "stop":
                break                                               # после стопа боевая защита не перевзводится
            if resume:
                phase, closed_i = "wait", closed_at if closed_at is not None else i
                wait_until = body[closed_i][0] + (rearm_min or 0) * 60
                if resume == "flat" and closed_i == i:
                    g, base, last, phase = _Grid(c, p), c, c, "grid"
                    lev0, resumes = sum(1 for f in st["fills"] if f[4] == "level"), resumes + 1
            else:
                break
    pre_eod = len(st["trades_pnl"])
    if st["pos"]:
        end_bar = tail[0] if tail else body[-1]
        _flat(st, end_bar[4], end_bar[0], "eod")
    kinds = {"be": 0, "stop": 0, "eod": 0}
    for kd in st["tk"]:
        if kd in kinds:
            kinds[kd] += 1
    eod_loss = sum(x for x, kd in zip(st["trades_pnl"], st["tk"]) if kd == "eod")
    return {"fills": st["fills"], "pnl_pts": sum(st["trades_pnl"]) * 1.0, "max_pos": st["max_pos"], "trig_i": trig_i,
            "kinds": kinds, "eod_loss": eod_loss, "n_contracts": sum(f[3] for f in st["fills"]), "closed": pre_eod,
            "resumes": resumes}


def run_trigger(arg: dict) -> dict:
    """mode=trigger_days: params, xs, ks, resumes ['none','flat','imp3','imp5','imp8'], draws, chunk [i, n] по режимам.
    Режимы: T1 (x), T2 (k), T3 (x, k). Дневные ряды и контроль (случайное включение в те же дни, одно на день)."""
    import random
    from trader.lab.commission import commission_for
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "TRIG", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows, with_sig=False)
    nt = len(days) * 2 // 3
    modes = [("T1", x, None) for x in arg.get("xs", [])] + [("T2", None, k) for k in arg.get("ks", [])] + \
            [("T3", x, k) for x in arg.get("xs", []) for k in arg.get("ks", [])]
    if arg.get("combos"):
        modes = [("C", x, k) for x, k in arg["combos"]]
    modes = [(m, x, k, rs, km) for m, x, k in modes for rs in arg.get("resumes", ["none"]) for km in arg.get("k_modes", ["fills"])]
    if arg.get("chunk"):
        i, n = arg["chunk"]
        modes = modes[i::n]
    out = {"id": "TRIG", "symbol": key, "n_days": len(days), "n_train": nt, "days": [d["stats"] for d in days],
           "base": {}, "configs": []}
    pv = inst["pv"]
    impflags: dict = {}

    def be_fee(fills):
        m = t = 0.0
        for ts, _s, price, qty, kind in fills:
            tk = commission_for(key, price, qty, pv, taker=True, ts=ts)
            m += commission_for(key, price, qty, pv, taker=False, ts=ts) if kind in ("level", "be") else tk
            t += tk
        return m, t

    def cols(rs, p):
        c = {"gross": [], "fee_m": [], "fee_t": [], "trig": [], "be": [], "stop": [], "eod": [], "eodl": [], "res": []}
        for r in rs:
            fm, ft = be_fee(r["fills"])
            c["gross"].append(round(r["pnl_pts"] * pv * p["lot"], 2))
            c["fee_m"].append(round(fm, 2))
            c["fee_t"].append(round(ft, 2))
            c["trig"].append(0 if r.get("trig_i") is None else 1)
            kd = r.get("kinds") or {"be": 0, "stop": 0, "eod": 0}
            c["be"].append(kd["be"])
            c["stop"].append(kd["stop"])
            c["eod"].append(kd["eod"])
            c["eodl"].append(round(r.get("eod_loss", 0.0) * pv * p["lot"], 2))
            c["res"].append(r.get("resumes", 0))
        return c

    for pi in range(len(arg["params"])):
        p = {**DEFAULTS, **inst, **arg["params"][pi], "lot": 1, "fill_pen": 1}
        rr = []
        for d in days:
            r = simulate_day(d["body"] + d["tail"], p)
            kinds = {"be": 0, "stop": sum(1 for kd in r["tk"] if kd == "stop"), "eod": sum(1 for kd in r["tk"] if kd == "eod")}
            rr.append({**r, "kinds": kinds, "eod_loss": sum(x for x, kd in zip(r["trades_pnl"], r["tk"]) if kd == "eod"),
                       "trig_i": None})
        out["base"][str(pi)] = cols(rr, p)
        for md, x, kk, rsm, km in modes:
            if rsm.startswith("imp"):
                if rsm not in impflags:
                    impflags[rsm] = [impulse_flags(d["full"], d["body"], int(rsm[3:]) * 10, 10) for d in days]
                imps, kw = impflags[rsm], {"resume": "imp"}
            elif rsm.startswith("time"):
                imps, kw = None, {"resume": "time", "rearm_min": int(rsm[4:])}
            else:
                imps, kw = None, ({"resume": "flat"} if rsm == "flat" else {})
            kw = {**kw, "k_mode": km}
            rs = [simulate_trigger(d["body"], d["tail"], p, x, kk, imp=imps[j] if imps else None, **kw)
                  for j, d in enumerate(days)]
            item = {"pi": pi, "mode": md, "x": x, "k": kk, "resume": rsm, "k_mode": km, **cols(rs, p)}
            ctrl = []
            tdays = [(j, rs[j]["trig_i"]) for j in range(nt, len(days)) if rs[j]["trig_i"] is not None]
            for dd in range(arg.get("draws", 0)):
                rng = random.Random(6000 + dd)
                rc = [simulate_trigger(days[j]["body"], days[j]["tail"], p, None, None,
                                       force_i=rng.randrange(0, max(1, len(days[j]["body"]) - 1)),
                                       imp=imps[j] if imps else None, **kw) for j, _ti in tdays]
                cl = cols(rc, p)
                ctrl.append(round(sum(cl["gross"]) - sum(cl["fee_t"]), 1))
            item["ctrl_days"] = [j for j, _ in tdays]
            item["ctrl"] = ctrl
            out["configs"].append(item)
    return out


# ── шестая редакция: «радиация» по расписанию дня ────────────────────────────────────────────────
D3_WINDOWS = ((420, 600), (600, 840), (840, 1140), (1140, 1430))
D5_WINDOWS = ((420, 540), (540, 660), (660, 840), (840, 1020), (1020, 1140), (1140, 1430))
DAY_WINDOW = (600, 1420)


def sched_windows() -> list:
    """Все окна (минуты дня МСК): разбиения D3, D5, скользящие 60 и 120 минут с шагом 30, и DAY (исходная дневная сетка)."""
    out = list(D3_WINDOWS) + list(D5_WINDOWS) + [DAY_WINDOW]
    for w in (60, 120):
        out += [(s, s + w) for s in range(420, 1430 - w + 1, 30)]
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def window_metrics(full: list, a: int, b: int, atr_day: float) -> tuple | None:
    """(направленность |close-open|/(high-low), размах в ATR дня, пересечения open окна по close, число баров) по барам окна."""
    bars = [r for r in full if a <= _minute(r[0]) < b]
    if len(bars) < 5:
        return None
    o, c = bars[0][1], bars[-1][4]
    hi, lo = max(r[2] for r in bars), min(r[3] for r in bars)
    prev = cross = 0
    for r in bars:
        d = r[4] - o
        sg = (d > 0) - (d < 0)
        if sg and prev and sg != prev:
            cross += 1
        if sg:
            prev = sg
    return (abs(c - o) / (hi - lo) if hi > lo else 0.0, (hi - lo) / atr_day if atr_day else 0.0, cross, len(bars))


def window_sim(full: list, a: int, b: int, p: dict, trig: tuple | None) -> dict | None:
    """Сетка только в окне [a, b): база = open первого бара окна (не позже a+10 минут), в конце окна позиция закрывается
    по close первого бара после окна (как флэт дня); стоп сетки закрывает позицию и завершает сетку; trig = (x%, k) триггер
    пятой редакции или None."""
    q = {**p, "start_min": a, "end_min": b}
    if trig is None:
        r = simulate_day(full, q)
        return None if r is None else {"fills": r["fills"], "pnl_pts": r["pnl_pts"]}
    body = [r for r in full if a <= _minute(r[0]) < b]
    if not body or _minute(body[0][0]) > a + p.get("late_start_min", 10):
        return None
    tail = [r for r in full if _minute(r[0]) >= b][:1]
    r = simulate_trigger(body, tail, q, trig[0], trig[1])
    return {"fills": r["fills"], "pnl_pts": r["pnl_pts"]}


def random_windows(rng, pool: list, minutes: int) -> list:
    """Случайные непересекающиеся окна из пула (a, b) с суммарной длиной >= minutes (контроль S2)."""
    chosen, total = [], 0
    cand = list(pool)
    rng.shuffle(cand)
    for a, b in cand:
        if total >= minutes:
            break
        if all(b <= x or a >= y for x, y in chosen):
            chosen.append((a, b))
            total += b - a
    return chosen


def run_sched(arg: dict) -> dict:
    """mode=sched_diag: метрики окон по дням. mode=sched_days: по каждому окну, набору сетки и варианту триггера дневные
    нетто-ряды (gross, fee_m, fee_t). chunk [i, n] по окнам."""
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "SCHED", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows, with_sig=False)
    nt = len(days) * 2 // 3
    wins = sched_windows()
    if arg.get("chunk"):
        i, n = arg["chunk"]
        wins = wins[i::n]
    out = {"id": "SCHED", "mode": arg["mode"], "symbol": key, "n_days": len(days), "n_train": nt,
           "days": [{"date": d["stats"]["date"], "dow": d["stats"]["dow"]} for d in days], "windows": {}}
    if arg["mode"] == "sched_diag":
        for a, b in wins:
            col = []
            for d in days:
                atr_day = sum(r[2] - r[3] for r in d["full"]) / len(d["full"])
                m = window_metrics(d["full"], a, b, atr_day)
                col.append(None if m is None else [round(m[0], 4), round(m[1], 3), m[2], m[3]])
            out["windows"][f"{a}-{b}"] = col
        return out
    pv = inst["pv"]
    for a, b in wins:
        res = {}
        for pi, p0 in enumerate(arg["params"]):
            p = {**DEFAULTS, **inst, **p0, "lot": 1, "fill_pen": 1}
            for tname, trig in (("none", None), ("t", (0.25, 3))):
                g_, m_, t_ = [], [], []
                for d in days:
                    r = window_sim(d["full"], a, b, p, trig)
                    if r is None:
                        g_.append(None)
                        m_.append(0)
                        t_.append(0)
                        continue
                    fee = _fee_rows(r["fills"], key, pv)
                    g_.append(round(r["pnl_pts"] * pv, 1))
                    m_.append(round(sum(x[1] for x in fee), 1))
                    t_.append(round(sum(x[2] for x in fee), 1))
                res[f"{pi}|{tname}"] = {"g": g_, "fm": m_, "ft": t_}
        out["windows"][f"{a}-{b}"] = res
    return out


# ── седьмая редакция: самовзведение на следующий день (docs/grid-regime-filter-2026.md) ─────────
def simulate_nextday(days_full: list, p: dict, x_pct: float | None, k_lv: int | None, touches: int, rearm: str,
                     delay_min: int, anchor: str, rng=None, trig_level: float | None = None) -> dict:
    """НЕПРЕРЫВНАЯ сетка по барам окна (days_full = бары дней подряд), без флэта в 23:40, позиция через ночь и
    выходные; гэп открытия проходится отрезком от прошлого close к open (пройденные уровни исполняются по цене
    уровня). Конец окна: flat по close последнего бара (вид 'end').
    Защита боевая: K = |позиция|/лот >= k_lv (набор в одну сторону, None = выкл); поклёвка: зона = за уровнем trig_level
    (base +- L*шаг; если L не задан, за x_pct% базы): касание на баре, если high (low) дошёл до границы зоны, а close
    прошлого бара был вне зоны (<= 1 касание на бар, стороны отдельно, счёт с
    момента (пере)взведения сетки); срабатывание на закрытии бара при касаниях одной стороны >= touches. После
    срабатывания входов нет, выход только лимитом на средней (как simulate_trigger), стоп сетки остаётся.
    rearm: 'none' мертва до конца окна; 'next' после выхода (безубыток) ИЛИ стопа ждать следующего дня с данными, перевзвод
    на первом баре не раньше начало дня + delay_min (anchor 'first' = первый бар дня, 'main' = 10:00), база = open этого
    бара; 'rand' то же, но случайный бар следующего дня (rng); 'time15'/'time60' через N минут после безубытка (после
    стопа не перевзводится). Старт окна по тому же правилу (начало первого дня + delay_min).
    -> по дням gross/fee_m/fee_t, закрытия be/stop/end, перевзводы, срабатывания, ночёвки с позицией, гэп-MTM."""
    tick, hs = p["tick"], p["half"]
    bars, didx = [], []
    for di, d in enumerate(days_full):
        for r in d:
            bars.append(r)
            didx.append(di)
    n, nd = len(bars), len(days_full)
    first_i, last_i = {}, {}
    for i, di in enumerate(didx):
        first_i.setdefault(di, i)
        last_i[di] = i
    day_of_ts = {r[0]: didx[i] for i, r in enumerate(bars)}

    def start_ts(di):
        t0 = days_full[di][0][0]
        return (t0 if anchor == "first" else t0 - t0 % 86400 + 36000) + delay_min * 60

    st = _new_state()
    S = {"phase": "wait", "t_ts": start_ts(0), "t_i": None}
    g, last, base, prev_close, cu, cd = None, bars[0][1], 0.0, 0.0, 0, 0
    rearms = fires = overnight = 0
    gap_pts = 0.0

    def schedule(kind, closed_ts):
        di = day_of_ts[closed_ts]
        S["t_ts"], S["t_i"] = None, None
        if rearm == "none":
            S["phase"] = "dead"
        elif rearm in ("next", "rand"):
            if di + 1 >= nd:
                S["phase"] = "dead"
            elif rearm == "next":
                S.update(phase="wait", t_ts=start_ts(di + 1))
            else:
                S.update(phase="wait", t_i=rng.randint(first_i[di + 1], last_i[di + 1]))
        elif rearm.startswith("time"):
            if kind == "stop":
                S["phase"] = "dead"
            else:
                S.update(phase="wait", t_ts=closed_ts + int(rearm[4:]) * 60)
        else:
            S["phase"] = "dead"

    for i in range(n):
        ts, o, h, lw, c = bars[i][:5]
        ph = S["phase"]
        if ph == "dead":
            last = c
            continue
        if ph == "wait":
            if (S["t_ts"] is not None and ts >= S["t_ts"]) or (S["t_i"] is not None and i >= S["t_i"]):
                g = _Grid(o, p)
                g.px = {k: round(v / tick) * tick for k, v in g.px.items()}
                g.side = {k: None for k in g.levels}
                g.pending = True
                base, last, prev_close, cu, cd = o, o, o, 0, 0
                S["phase"] = ph = "grid"
                rearms += 1
            else:
                last = c
                continue
        if i > 0 and didx[i] != didx[i - 1] and st["pos"]:
            overnight += 1
            gap_pts += st["pos"] * (o - last)
        if g.pending:
            g.place(last)
        pos, avg = st["pos"], st["avg"]
        if ph == "grid":
            if not (not g.pending and h < (g.sell_t[0] if g.sell_t else math.inf)
                    and lw > (g.buy_t[-1] if g.buy_t else -math.inf)):
                path = [o, lw, h, c] if c >= o else [o, h, lw, c]
                a = last
                for b in [o] + path[1:]:
                    _segment(g, st, a, b, ts)
                    a = b
        elif pos:
            path = [o, lw, h, c] if c >= o else [o, h, lw, c]
            a = last
            for b in [o] + path[1:]:
                hit = (max(a, b) >= avg + tick) if pos > 0 else (min(a, b) <= avg - tick)
                if hit:
                    _flat(st, a if ((pos > 0 and a >= avg + tick) or (pos < 0 and a <= avg - tick)) else avg, ts, "be")
                    break
                a = b
        last = c
        if st["pos"] and g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            if i + 1 < n:
                _flat(st, bars[i + 1][1] - hs if st["pos"] > 0 else bars[i + 1][1] + hs, bars[i + 1][0], "stop")
                schedule("stop", bars[i + 1][0])
            else:
                _flat(st, c, ts, "stop")
                S["phase"] = "dead"
            continue
        if not st["pos"] and g.so.g_stop_pts > 0 and ((g.lo and c <= g.lo) or (g.hi and c >= g.hi)):
            schedule("stop", ts)                           # цена ушла за край сетки без позиции
            continue
        if ph == "grid":
            fire = bool(k_lv and abs(st["pos"]) >= k_lv)
            if x_pct or trig_level:
                if trig_level:
                    up, dn = base + trig_level * p["step"], base - trig_level * p["step"]
                else:
                    up, dn = base * (1 + x_pct / 100), base * (1 - x_pct / 100)
                if h >= up and prev_close < up:
                    cu += 1
                if lw <= dn and prev_close > dn:
                    cd += 1
                if cu >= touches or cd >= touches:
                    fire = True
            prev_close = c
            if fire and i + 1 < n:
                fires += 1
                S["phase"] = "exit"
                pos, avg = st["pos"], st["avg"]
                if pos and ((pos > 0 and c > avg + tick) or (pos < 0 and c < avg - tick)):
                    _flat(st, bars[i + 1][1] - hs if pos > 0 else bars[i + 1][1] + hs, bars[i + 1][0], "be")
                    schedule("be", bars[i + 1][0])
                elif not pos:
                    schedule("be", ts)
        elif not st["pos"]:
            schedule("be", ts)
    if st["pos"]:
        _flat(st, bars[-1][4], bars[-1][0], "end")
    return {"st": st, "day_of_ts": day_of_ts, "nd": nd, "rearms": rearms, "fires": fires, "overnight": overnight,
            "gap_pts": gap_pts}


def run_nextday(arg: dict) -> dict:
    """mode=nextday_days: params, configs [{name, k, L, x, touches, rearm, delay, anchor, prot}], draws, chunk [i, n] по
    наборам params. Для каждого контракта два прогона: с первого дня обучения (первые 2/3 дней) и заново с первого дня
    отложенной трети. rearm 'rand' считается только на тесте (arg draws розыгрышей)."""
    import random
    from trader.lab.commission import commission_for
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "NEXT7", "symbol": key, "error": "нет баров в окне"}
    inst = INST["Si" if key[:2].lower() == "si" else "RI"]
    days = prep_days(rows, with_sig=False)
    nt = len(days) * 2 // 3
    wins = {"train": [d["full"] for d in days[:nt]], "test": [d["full"] for d in days[nt:]]}
    pv = inst["pv"]
    pis = arg.get("pis") or list(range(len(arg["params"])))
    if arg.get("chunk"):
        i, n = arg["chunk"]
        pis = pis[i::n]
    out = {"id": "NEXT7", "symbol": key, "n_days": len(days), "n_train": nt,
           "dates": {"train": [d["stats"]["date"] for d in days[:nt]], "test": [d["stats"]["date"] for d in days[nt:]]},
           "configs": []}

    def summarize(res):
        st, dmap, nd = res["st"], res["day_of_ts"], res["nd"]
        g_, fm, ft = [0.0] * nd, [0.0] * nd, [0.0] * nd
        for x, ts in zip(st["trades_pnl"], st["tts"]):
            g_[dmap[ts]] += x * pv
        for ts, _s, price, qty, kind in st["fills"]:
            tk = commission_for(key, price, qty, pv, taker=True, ts=ts)
            mk = commission_for(key, price, qty, pv, taker=False, ts=ts) if kind in ("level", "be") else tk
            d = dmap[ts]
            fm[d] += mk
            ft[d] += tk
        kinds = {"be": 0, "stop": 0, "end": 0}
        for kd in st["tk"]:
            if kd in kinds:
                kinds[kd] += 1
        return {"gross": [round(x, 1) for x in g_], "fee_m": [round(x, 1) for x in fm], "fee_t": [round(x, 1) for x in ft],
                "be": kinds["be"], "stop": kinds["stop"], "end": kinds["end"], "rearms": res["rearms"],
                "fires": res["fires"], "overnight": res["overnight"], "gap": round(res["gap_pts"] * pv, 1)}

    for pi in pis:
        p = {**DEFAULTS, **inst, **arg["params"][pi], "lot": 1, "fill_pen": 1}
        for cf in arg["configs"]:
            item = {"pi": pi, **{k: cf.get(k) for k in ("name", "k", "L", "x", "touches", "rearm", "delay", "anchor", "prot")}}
            kw = {"x_pct": cf.get("x"), "k_lv": cf.get("k"), "touches": cf.get("touches") or 1, "trig_level": cf.get("L")}
            for part in ("train", "test"):
                if cf["rearm"] == "rand":
                    if part == "train":
                        continue
                    draws = []
                    for dd in range(arg.get("draws", 20)):
                        s = summarize(simulate_nextday(wins[part], p, kw["x_pct"], kw["k_lv"], kw["touches"], "rand",
                                                       cf["delay"], cf["anchor"], random.Random(8000 + dd), kw["trig_level"]))
                        draws.append({"net_t": round(sum(s["gross"]) - sum(s["fee_t"]), 1),
                                      "net_m": round(sum(s["gross"]) - sum(s["fee_m"]), 1), "rearms": s["rearms"]})
                    item["rand"] = draws
                    continue
                item[part] = summarize(simulate_nextday(wins[part], p, kw["x_pct"], kw["k_lv"], kw["touches"], cf["rearm"],
                                                        cf["delay"], cf["anchor"], None, kw["trig_level"]))
            out["configs"].append(item)
    return out

# ── сетка документа и живой набор ────────────────────────────────────────────
STEPS = (50, 100, 150, 200, 300, 400, 600)
LEVELS = (2, 3, 5, 8, 12)
STOPS = (0, 100, 300, 600, 1000)
ASYM = ((5, 2), (2, 5), (8, 3), (3, 8))
ASYM_STEPS = (100, 200, 300)
# Живой набор оператора на 01.10.2026 (RIZ6, data/smart_orders.json, kind=grid, только чтение)
LIVE = ({"step": 100, "buys": 10, "sells": 10, "stop_pts": 300},
        {"step": 100, "buys": 10, "sells": 15, "stop_pts": 300},
        {"step": 100, "buys": 10, "sells": 15, "stop_pts": 250})


def build_variants(preset: str = "grid") -> list[dict]:
    live = [dict(v) for v in LIVE]
    if preset == "live":
        base = live
    else:
        base = live + [{"step": s, "buys": n, "sells": n, "stop_pts": t}
                       for s in STEPS for n in LEVELS for t in STOPS]
        base += [{"step": s, "buys": b, "sells": a, "stop_pts": t}
                 for s in ASYM_STEPS for b, a in ASYM for t in STOPS]
    out = []
    for v in base:                      # справочные режимы только там, где стоп есть
        out.append(v)
        if v["stop_pts"] > 0:
            out.append({**v, "stop_mode": 1})
            out.append({**v, "restart": "after_stop"})
    seen, uniq = set(), []
    for v in out:
        vid = variant_id({**DEFAULTS, **v})
        if vid not in seen:
            seen.add(vid)
            uniq.append(v)
    return uniq


def run(arg: dict) -> dict:
    """Задача агента (kind=task): arg = {"symbol_key", "since", "until", "preset": "grid"|"live",
    "variants": [...] (вместо preset), "chunk": [i, n] (берутся варианты i::n)}."""
    if str(arg.get("mode", "")).startswith("regime"):
        return run_regime(arg)
    if str(arg.get("mode", "")).startswith("delay"):
        return run_delay(arg)
    if str(arg.get("mode", "")).startswith("wide"):
        return run_wide(arg)
    if str(arg.get("mode", "")).startswith("reset"):
        return run_reset(arg)
    if str(arg.get("mode", "")).startswith("short"):
        return run_short(arg)
    if str(arg.get("mode", "")).startswith("trigger"):
        return run_trigger(arg)
    if str(arg.get("mode", "")).startswith("sched"):
        return run_sched(arg)
    if str(arg.get("mode", "")).startswith("nextday"):
        return run_nextday(arg)
    from trader.lab.footprints import common
    key = arg["symbol_key"]
    rows = common.load_bars(key, arg.get("since"), arg.get("until"))
    if not rows:
        return {"id": "GRID", "symbol": key, "error": "нет баров в окне"}
    variants = arg.get("variants") or build_variants(arg.get("preset", "grid"))
    if arg.get("chunk"):
        i, n = arg["chunk"]
        variants = variants[i::n]
    res = analyze({key: rows}, variants)[key]
    return {"id": "GRID", "symbol": key, "chunk": arg.get("chunk"), "n_bars": len(rows), **res}
