"""Smart Contrarian session — port of go-bot/internal/event/contrarian.go.

Faithful to the Go state machine, sizing constants and direction logic, but
driven by step(now, ...) instead of goroutines+channels (the platform tick model).
Execution / risk / LLM-gate are injected so the session is unit-testable without
Finam: see the Executor/Risk/Gate protocols below. Defaults are permissive.

State: MONITORING -> PRIMARY_ACTIVE -> WAITING_REVERSAL -> REVERSAL_ACTIVE -> DONE/ABORT.
"""
from __future__ import annotations

from dataclasses import dataclass

# Sizing/entry params (contrarian.go, pipeline v2.5 §7) — exact.
SIZE_BASE = 0.03
MIN_AGREEMENT = 0.5
REVERSAL_SIGS = 3
MONITORING_DUR = 5 * 60
PRIMARY_HOLD = 15 * 60
WAIT_REVERSAL = 10 * 60
REVERSAL_HOLD = 30 * 60

# risk.LongOFIBoost — OFI bias band above which longs are favoured (contrarian.go
# flips to long only when OFI > this AND long agreement dominates).
LONG_OFI_BOOST = 0.15

MONITORING = "MONITORING"
PRIMARY_ACTIVE = "PRIMARY_ACTIVE"
WAITING_REVERSAL = "WAITING_REVERSAL"
REVERSAL_ACTIVE = "REVERSAL_ACTIVE"
DONE = "DONE"
ABORT = "ABORT"


def decide_primary_direction(sig_ofi: float, long_agr: float, short_agr: float,
                             long_ofi_boost: float = LONG_OFI_BOOST) -> str:
    """contrarian.go: default 'short' (fade the relief rally); flip to 'long'
    only when OFI > boost AND long agreement materially dominates (×1.3)."""
    if sig_ofi > long_ofi_boost and long_agr > short_agr * 1.3:
        return "long"
    return "short"


def signal_aligned_with(sig_ofi: float, price_change: float, direction: str) -> bool:
    """contrarian.go::signalAlignedWith."""
    if direction == "long":
        return sig_ofi > 0 or price_change > 0
    return sig_ofi < 0 or price_change < 0


def is_panic(hmm_state: str, volume_ratio: float) -> bool:
    """contrarian.go::isPanic."""
    return hmm_state == "panic" or volume_ratio > 10


# ── injected dependencies (defaults are permissive no-ops) ───────────────────

class _DefaultExecutor:
    def __init__(self): self.calls: list[tuple] = []
    def enter_long(self, ticker, strategy, size_pct, stop_pct, take_pct): self.calls.append(("long", ticker, size_pct, stop_pct, take_pct))
    def enter_short(self, ticker, strategy, size_pct, stop_pct, take_pct): self.calls.append(("short", ticker, size_pct, stop_pct, take_pct))
    def close_soft(self, ticker, strategy): self.calls.append(("close_soft", ticker))
    def close_hard(self, ticker, strategy): self.calls.append(("close_hard", ticker))


class _DefaultRisk:
    def regime_role(self, strategy): return (True, 1.0)         # (allowed, size_mult)
    def approve_for_event(self, ticker, strategy, side, size_pct, event_id): return True


def _default_gate(proposal: dict) -> tuple[bool, float]:
    """(proceed, final_size_pct). Real wiring injects the LLM PM gate."""
    return True, float(proposal.get("proposed_size_pct", 0.0))


@dataclass
class _Leg:
    open: bool = False
    side: str = ""   # "long" | "short"


