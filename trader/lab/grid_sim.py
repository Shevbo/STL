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
            "stops": []}


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
    return {"fills": st["fills"], "trades_pnl": st["trades_pnl"],
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
