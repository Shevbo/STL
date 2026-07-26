"""Smart orders for the OPERATOR's manual QUIK trading (STL-side watcher, v1).

Kinds (the classic community set — QUIK «стоп-заявки» / broker bracket orders):
  sl        stop-loss:    side=sell fires when price <= trigger (protect a long),
                          side=buy  fires when price >= trigger (protect a short).
  tp        take-profit:  side=sell fires when price >= trigger,
                          side=buy  fires when price <= trigger.
  trail_tp  trailing stop: optional activation level (trigger_price, 0 = active at
                          once), then tracks the best price and fires when it
                          retraces trail_offset POINTS from the peak (sell) /
                          trough (buy).
  on_fill   conditional (order-sends-order): fires the child order the moment the
                          watched client_id reports FILLED — lets the operator
                          chain "entry -> attach SL+TP bracket".
  (OCO)     any order carrying the same non-empty oco_group cancels its siblings
                          when one of the group fires — SL+TP as a bracket.

Not implemented (surveyed, deliberately later): iceberg, scale/ladder,
time-activated. See docs/runbooks/smart-orders.md.

ARCHITECTURE / honesty: this v1 watches ticks INSIDE the STL process (1s loop)
and fires plain limit orders through the existing human place path — so smart
orders pause while STL is down (robots on the agent are unaffected; the operator
is the user here and sees the stand). v2 = native QUIK NEW_STOP_ORDER through
the Lua bridge (exchange-side triggering, survives everything) — needs a proto
extension + an operator Lua restart, so it could not ship overnight.

Fired child prices are made MARKETABLE (cross by a cushion) and are ALWAYS
quantized to the instrument price step — an off-grid price is rejected by QUIK
("не кратно шагу") and silently fills nothing (bit us live 2026-07-21).

Pure logic lives in evaluate(); the API/watcher wiring is in
trader/api/quik_smart_orders.py. No I/O in this module.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass

KINDS = ("sl", "tp", "trail_tp", "on_fill")

# Cross cushion for a fired (marketable) child: enough to actually cross after
# exchange lag, but safely under the 0.2% hard price collar.
_CUSHION_FRAC = 0.0005     # 0.05% of price
_CUSHION_MIN_STEPS = 3     # never less than 3 price steps
_CUSHION_MAX_FRAC = 0.0015 # hard cap, must stay under the 0.2% collar
_STALE_TICK_MS = 30_000    # never fire off a quote older than this


@dataclass
class SmartOrder:
    so_id: str
    kind: str                 # one of KINDS
    code: str                 # instrument, e.g. RIU6
    side: str                 # side of the CHILD order to fire: "buy" | "sell"
    qty: int
    trigger_price: float = 0.0   # sl/tp: level; trail_tp: activation level (0 = at once)
    trail_offset: float = 0.0    # trail_tp: retrace in PRICE POINTS
    watch_client_id: str = ""    # on_fill: client_id whose FILL fires the child
    child_price: float = 0.0     # on_fill: explicit child limit (0 = marketable)
    oco_group: str = ""          # same non-empty group = one-cancels-others
    good_till_ms: int = 0        # 0 = no expiry
    status: str = "armed"        # armed | fired | cancelled | expired | error
    note: str = ""
    peak: float = 0.0            # trail bookkeeping (best price since activation)
    activated: bool = False      # trail: activation level crossed
    created_ms: int = 0
    fired_ms: int = 0
    fired_client_id: str = ""

    def validate(self) -> str | None:
        """Returns a human error or None. Kept dumb and explicit."""
        if self.kind not in KINDS:
            return f"kind должен быть одним из {KINDS}"
        if self.side not in ("buy", "sell"):
            return "side должен быть buy|sell"
        if self.qty <= 0:
            return "qty должен быть > 0"
        if not self.code:
            return "code обязателен"
        if self.kind in ("sl", "tp") and self.trigger_price <= 0:
            return "trigger_price обязателен для sl/tp"
        if self.kind == "trail_tp" and self.trail_offset <= 0:
            return "trail_offset (пункты) обязателен для trail_tp"
        if self.kind == "on_fill" and not self.watch_client_id:
            return "watch_client_id обязателен для on_fill"
        return None


@dataclass
class Fire:
    """Action: place the child order of `so` at `price` (grid-quantized)."""
    so: SmartOrder
    price: float


@dataclass
class Cancel:
    """Action: mark `so` cancelled (OCO sibling of a fired order)."""
    so: SmartOrder
    reason: str


def quantize(price: float, step: float, side: str) -> float:
    """Snap price onto the exchange grid: DOWN for sell, UP for buy — the
    conservative direction keeps a marketable order marketable."""
    if step <= 0:
        return price
    n = price / step
    snapped = math.floor(n) * step if side == "sell" else math.ceil(n) * step
    # float dust: 83533.12 -> 83530.0000001 style artifacts
    return round(snapped, 10)


def marketable_price(side: str, bid: float, ask: float, last: float, step: float) -> float:
    """Cross the current touch by a cushion, on-grid. sell -> below bid,
    buy -> above ask (fallback last when the book side is empty)."""
    base = (bid if side == "sell" else ask) or last
    if base <= 0:
        return 0.0
    cushion = min(max(_CUSHION_FRAC * base, _CUSHION_MIN_STEPS * step),
                  _CUSHION_MAX_FRAC * base)
    raw = base - cushion if side == "sell" else base + cushion
    return quantize(raw, step, side)


def _trigger_hit(so: SmartOrder, price: float) -> bool:
    if so.kind == "sl":
        return price <= so.trigger_price if so.side == "sell" else price >= so.trigger_price
    if so.kind == "tp":
        return price >= so.trigger_price if so.side == "sell" else price <= so.trigger_price
    return False


def _trail_step(so: SmartOrder, price: float) -> bool:
    """Advance trailing state; returns True when the retrace fired."""
    if not so.activated:
        if so.trigger_price <= 0:
            so.activated = True
            so.peak = price
        elif (so.side == "sell" and price >= so.trigger_price) or \
             (so.side == "buy" and price <= so.trigger_price):
            so.activated = True
            so.peak = price
        else:
            return False
    if so.side == "sell":
        so.peak = max(so.peak, price)
        return price <= so.peak - so.trail_offset
    so.peak = min(so.peak, price)
    return price >= so.peak + so.trail_offset


def catch_up_trail(so: SmartOrder, wmin: float, wmax: float) -> bool:
    """Догон пропущенной активации trail_tp по экстремумам окна С МОМЕНТА СОЗДАНИЯ
    заявки (пробой уровня случился, пока STL/watcher лежал). buy: минимум окна
    пробил trigger вниз -> активируем от этого минимума; sell: зеркально по
    максимуму. Только активация — дальше ведёт обычный evaluate() по живым тикам.
    Возвращает True, если состояние изменилось."""
    if (so.kind != "trail_tp" or so.activated or so.status != "armed"
            or so.trigger_price <= 0 or wmin <= 0 or wmax <= 0):
        return False
    if so.side == "buy" and wmin <= so.trigger_price:
        so.activated = True
        so.peak = wmin
        so.note = ((so.note + " ") if so.note else "") + \
            f"авто-активация: минимум {wmin:g} пробил {so.trigger_price:g} (ISS, задержка ~15 мин)"
        return True
    if so.side == "sell" and wmax >= so.trigger_price:
        so.activated = True
        so.peak = wmax
        so.note = ((so.note + " ") if so.note else "") + \
            f"авто-активация: максимум {wmax:g} пробил {so.trigger_price:g} (ISS, задержка ~15 мин)"
        return True
    return False


def evaluate(orders: list[SmartOrder], code: str, *, last: float, bid: float,
             ask: float, tick_ms: int, now_ms: int,
             filled_client_ids: set[str], step: float) -> list[Fire | Cancel]:
    """One watcher pass for one instrument. Mutates trailing bookkeeping on the
    orders; returns the actions to execute. Deterministic, no I/O.

    Price basis: LAST trade (QUIK stop-orders trigger on last), falling back to
    the book mid when the tape is silent. A stale quote never fires anything.
    """
    actions: list[Fire | Cancel] = []
    price = last if last > 0 else ((bid + ask) / 2 if bid > 0 and ask > 0 else 0.0)
    fresh = tick_ms > 0 and (now_ms - tick_ms) <= _STALE_TICK_MS

    armed = [o for o in orders if o.status == "armed" and o.code == code]
    fired_groups: set[str] = set()

    for so in armed:
        if so.good_till_ms and now_ms > so.good_till_ms:
            so.status = "expired"
            continue

        if so.kind == "on_fill":
            if so.watch_client_id in filled_client_ids:
                px = so.child_price or marketable_price(so.side, bid, ask, price, step)
                if px > 0:
                    actions.append(Fire(so, quantize(px, step, so.side)))
                    if so.oco_group:
                        fired_groups.add(so.oco_group)
            continue

        if not fresh or price <= 0:
            continue  # never act on a dead/stale feed

        hit = _trail_step(so, price) if so.kind == "trail_tp" else _trigger_hit(so, price)
        if hit:
            px = marketable_price(so.side, bid, ask, price, step)
            if px > 0:
                actions.append(Fire(so, px))
                if so.oco_group:
                    fired_groups.add(so.oco_group)

    if fired_groups:
        fired_ids = {a.so.so_id for a in actions if isinstance(a, Fire)}
        for so in armed:
            if (so.status == "armed" and so.oco_group in fired_groups
                    and so.so_id not in fired_ids):
                actions.append(Cancel(so, "OCO: сработала парная заявка"))
    return actions


# ---- book with JSON persistence (survives an STL restart) ----

class SmartOrderBook:
    """All smart orders + save/load. The watcher task is the single mutator at
    runtime; API handlers run on the same event loop, so plain attrs are safe."""

    def __init__(self, path: str) -> None:
        self._path = path
        self.orders: list[SmartOrder] = []

    def load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            self.orders = [SmartOrder(**o) for o in raw]
        except Exception:
            # A corrupt file must not kill STL startup; start empty, keep the
            # broken file for post-mortem.
            try:
                os.replace(self._path, self._path + ".corrupt")
            except OSError:
                pass
            self.orders = []

    def save(self) -> None:
        d = os.path.dirname(self._path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(o) for o in self.orders], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._path)

    def add(self, so: SmartOrder) -> None:
        self.orders.append(so)
        self.save()

    def get(self, so_id: str) -> SmartOrder | None:
        return next((o for o in self.orders if o.so_id == so_id), None)

    def active(self) -> list[SmartOrder]:
        return [o for o in self.orders if o.status == "armed"]

    def codes(self) -> set[str]:
        return {o.code for o in self.active()}


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def now_ms() -> int:
    return int(time.time() * 1000)
