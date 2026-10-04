"""Честный объём и «купил и держи» для витрины (docs/backtest-workbench-spec.md, ЧЕСТНЫЙ ОБЪЁМ).

Без ГО нигде. Нет данных = None, не 0. Чистые функции покрыты tests/lab/test_showcase_volume.py;
BarsCtx читает бары поконтрактно из agent_bars (файлы на хостере, не БД) и pv из движка
(grid_sim.SWEEP_CONTRACTS) либо из instrument_meta (словарь symbol -> pv, подаёт сборщик).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

_CONTRACT = re.compile(r"^([A-Za-z0-9]+?)[FGHJKMNQUVXZ]\d$")


def instrument_of(sym: str | None) -> str:
    m = _CONTRACT.match(sym or "")
    return m.group(1) if m else (sym or "")


# ---------- чистые функции ----------

def peak_from_trades(trades: list | None):
    """-> (пик |позиции|, цена сделки, поднявшей позицию до пика, время) или None. Покупка +, продажа -."""
    if not trades:
        return None
    pos, best = 0.0, None
    for t in sorted((t for t in trades if t.get("time") is not None and t.get("qty") and t.get("price") is not None),
                    key=lambda t: t["time"]):
        pos += t["qty"] if t.get("side") == "buy" else -t["qty"]
        if best is None or abs(pos) > best[0]:
            best = (abs(pos), float(t["price"]), int(t["time"]))
    return best if best and best[0] > 0 else None


def gross_points(trades: list | None) -> float | None:
    """Реализованные пункты по сделкам (учёт по средней цене, развороты через ноль)."""
    if not trades:
        return None
    pos = avg = real = 0.0
    for t in sorted((t for t in trades if t.get("time") is not None), key=lambda t: t["time"]):
        q = t["qty"] if t.get("side") == "buy" else -t["qty"]
        p = float(t["price"])
        if pos == 0 or (pos > 0) == (q > 0):
            avg = (avg * abs(pos) + p * abs(q)) / (abs(pos) + abs(q))
            pos += q
            continue
        closed = min(abs(q), abs(pos))
        real += (p - avg) * closed * (1 if pos > 0 else -1)
        pos += q
        if pos != 0 and (pos > 0) != (pos - q > 0):  # развернулись: остаток открыт по p
            avg = p
        elif pos == 0:
            avg = 0.0
    return real


def infer_unit(net: float | None, gross_pts: float | None, pv: float | None) -> str | None:
    """В чём считан net прогона: 'rub' (пункты x pv) или 'points'. None - не удалось честно определить."""
    if net is None or gross_pts is None or pv is None:
        return None
    if abs(pv - 1.0) < 1e-9:
        return "rub"  # пункт = рубль
    return "rub" if abs(net - gross_pts * pv) <= abs(net - gross_pts) else "points"


def full_cost_rub(peak, price, pv) -> float | None:
    if peak is None or price is None or pv is None:
        return None
    return round(peak * price * pv, 2)


def return_pct(net_rub, cost) -> float | None:
    if net_rub is None or not cost:
        return None
    return round(net_rub / cost * 100, 2)


def month_share(curve: list | None) -> float | None:
    """Доля календарных месяцев (UTC) окна кривой с плюсовым приростом; первый месяц от начала кривой."""
    if not curve or len(curve) < 2:
        return None
    last: dict = {}
    for ts, y in curve:
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        last[(d.year, d.month)] = y
    prev, pos = curve[0][1], 0
    for k in sorted(last):
        pos += last[k] - prev > 0
        prev = last[k]
    return round(pos / len(last), 4)


def score_of(rf, net, l_share) -> float | None:
    if rf is None or net is None or l_share is None:
        return None
    return rf * net * l_share


def buyhold_curve(bars: list, rolls: dict, pvs: list, t0: int, t1: int, n: float, to_rub: bool,
                  n_points: int, downsample) -> list | None:
    """pnl «купил на открытии первого бара окна и держи» на n контрактов: [[ts, pnl]].
    bars [ts,o,h,l,c,v,ci]; rolls {индекс бара: разница нового контракта над старым} как в grid_sim.prep_wide
    (на стыке разница вычитается из прироста); pvs[ci] - рублей за пункт (to_rub=False: пункты)."""
    idx = [j for j, b in enumerate(bars) if t0 <= b[0] <= t1]
    if not idx or not n:
        return None
    prev, acc = bars[idx[0]][1], 0.0
    out = []
    for k, j in enumerate(idx):
        b = bars[j]
        pv = pvs[b[6] if len(b) > 6 else 0] if to_rub else 1.0
        if pv is None:
            return None
        acc += (b[4] - prev - (rolls.get(j, 0.0) if k else 0.0)) * n * pv
        prev = b[4]
        out.append([int(b[0]), round(acc, 2)])
    return downsample(out, n_points)


# ---------- контекст на хостере ----------

class BarsCtx:
    def __init__(self, bars_dir: str, meta_pv: dict | None = None):
        self.dir = bars_dir
        self.meta = meta_pv or {}
        self._files: dict = {}
        self._splice: dict = {}

    def _load(self, key: str) -> list:
        if key not in self._files:
            p = os.path.join(self.dir, key + ".json")
            self._files[key] = json.load(open(p, encoding="utf-8")).get("rows", []) if os.path.exists(p) else []
        return self._files[key]

    @staticmethod
    def _sweep(inst: str):
        from trader.lab import grid_sim
        return grid_sim.SWEEP_CONTRACTS.get(inst)

    def _spliced(self, inst: str):
        if inst not in self._splice:
            from trader.lab import grid_sim
            cs = self._sweep(inst)
            self._splice[inst] = (grid_sim.prep_wide(cs, "2026-12-31", self._load), [c["spec"]["pv"] for c in cs])
        return self._splice[inst]

    def pv_at(self, sym: str | None, ts: int | None) -> float | None:
        if not sym or sym.startswith("sp"):  # спреды: цена синтетическая, полной стоимости нет
            return None
        inst = instrument_of(sym)
        cs = self._sweep(inst)
        if cs and sym == inst and ts is not None:  # склейка: контракт на момент ts
            pw, pvs = self._spliced(inst)
            cand = [b for b in pw["bars"] if b[0] <= ts]
            return pvs[cand[-1][6]] if cand else None
        if cs:
            for c in cs:
                if c["key"] == sym:
                    return c["spec"]["pv"]
        if self.meta.get(sym):
            return self.meta[sym]
        fam = [v for k, v in self.meta.items() if instrument_of(k) == inst and v]
        return fam[0] if fam else None

    def buyhold(self, sym: str | None, t0: int, t1: int, n: float, to_rub: bool, n_points: int, downsample):
        if not sym or sym.startswith("sp") or not n:
            return None
        inst = instrument_of(sym)
        if self._sweep(inst) and sym == inst:
            pw, pvs = self._spliced(inst)
            return buyhold_curve(pw["bars"], pw["rolls"], pvs, t0, t1, n, to_rub, n_points, downsample)
        if sym == inst:  # склейка без расписания контрактов (нет в SWEEP_CONTRACTS): честно склеить нечем
            return None
        rows = self._load(sym)
        pv = self.pv_at(sym, t1)
        bars = [list(r[:6]) + [0] for r in rows]
        return buyhold_curve(bars, {}, [pv], t0, t1, n, to_rub, n_points, downsample)
