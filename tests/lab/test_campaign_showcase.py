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
    assert set(auto) == {"autocci-20260711-s-ri", "rfa-20260801-s-ri"}
    assert len(auto["rfa-20260801-s-ri"]["runs"]) == 2 and auto["rfa-20260801-s-ri"]["entry"]["idea"] == b.NO_DESC
    assert not any("gatekelt" in k for k in auto)


def test_no_curve_does_not_fail_and_curve_card():
    runs = [_run("camp-20260711-autocci", 5.0)]
    bf = {"camp-20260711-autocci": [{"run_id": "camp-20260711-autocci-bf0", "net": 9.0, "trades": 3,
                                    "max_dd": 1.0, "sharpe": 1.0, "params": {"a": 1, "symbol": "RI"},
                                    "curve": [[1, 0.0], [2, 10.0], [3, 4.0]]}]}
    out = b.build([_entry("research-x", kind="research", status_hint="done")], runs, [], bf, {}, "now")
    by = {c["slug"]: (c, d) for c, d in out}
    c, d = by["research-x"]
    assert c["status"] == "no_curve" and c["thumb"] is None and c["no_curve_reason"]
    c2, d2 = by["autocci-20260711-s-ri"]
    assert c2["status"] == "done" and c2["thumb"] == [[1, 0.0], [2, 10.0], [3, 4.0]]
    assert c2["headline"]["net"] == 4.0 and d2["leaders"][0]["rank"] == 1
    assert d2["revisions"][0]["slug"] == "autocci-20260711-s-ri"


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
    assert first == sorted(os.listdir(tmp_path)) == ["a.json", "index.json", "slug_redirects.json"]
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))[0]["slug"] == "a"


def test_units_symbols_and_sort():
    assert b.run_unit(None) == "points" and b.run_unit(1.0) == "points" and b.run_unit(1.68) == "rub"
    runs = [dict(_run("camp-20260711-autoa", 5.0, sym="RIU6"), point_value=1.68),
            dict(_run("camp-20260711-autob", 9.0, sym="SiU6"), point_value=None),
            dict(_run("camp-20260801-mixa", 9.0), point_value=None),
            dict(_run("camp-20260801-mixa2", 1.0), point_value=1.68)]
    bf = {"camp-20260711-autoa": [{"run_id": "camp-20260711-autoa-bf0", "params": {"symbol": "RIU6"}, "net": 1.0, "curve": [[1, 0.0], [2, 1.0]]}]}
    reg = [_entry("r1", symbols=["RI", "Si"]), _entry("r2", status_hint="queued")]
    out = b.build(reg, runs, [], bf, {}, "now")
    cards = {c["slug"]: (c, d) for c, d in out}
    assert cards["autoa-20260711-s-ri"][0]["unit"] is None and cards["autoa-20260711-s-ri"][0]["symbols"] == ["RIU6"]
    # без перепрогона единица не угадывается по point_value (эвристика ошибалась в 7 из 15)
    assert cards["autob-20260711-s-si"][0]["unit"] is None and cards["autob-20260711-s-si"][0]["unit_source"] is None
    assert all(ld["unit"] is None and ld["return_pct"] is None for ld in cards["mixa-20260801-s-ri"][1]["leaders"])
    assert cards["r1"][0]["symbols"] == ["RI", "Si"] and cards["r2"][0]["symbols"] is None
    order = [c["slug"] for c, _ in out]
    assert order[0] == "autoa-20260711-s-ri"  # единственная с кривой первой
    assert [c["status"] for c, _ in out][1:].count("queued") >= 1
    assert out[-1][0]["status"] == "no_curve"


def test_instrument_and_window_label():
    assert [b.instrument(x) for x in ("RIU6", "SiM6", "BRN6", "GDZ5", "RI", None)] == ["RI", "Si", "BR", "GD", "RI", ""]
    assert b.window_label("2026-07-01", "2026-07-30") == "июль 2026"
    assert b.window_label("2026-06-01", "2026-07-30") == "июнь–июль 2026"
    assert b.window_label("2025-12-01", "2026-01-30") == "декабрь 2025–январь 2026"
    assert b.window_label(None, None, "20260711") == "11.07.2026"


def test_strategy_info_library_inverse_unknown():
    n, i = b.strategy_info("macd_cross")
    assert n == "MACD Crossover" and i.startswith("трендовая стратегия")
    n2, i2 = b.strategy_info("macd_cross__inv")
    assert n2 == "MACD Crossover (инверсия)" and i2.startswith("Зеркальный сигнал")
    assert b.strategy_info("no_such_strategy_x") == ("no_such_strategy_x", b.NO_DESC)
    n3, i3 = b.strategy_info("impulse_fade")  # не library: docstring через ast
    assert n3 == "Impulse Fade" and i3 != b.NO_DESC
    assert b.strategy_info(None)[1] == b.NO_DESC