class ContrarianSession:
    def __init__(self, ticker: str, *, executor=None, risk=None, gate=None,
                 primary_size_base: float = SIZE_BASE) -> None:
        self.ticker = ticker
        self.exec = executor or _DefaultExecutor()
        self.risk = risk or _DefaultRisk()
        self.gate = gate or _default_gate
        self.size_base = primary_size_base
        self.state = MONITORING
        self.primary = "short"
        self.event_id = ""
        self._role_mult = 1.0
        self._phase_start = 0.0
        self._confirmations = 0
        self._primary = _Leg()
        self._reversal = _Leg()
        self._aborted = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, now: float, sig_ofi: float, long_agr: float, short_agr: float,
              event_id: str = "") -> None:
        self.event_id = event_id
        self.primary = decide_primary_direction(sig_ofi, long_agr, short_agr)
        allowed, mult = self.risk.regime_role("contrarian")
        if not allowed:
            self.state = DONE
            return
        self._role_mult = mult
        self.state = MONITORING
        self._phase_start = now

    def deliver_verdict(self, verdict: str) -> None:
        if verdict == "abort":
            self._aborted = True

    def deliver_signal(self, sig_ofi: float, price_change: float) -> None:
        """In WAITING_REVERSAL, count confirming reverse-direction signals."""
        if self.state != WAITING_REVERSAL:
            return
        rev = "long" if self.primary == "short" else "short"
        if signal_aligned_with(sig_ofi, price_change, rev):
            self._confirmations += 1

    def step(self, now: float, feat) -> str:
        """Advance the state machine. `feat` exposes agreement_ratio(dir),
        hmm_state, volume_ratio. Returns the current state."""
        if self.state in (DONE, ABORT):
            return self.state

        if self.state == MONITORING:
            if now - self._phase_start < MONITORING_DUR:
                return self.state
            if is_panic(feat.hmm_state, feat.volume_ratio):
                self.state = DONE
                return self.state
            agr = feat.agreement_ratio(self.primary)
            if agr < MIN_AGREEMENT:
                self.state = DONE
                return self.state
            size = self.size_base * agr * self._role_mult
            if self._open_leg(self._primary, self.primary, size, 0.015, 0.025, "contrarian primary entry", agr):
                self.state = PRIMARY_ACTIVE
                self._phase_start = now
            else:
                self.state = DONE
            return self.state

        if self.state == PRIMARY_ACTIVE:
            if self._aborted:
                self._close_leg(self._primary, soft=False)
                self.state = ABORT
                return self.state
            if now - self._phase_start >= PRIMARY_HOLD:
                self._close_leg(self._primary, soft=True)
                self.state = WAITING_REVERSAL
                self._phase_start = now
                self._confirmations = 0
            return self.state

        if self.state == WAITING_REVERSAL:
            if self._confirmations >= REVERSAL_SIGS:
                rev = "long" if self.primary == "short" else "short"
                agr = feat.agreement_ratio(rev)
                if agr < MIN_AGREEMENT:
                    self.state = DONE
                    return self.state
                size = SIZE_BASE * agr * self._role_mult
                if self._open_leg(self._reversal, rev, size, 0.015, 0.02, "contrarian reversal entry", agr):
                    self.state = REVERSAL_ACTIVE
                    self._phase_start = now
                else:
                    self.state = DONE
            elif now - self._phase_start >= WAIT_REVERSAL:
                self.state = DONE
            return self.state

        if self.state == REVERSAL_ACTIVE:
            if self._aborted:
                self._close_leg(self._reversal, soft=False)
                self.state = DONE
            elif now - self._phase_start >= REVERSAL_HOLD:
                self._close_leg(self._reversal, soft=True)
                self.state = DONE
            return self.state

        return self.state

    # ── entry/exit ───────────────────────────────────────────────────────────

    def _open_leg(self, leg: _Leg, direction: str, size_pct: float,
                  stop_pct: float, take_pct: float, reason: str, agreement: float) -> bool:
        side = "buy" if direction == "long" else "sell"
        proceed, final = self.gate({
            "ticker": self.ticker, "side": side, "proposed_size_pct": size_pct,
            "strategy": "contrarian", "reason": reason, "tf_agreement": agreement,
        })
        if not proceed or final <= 0:
            return False
        if not self.risk.approve_for_event(self.ticker, "contrarian", side, final, self.event_id):
            return False
        if direction == "long":
            self.exec.enter_long(self.ticker, "contrarian", final, stop_pct, take_pct)
        else:
            self.exec.enter_short(self.ticker, "contrarian", final, stop_pct, take_pct)
        leg.open = True
        leg.side = direction
        return True

    def _close_leg(self, leg: _Leg, soft: bool) -> None:
        if not leg.open:
            return
        if soft:
            self.exec.close_soft(self.ticker, "contrarian")
        else:
            self.exec.close_hard(self.ticker, "contrarian")
        leg.open = False

    def cleanup(self) -> None:
        """Hard-close any open legs (shutdown/abort safety)."""
        self._close_leg(self._primary, soft=False)
        self._close_leg(self._reversal, soft=False)
