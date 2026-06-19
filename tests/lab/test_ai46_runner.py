"""Integration test for Ai46Runner — detector → session → paper entry."""
from trader.lab.runtime import Bar
from trader.lab.ai46 import contrarian as C
from trader.lab.ai46.engine import MarketFeatures
from trader.lab.ai46.runner import Ai46Runner


class StubFE:
    """Returns a fixed bearish-aligned feature snapshot with strong +OFI so the
    detector fires an OFI anomaly and the session enters (short, full agreement)."""
    def compute(self, sym, bars, flow=None):
        return MarketFeatures(
            ofi5m=0.9, volume_ratio=1.0, price_change_1m=0.0, hmm_state="flat",
            dir_1h="short", dir_10m="short",
            directions={"1d": "short", "1h": "short", "10m": "short", "1m": "short"},
            garch_vol=0.2, vwap=100.0, tf_agreement=1.0,
        )


def _bar(close, t=1_700_000_000):
    return Bar(time=t, open=close, high=close, low=close, close=close, volume=1000)


async def test_runner_opens_paper_position_after_monitoring():
    r = Ai46Runner(["RIU6"], feature_engine=StubFE())   # klod disabled by default
    bars = {"RIU6": [_bar(100.0)]}

    await r.tick(0.0, bars)                              # detector OFI → session MONITORING
    assert "RIU6" in r.sessions
    assert r.sessions["RIU6"].state == C.MONITORING
    assert r.exec.open_count() == 0                      # no entry yet

    await r.tick(C.MONITORING_DUR + 1, bars)            # monitoring elapsed → primary entry
    assert r.sessions["RIU6"].state == C.PRIMARY_ACTIVE
    opens = [f for f in r.exec.fills if f.kind == "open"]
    assert len(opens) == 1 and opens[0].side == "sell"  # primary short
    assert r.exec.open_exposure() > 0


async def test_runner_no_signal_no_session():
    class Quiet(StubFE):
        def compute(self, sym, bars, flow=None):
            mf = super().compute(sym, bars, flow)
            mf.ofi5m = 0.0          # below detector thresholds
            mf.volume_ratio = 1.0
            return mf
    r = Ai46Runner(["RIU6"], feature_engine=Quiet())
    await r.tick(0.0, {"RIU6": [_bar(100.0)]})
    assert r.sessions == {}
