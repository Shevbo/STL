import json

from trader.util import i9_hb_view


def _raw(**over):
    d = {
        "_recv_ts": 1000.0, "cpu_pct": 87.5, "per_core": [90, 85, 80, 95],
        "cpu_count": 20, "workers": 18, "priority": "idle", "ram_pct": 61.0,
        "ram_used_mb": 40000, "ram_total_mb": 65000, "version": "2026-07-18-cpu-hb",
        "psutil": True, "agent_id": "Win10-HyperV:1234",
        "leaders": [{"strategy": "cci", "symbol": "RIU6", "net": 12345, "rf": 3.1,
                     "trades": 40, "params": {"qty": 1, "tp_atr": 60}}],
        "activity": {"state": "job", "run_id": "camp-x", "symbol": "RIU6", "combos": 300},
    }
    d.update(over)
    return json.dumps(d)


def test_none_when_never_reported():
    assert i9_hb_view(None, 1000.0) is None
    assert i9_hb_view("", 1000.0) is None


def test_none_on_bad_json():
    assert i9_hb_view("{not json", 1000.0) is None


def test_fresh_passthrough():
    v = i9_hb_view(_raw(), now_ts=1004.0)   # 4s old
    assert v is not None
    assert v["cpu_pct"] == 87.5
    assert v["per_core"] == [90, 85, 80, 95]
    assert v["workers"] == 18 and v["cpu_count"] == 20
    assert v["has_psutil"] is True
    assert v["activity"]["symbol"] == "RIU6"
    assert v["priority"] == "idle"
    assert len(v["leaders"]) == 1 and v["leaders"][0]["net"] == 12345
    assert v["leaders"][0]["params"] == {"qty": 1, "tp_atr": 60}
    assert v["age_sec"] == 4
    assert v["stale"] is False


def test_leaders_default_empty_when_absent():
    v = i9_hb_view(_raw(leaders=None), now_ts=1000.0)
    assert v["leaders"] == []


def test_stale_when_old():
    v = i9_hb_view(_raw(), now_ts=1030.0)   # 30s old > 15s default
    assert v["stale"] is True
    assert v["age_sec"] == 30


def test_stale_when_no_recv_ts():
    v = i9_hb_view(_raw(_recv_ts=0), now_ts=1000.0)
    assert v["age_sec"] is None
    assert v["stale"] is True


def test_activity_defaults_when_missing():
    v = i9_hb_view(_raw(activity=None), now_ts=1000.0)
    assert v["activity"] == {"state": "?"}
