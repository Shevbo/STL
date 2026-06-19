"""Tests for the team-46 event detector."""
from trader.lab.ai46 import detector as D
from trader.lab.ai46.detector import Detector, TickerFeatures as TF


def test_ofi_anomaly_trigger():
    d = Detector()
    sigs = d.check_ticker("RIU6", TF(ofi5m=0.8), now=0)
    assert any(s.type == D.OFI_ANOMALY for s in sigs)
    assert sigs[0].category == D.NORMAL  # ofi 0.8 not extreme, vol 1


def test_volume_spike_trigger():
    d = Detector()
    sigs = d.check_ticker("RIU6", TF(volume_ratio=3.5), now=0)
    assert any(s.type == D.VOL_SPIKE for s in sigs)


def test_emit_cooldown_suppresses_repeat():
    d = Detector()
    first = d.check_ticker("RIU6", TF(ofi5m=0.8), now=0)
    again = d.check_ticker("RIU6", TF(ofi5m=0.8), now=100)   # <300s → suppressed
    later = d.check_ticker("RIU6", TF(ofi5m=0.8), now=400)   # >300s → allowed
    assert any(s.type == D.OFI_ANOMALY for s in first)
    assert not any(s.type == D.OFI_ANOMALY for s in again)
    assert any(s.type == D.OFI_ANOMALY for s in later)


def test_price_shock_after_seed():
    d = Detector()
    assert d.check_ticker("X", TF(price_change_1m=0.0), now=0) == []  # seed
    sigs = d.check_ticker("X", TF(price_change_1m=5.0), now=10)
    ps = [s for s in sigs if s.type == D.PRICE_SHOCK]
    assert ps and ps[0].sigma > 2


def test_classify_black_swan_and_uncertain():
    d = Detector()
    assert d.classify(TF(hmm_state="panic"), 0) == D.BLACK_SWAN
    assert d.classify(TF(ofi5m=0.9, volume_ratio=6), 0) == D.UNCERTAIN  # 2 extremes
    assert d.classify(TF(ofi5m=0.9), 0) == D.NORMAL                     # 1 extreme


def test_trend_flip_requires_decisive_reversal_confirmed():
    d = Detector()
    # seed last_dir = long
    d.check_ticker("MX", TF(dir_1h="long", dir_10m="long"), now=0)
    sigs = d.check_ticker("MX", TF(dir_1h="short", dir_10m="short"), now=400)
    assert any(s.type == D.TREND_FLIP for s in sigs)


def test_news_signal_severity_gate():
    d = Detector()
    assert d.classify_news_signal("RIU6", TF(), severity=5, now=0) is None
    s = d.classify_news_signal("RIU6", TF(), severity=7, now=0)
    assert s is not None and s.type == D.NEWS and s.category == D.UNCERTAIN
