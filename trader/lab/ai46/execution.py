"""Paper execution + risk for team-46.

PaperExecutor satisfies the contrarian session's executor protocol and records
paper fills (no real orders). RiskManager provides regime roles (contrarian.go
regime routing), exposure limits, and a CUSUM P&L-drift halt (risk/cusum.go).
Real (Finam) execution is a drop-in replacement of PaperExecutor in Phase 7b+.
"""
from __future__ import annotations

from dataclasses import dataclass

from trader.lab.ai46 import models as MOD


@dataclass
class PaperFill:
    time: float
    ticker: str
    side: str        # "buy" | "sell"
    size_pct: float
    price: float
    kind: str        # "open" | "close_soft" | "close_hard"
    pnl: float = 0.0  # fractional return contribution (close fills only)


@dataclass
class _Pos:
    side: str        # "long" | "short"
    size_pct: float
    entry: float
    stop_pct: float
    take_pct: float


class PaperExecutor:
    def __init__(self) -> None:
        self.prices: dict[str, float] = {}
        self.positions: dict[str, _Pos] = {}
        self.fills: list[PaperFill] = []
        self.realized_pnl: float = 0.0
        self._now: float = 0.0

    def set_price(self, ticker: str, price: float) -> None:
        self.prices[ticker] = price

    def set_time(self, now: float) -> None:
        self._now = now

    def price_of(self, ticker: str) -> float:
        return self.prices.get(ticker, 0.0)

    def open_count(self) -> int:
        return len(self.positions)

    def open_exposure(self) -> float:
        return sum(p.size_pct for p in self.positions.values())

    def _record(self, ticker, side, size_pct, price, kind, pnl=0.0) -> None:
        self.fills.append(PaperFill(self._now, ticker, side, size_pct, price, kind, pnl))

    def enter_long(self, ticker, strategy, size_pct, stop_pct, take_pct) -> None:
        self._enter(ticker, "long", size_pct, stop_pct, take_pct)

    def enter_short(self, ticker, strategy, size_pct, stop_pct, take_pct) -> None:
        self._enter(ticker, "short", size_pct, stop_pct, take_pct)

    def _enter(self, ticker, side, size_pct, stop_pct, take_pct) -> None:
        price = self.price_of(ticker)
        self.positions[ticker] = _Pos(side, size_pct, price, stop_pct, take_pct)
        self._record(ticker, "buy" if side == "long" else "sell", size_pct, price, "open")

    def close_soft(self, ticker, strategy) -> None:
        self._close(ticker, "close_soft")

    def close_hard(self, ticker, strategy) -> None:
        self._close(ticker, "close_hard")

    def _close(self, ticker, kind) -> None:
        pos = self.positions.pop(ticker, None)
        if pos is None:
            return
        price = self.price_of(ticker)
        dirmul = 1.0 if pos.side == "long" else -1.0
        ret = ((price - pos.entry) / pos.entry * dirmul) if pos.entry else 0.0
        pnl = ret * pos.size_pct
        self.realized_pnl += pnl
        self._record(ticker, "sell" if pos.side == "long" else "buy", pos.size_pct, price, kind, pnl)


class RiskManager:
    """Regime routing + exposure caps + CUSUM halt. References the executor for
    live open state."""

    def __init__(self, executor: PaperExecutor, *, max_positions: int = 5,
                 max_exposure: float = 0.30, sigma_pnl: float = 0.01) -> None:
        self.exec = executor
        self.max_positions = max_positions
        self.max_exposure = max_exposure
        self.regime = "flat"
        self.cusum = MOD.CUSUMDetector(sigma_pnl)
        self.halted = False

    def set_regime(self, state: str) -> None:
        self.regime = state

    def regime_role(self, strategy: str) -> tuple[bool, float]:
        """contrarian.go regime routing: native to trend_down (full), panic +
        trend_up demote to half; flat full."""
        if strategy == "contrarian":
            if self.regime == "trend_down":
                return (True, 1.0)
            if self.regime in ("panic", "trend_up"):
                return (True, 0.5)
            return (True, 1.0)
        return (True, 1.0)

    def approve_for_event(self, ticker, strategy, side, size_pct, event_id) -> bool:
        if self.halted:
            return False
        if ticker in self.exec.positions:
            return False
        if self.exec.open_count() >= self.max_positions:
            return False
        if self.exec.open_exposure() + size_pct > self.max_exposure:
            return False
        return True

    def ref_price(self, ticker, strategy) -> float:
        return self.exec.price_of(ticker)

    def record_pnl(self, realized: float, expected: float = 0.0) -> bool:
        """Feed a realized P&L deviation to CUSUM; sets halted on drift. Returns halted."""
        if self.cusum.update(realized, expected):
            self.halted = True
        return self.halted