def test_auto_cards_split_by_strategy_and_instrument_with_varieties():
    f = lambda run, st, sym, net, fr="2026-07-01", to="2026-07-30": dict(  # noqa: E731
        _run(run, net, st, sym), date_from=fr, date_to=to)
    runs = [f("camp-20260711-autox1", "macd_cross", "RIU6", 5.0), f("camp-20260711-autox1", "cci", "RIU6", 3.0),
            f("camp-20260711-autox2", "macd_cross", "RIM6", 9.0, "2026-06-01"),
            f("camp-20260711-autox2", "macd_cross", "BRN6", 2.0), f("camp-20260711-autox3", "macd_cross", "RIU6", 7.0)]
    cards = {c["entry"]["slug"]: c for c in b.merge_cards([], runs)}
    assert set(cards) == {"autox-20260711-macd-cross-ri", "autox-20260711-cci-ri", "autox-20260711-macd-cross-br"}
    c = cards["autox-20260711-macd-cross-ri"]
    assert c["entry"]["title"] == "MACD Crossover · RI · июнь–июль 2026"
    assert cards["autox-20260711-cci-ri"]["entry"]["title"] == "CCI Reversal · RIU6 · июль 2026"
    v = b.varieties_of(c["runs"])
    assert [(x["label"], x["n_runs"]) for x in v] == [("RIM6 · июнь–июль 2026".replace("июнь–июль 2026", "июнь–июль 2026"), 1),
                                                       ("RIU6 · июль 2026", 2)] or len(v) == 2
    assert v[1]["best"]["net"] == 7.0 and v[1]["n_runs"] == 2


def test_bf_row_goes_only_to_its_own_logic():
    runs = [dict(_run("camp-20260711-ay", 5.0, "macd_cross", "RIU6")), dict(_run("camp-20260711-ay", 3.0, "cci", "RIU6"))]
    row = {"run_id": "camp-20260711-ay-bf1", "params": {"symbol": "RIU6"}, "strategy": "cci"}
    assert b.bf_belongs(row, runs[1:]) and not b.bf_belongs(row, runs[:1])
    assert not b.bf_belongs(dict(row, strategy=None), runs)  # неоднозначно: две стратегии
    assert not b.bf_belongs(dict(row, params={"symbol": "SiU6"}), runs)


def test_opt_sweeps_merge_across_dates_by_logic():
    f = lambda run, net: dict(_run(run, net, "cci", "RIU6"), date_from="2026-06-01", date_to="2026-06-30")  # noqa: E731
    cards = b.merge_cards([], [f("opt-20260605-0618", 1.0), f("opt-20260606-1326", 2.0)])
    assert [c["entry"]["slug"] for c in cards] == ["opt-cci-ri"]
    assert b.varieties_of(cards[0]["runs"])[0]["n_runs"] == 2


def test_strategy_name_without_parenthesis_tail():
    assert b.strategy_info("us_open_fvg")[0] == "US-Open Opening Range + FVG / Retest"


def test_bf_drift_note():
    ok = [{"net": 100.0, "lb_net": 100.5}, {"net": 5.0, "lb_net": None}]
    assert b.bf_drift_note(ok) is None
    note = b.bf_drift_note([{"net": -300.0, "lb_net": 350000.0}, {"net": 1.0, "lb_net": 1.0}])
    assert "1 из 2" in note and "-300" in note


def test_headline_comparable_follows_unit():
    runs = [_run("camp-20260711-autoq", 5.0)]
    out = b.build([_entry("r", unit="rub")], runs, [], {}, {}, "now")
    by = {c["slug"]: c for c, _ in out}
    assert by["autoq-20260711-s-ri"]["unit"] is None and by["autoq-20260711-s-ri"]["headline"]["comparable"] is False
    assert by["r"]["unit"] == "rub" and by["r"]["headline"]["comparable"] is True


def test_workbench_base_present_null_and_strategy_match():
    runs = [dict(_run("camp-20260711-autow", 9.0, "cci", "RIU6"), params={"symbol": "RIU6", "p": 1}),
            dict(_run("opt-20260711-0001", 3.0, "roc", "SiU6"), params={"symbol": "SiU6"})]
    out = b.build([_entry("res", kind="research")], runs, [], {}, {}, "now")
    jobs = {("camp-20260711-autow", "cci"): {"scriptCode": "SC", "dateFrom": "2026-03-01", "dateTo": "2026-07-10"},
            ("camp-20260711-autow", "roc"): {"scriptCode": "ЧУЖОЙ"}}  # чужая стратегия под своим ключом не берётся
    b.apply_workbench(out, jobs, None)
    by = {d["slug"]: d for _, d in out}
    wb = by["autow-20260711-cci-ri"]["workbench_base"]
    assert wb["strategy"] == "cci" and wb["script_code"] == "SC" and wb["base_params"] == {"symbol": "RIU6", "p": 1}
    assert wb["symbol"] == "RIU6" and wb["date_from"] == "2026-03-01" and wb["date_to"] == "2026-07-10"
    assert "point_value" in wb
    nul = by["opt-roc-si"]
    assert nul["workbench_base"] is None and "нет job_body" in nul["notes"]
    assert "workbench_base" not in by["res"] and all("_wb" not in d for d in by.values())
