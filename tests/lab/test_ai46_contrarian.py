"""Tests for the team-46 Smart Contrarian session."""
from trader.lab.ai46 import contrarian as C
from trader.lab.ai46.contrarian import ContrarianSession, _DefaultExecutor


class FF:
    """Fake TickerFeatures: fixed agreement, hmm, volume."""
    def __init__(self, agr=0.8, hmm="flat", vol=1.0):
        self._agr, self.hmm_state, self.volume_ratio = agr, hmm, vol

    def agreement_ratio(self, direction):
        return self._agr


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_decide_primary_direction():
    assert C.decide_primary_direction(0.0, 0.5, 0.5) == "short"            # default
    assert C.decide_primary_direction(0.5, 0.9, 0.5) == "long"             # ofi+agr favour long
    assert C.decide_primary_direction(0.5, 0.55, 0.5) == "short"           # agr not dominant ×1.3


def test_signal_aligned_with():
    assert C.signal_aligned_with(0.5, 0.0, "long") is True
    assert C.signal_aligned_with(-0.5, 0.0, "short") is True
    assert C.signal_aligned_with(0.5, 0.0, "short") is False


def test_is_panic():
    assert C.is_panic("panic", 1.0) is True
    assert C.is_panic("flat", 11) is True
    assert C.is_panic("flat", 2) is False


# ── state machine ─────────────────────────────────────────────────────────────

def test_monitoring_then_primary_entry():
    ex = _DefaultExecutor()
    s = ContrarianSession("RIU6", executor=ex)
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    assert s.state == C.MONITORING and s.primary == "short"
    assert s.step(100, FF(0.8)) == C.MONITORING            # before monitoring dur
    assert s.step(301, FF(0.8)) == C.PRIMARY_ACTIVE        # after 5 min
    assert ex.calls[0] == ("short", "RIU6", 0.03 * 0.8, 0.015, 0.025)


def test_panic_aborts_before_entry():
    s = ContrarianSession("RIU6")
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    assert s.step(301, FF(0.8, hmm="panic")) == C.DONE


def test_low_agreement_skips():
    s = ContrarianSession("RIU6")
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    assert s.step(301, FF(0.3)) == C.DONE                  # agr < 0.5


def test_abort_closes_primary_hard():
    ex = _DefaultExecutor()
    s = ContrarianSession("RIU6", executor=ex)
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    s.step(301, FF(0.8))                                   # PRIMARY_ACTIVE
    s.deliver_verdict("abort")
    assert s.step(310, FF(0.8)) == C.ABORT
    assert ("close_hard", "RIU6") in ex.calls


def test_full_cycle_to_reversal_and_done():
    ex = _DefaultExecutor()
    s = ContrarianSession("RIU6", executor=ex)
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)  # primary short
    s.step(301, FF(0.8))                                       # PRIMARY_ACTIVE @301
    assert s.step(301 + 901, FF(0.8)) == C.WAITING_REVERSAL    # primary hold elapsed → soft close
    assert ("close_soft", "RIU6") in ex.calls
    # reversal of short = long; aligned signals need ofi>0
    for _ in range(3):
        s.deliver_signal(0.5, 0.0)
    assert s.step(301 + 902, FF(0.8)) == C.REVERSAL_ACTIVE
    assert any(c[0] == "long" for c in ex.calls)              # reversal long entered
    assert s.step(301 + 902 + 1801, FF(0.8)) == C.DONE         # reversal hold elapsed


def test_waiting_reversal_times_out_without_confirmations():
    s = ContrarianSession("RIU6")
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    s.step(301, FF(0.8))
    s.step(301 + 901, FF(0.8))                                 # WAITING_REVERSAL
    assert s.step(301 + 901 + 601, FF(0.8)) == C.DONE          # 10 min, <3 sigs


def test_gate_rejection_skips_entry():
    ex = _DefaultExecutor()
    s = ContrarianSession("RIU6", executor=ex, gate=lambda p: (False, 0.0))
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    assert s.step(301, FF(0.8)) == C.DONE
    assert ex.calls == []


def test_regime_not_allowed_done_at_start():
    class NoTrade:
        def regime_role(self, strategy): return (False, 0.0)
        def approve_for_event(self, *a): return True
    s = ContrarianSession("RIU6", risk=NoTrade())
    s.start(now=0, sig_ofi=0.0, long_agr=0.5, short_agr=0.5)
    assert s.state == C.DONE
