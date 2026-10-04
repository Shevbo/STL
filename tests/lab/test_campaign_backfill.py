from scripts import campaign_backfill as bf
from scripts import campaign_showcase_build as sb


def _r(run, net, created, sym="RIU6", st="cci", trades=50):
    return {"campaign_run": run, "net": net, "trades": trades, "strategy": st, "symbol": sym,
            "created_at": created, "max_dd": 1.0, "date_from": None, "date_to": None}


def test_pick_rules_fresh_among_strong_unique_run_not_service():
    runs = [_r("camp-20260901-aa1", 5, "2026-09-01"), _r("camp-20260902-bb1", 5, "2026-09-02"),
            _r("camp-20261001-gatex1", 9, "2026-10-01"), _r("camp-20260903-cc1", -1, "2026-09-03"),
            _r("camp-20260904-dd1", 5, "2026-09-04", trades=3)]
    cards = sb.merge_cards([], runs)
    scores = {c["entry"]["slug"]: {"aa": 9, "bb": 8, "gate": 99, "cc": 9, "dd": 9}[c["entry"]["slug"].split("-")[0][:2] if not c["entry"]["slug"].startswith("gate") else "gate"] for c in cards}
    got = [s for s, _ in bf.pick(cards, scores, n=5)]
    assert got == ["bb-20260902-cci-ri", "aa-20260901-cci-ri"]


def test_job_and_net_tolerance():
    it = {"campaign_run": "camp-1", "script_code": "x", "symbol": "RI", "date_from": "a", "date_to": "b", "robot_id": "r"}
    ld = {"rank": 2, "params": {"p": 1}}
    assert bf.bf_name(it, ld) == "camp-1-bf2" and bf.job_of(it, ld)["paramSets"] == [{}]
    assert bf.job_of(it, ld)["engine"] == "remote"
    assert bf.net_matches(100000.0, 100500.0) and not bf.net_matches(100000.0, 90000.0)
    assert not bf.net_matches(None, 1.0)
