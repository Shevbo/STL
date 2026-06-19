"""Ai46Runner — the team-46 strategy loop, one tick per evaluation.

Ties together: order-flow (Phase 3) → FeatureEngine (Phase 1/2) → Detector
(Phase 5) → ContrarianSession (Phase 5) with the LLM PM gate (Phase 4) →
PaperExecutor/RiskManager. Runs as a privileged backend task (NOT the sandbox).

The contrarian session's gate is synchronous; the LLM gate is async, so the
runner refreshes a per-ticker (proceed, size_factor) cache just before a session
would enter and the session's sync gate applies it. With the LLM disabled the
PM verdict degrades to full-size approval (like the go-bot when the LLM is down).
"""
from __future__ import annotations

from trader.lab.ai46 import contrarian as C
from trader.lab.ai46 import detector as DET
from trader.lab.ai46 import llm as LLM
from trader.lab.ai46.engine import FeatureEngine
from trader.lab.ai46.execution import PaperExecutor, RiskManager
from trader.lab.ai46.order_flow import OrderFlow

# Detector signal types that launch / feed a contrarian session.
_ENTRY_SIGNALS = {DET.OFI_ANOMALY, DET.PRICE_SHOCK, DET.VOL_SPIKE, DET.NEWS, DET.TREND_FLIP}


class Ai46Runner:
    def __init__(self, symbols, *, klod=None, feature_engine=None,
                 executor=None, risk=None, order_flow=None) -> None:
        self.symbols = list(symbols)
        self.klod = klod or LLM.KlodClient(enabled=False)   # degraded until wired
        self.fe = feature_engine or FeatureEngine()
        self.exec = executor or PaperExecutor()
        self.risk = risk or RiskManager(self.exec)
        self.flow = order_flow or OrderFlow()
        self.detector = DET.Detector()
        self.sessions: dict[str, C.ContrarianSession] = {}
        self._gate: dict[str, tuple[bool, float]] = {}   # ticker -> (proceed, size_factor)

    def _make_gate(self, sym: str):
        def gate(proposal: dict):
            proceed, factor = self._gate.get(sym, (True, 1.0))
            return proceed, float(proposal.get("proposed_size_pct", 0.0)) * factor
        return gate

    def _about_to_enter(self, s: C.ContrarianSession, now: float) -> bool:
        if s.state == C.MONITORING:
            return now - s._phase_start >= C.MONITORING_DUR
        if s.state == C.WAITING_REVERSAL:
            return s._confirmations >= C.REVERSAL_SIGS
        return False

    async def _refresh_gate(self, sym: str, feat) -> None:
        side = "sell" if self.sessions[sym].primary == "short" else "buy"
        proposal = {
            "ticker": sym, "side": side, "proposed_size_pct": C.SIZE_BASE,
            "strategy": "contrarian", "reason": "contrarian entry",
            "ofi": feat.ofi5m, "tf_agreement": feat.tf_agreement,
            "hmm_state": feat.hmm_state, "garch_vol": feat.garch_vol,
            "current_price": self.exec.price_of(sym),
            "price_change_pct": feat.price_change_1m, "volume_ratio": feat.volume_ratio,
        }
        v = await LLM.evaluate_proposal(self.klod, proposal)
        self._gate[sym] = (v.verdict != "REJECT", v.size_factor)

    async def tick(self, now: float, bars_by_symbol: dict) -> None:
        for sym in self.symbols:
            bars = bars_by_symbol.get(sym)
            if not bars:
                continue
            feat = self.fe.compute(sym, bars, self.flow)
            if feat is None:
                continue
            self.exec.set_price(sym, bars[-1].close)
            self.exec.set_time(now)
            self.risk.set_regime(feat.hmm_state)

            sigs = self.detector.check_ticker(sym, feat, now)
            sess = self.sessions.get(sym)
            for s in sigs:
                if s.type not in _ENTRY_SIGNALS:
                    continue
                if sess is None or sess.state in (C.DONE, C.ABORT):
                    sess = C.ContrarianSession(sym, executor=self.exec, risk=self.risk,
                                               gate=self._make_gate(sym))
                    sess.start(now, s.ofi, feat.agreement_ratio("long"),
                               feat.agreement_ratio("short"), s.type)
                    self.sessions[sym] = sess
                else:
                    sess.deliver_signal(s.ofi, s.price_change)

            if sess is not None:
                if self._about_to_enter(sess, now):
                    await self._refresh_gate(sym, feat)
                sess.step(now, feat)
                if sess.state in (C.DONE, C.ABORT):
                    self.sessions.pop(sym, None)

    def deliver_verdict(self, sym: str, verdict: str) -> None:
        s = self.sessions.get(sym)
        if s is not None:
            s.deliver_verdict(verdict)
