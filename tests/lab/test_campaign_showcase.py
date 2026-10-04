import json
import os

from scripts import campaign_showcase_build as b


def _run(name, net=1.0, strat="s", sym="RI"):
    return {"campaign_run": name, "net": net, "trades": 5, "max_dd": 1.0, "strategy": strat,
            "symbol": sym, "date_from": "2026-01-01", "date_to": "2026-02-01", "created_at": "2026-10-01T00:00:00"}


def _entry(slug, **kw):
    e = {"slug": slug, "title": slug, "idea": "i", "family": slug, "rev": 1, "campaign_runs": [],
         "task_ids": [], "task_modules": [], "status_hint": "done", "unit": "rub", "kind": "research"}
    e.update(kw)
    return e


def test_downsample_keeps_extremes_and_ends():
    pts = [[i, (i % 50) * 1.0] for i in range(10000)]
    pts[4321][1] = 9999.0
    pts[7777][1] = -9999.0
    out = b.downsample(pts, 200)
    assert len(out) <= 200
    assert out[0] == pts[0] and out[-1] == pts[-1]
    ys = [p[1] for p in out]
    assert max(ys) == 9999.0 and min(ys) == -9999.0
    assert [p[0] for p in out] == sorted(p[0] for p in out)
    assert b.downsample(pts[:10], 200) == pts[:10]


def test_curves_and_drawdown():
    c = b.equity_to_curve([{"time": 1, "equity": 100.0}, {"time": 2, "equity": 130.0}, {"time": 3, "equity": 90.0}])
    assert c == [[1, 0.0], [2, 30.0], [3, -10.0]]
    assert b.max_drawdown(c) == 40.0
    assert b.max_drawdown([]) is None
    d = b.days_to_curve(["2026-09-01", "2026-09-02"], [5.0, -2.0])
    assert [p[1] for p in d] == [5.0, 3.0] and d[1][0] - d[0][0] == 86400


def test_status_rules():
    t = lambda *s: [{"status": x} for x in s]  # noqa: E731
    assert b.status_of(t("done", "running"), False, False, "done") == ("running", {"finished": 1, "total": 2})
    assert b.status_of(t("pending", "pending"), False, False, None)[0] == "queued"
    assert b.status_of(t("done"), True, False, None)[0] == "done"
    assert b.status_of(t("done"), False, False, None)[0] == "no_curve"
    assert b.status_of([], False, False, None) == ("queued", None)
    assert b.status_of([], False, True, None)[0] == "no_curve"
    assert b.status_of([], True, True, "queued")[0] == "done"


def test_registry_beats_auto_and_groups_shards():
    runs = [_run("camp-20261001-gatekelt1w1"), _run("camp-20261001-gatekelt2w2"),
            _run("camp-20260711-autocci", 5.0), _run("camp-20260801-rfa0"), _run("camp-20260801-rfa1", 9.0)]
    reg = [_entry("kelt", campaign_runs=["camp-20261001-gatekelt*"], title="Гейт")]
    cards = b.merge_cards(reg, runs)
    assert cards[0]["entry"]["title"] == "Гейт" and len(cards[0]["runs"]) == 2
    auto = {c["entry"]["slug"]: c for c in cards[1:]}
    assert set(auto) == {"autocci-20260711", "rfa-20260801"}
    assert len(auto["rfa-20260801"]["runs"]) == 2 and auto["rfa-20260801"]["entry"]["idea"] == b.NO_DESC
    assert not any("gatekelt" in k for k in auto)


def test_no_curve_does_not_fail_and_curve_card():
    runs = [_run("camp-20260711-autocci", 5.0)]
    bf = {"camp-20260711-autocci": [{"run_id": "camp-20260711-autocci-bf0", "net": 9.0, "trades": 3,
                                    "max_dd": 1.0, "sharpe": 1.0, "params": {"a": 1},
                                    "curve": [[1, 0.0], [2, 10.0], [3, 4.0]]}]}
    out = b.build([_entry("research-x", kind="research", status_hint="done")], runs, [], bf, {}, "now")
    by = {c["slug"]: (c, d) for c, d in out}
    c, d = by["research-x"]
    assert c["status"] == "no_curve" and c["thumb"] is None and c["no_curve_reason"]
    c2, d2 = by["autocci-20260711"]
    assert c2["status"] == "done" and c2["thumb"] == [[1, 0.0], [2, 10.0], [3, 4.0]]
    assert c2["headline"]["net"] == 4.0 and d2["leaders"][0]["rank"] == 1
    assert d2["revisions"][0]["slug"] == "autocci-20260711"


def test_research_sweep_curve_from_task_results():
    res = {"t1": [{"phase": "test", "instrument": "RI", "window": "3m", "test_days": ["2026-09-01", "2026-09-02"],
                   "results": [{"tag": "sel", "vec": {"L": 1}, "net_t": 7.0, "net_m": 8.0, "cycles": 2,
                                "fills": 4, "day_net": [3.0, 4.0]}]}]}
    e = _entry("sw", task_ids=["t*"], curve={"kind": "sweep", "tasks": ["t*"]})
    tasks = [{"id": "t1", "module": "m", "status": "done", "finished_at": "2026-10-04T00:00:00"}]
    (c, d), = b.build([e], [], tasks, {}, res, "now")
    assert c["status"] == "done" and c["thumb"][-1][1] == 7.0 and c["progress"] == {"finished": 1, "total": 1}


def test_write_all_atomic_and_idempotent(tmp_path):
    out = b.build([_entry("a")], [], [], {}, {}, "now")
    b.write_all(out, str(tmp_path))
    first = sorted(os.listdir(tmp_path))
    (tmp_path / "stale.json").write_text("{}")
    b.write_all(out, str(tmp_path))
    assert first == sorted(os.listdir(tmp_path)) == ["a.json", "index.json"]
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))[0]["slug"] == "a"
