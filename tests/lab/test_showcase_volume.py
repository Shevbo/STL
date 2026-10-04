import json

from scripts import campaign_showcase_build as b
from scripts import showcase_volume as sv


def test_return_pct_and_null_behaviour():
    assert sv.full_cost_rub(10, 100.0, 2.0) == 2000.0
    assert sv.return_pct(500.0, 2000.0) == 25.0
    assert sv.full_cost_rub(None, 100.0, 2.0) is None and sv.full_cost_rub(10, 100.0, None) is None
    assert sv.return_pct(None, 2000.0) is None and sv.return_pct(5.0, None) is None and sv.return_pct(5.0, 0) is None
    assert sv.score_of(2.0, 10.0, 0.5) == 10.0 and sv.score_of(None, 10.0, 0.5) is None


def test_peak_gross_and_unit():
    tr = [{"qty": 2, "side": "buy", "time": 1, "price": 100.0}, {"qty": 3, "side": "buy", "time": 2, "price": 110.0},
          {"qty": 5, "side": "sell", "time": 3, "price": 120.0}]
    assert sv.peak_from_trades(tr) == (5, 110.0, 2)
    assert sv.peak_from_trades([]) is None
    assert round(sv.gross_points(tr), 6) == round((120 - 106) * 5, 6)  # средняя (200+330)/5 = 106
    assert sv.infer_unit(700.0, 70.0, 10.0) == "rub" and sv.infer_unit(71.0, 70.0, 10.0) == "points"
    assert sv.infer_unit(5.0, 5.0, 1.0) == "rub" and sv.infer_unit(5.0, None, 10.0) is None


def test_month_share_calendar_months():
    d = lambda y, m, day: int(__import__("datetime").datetime(y, m, day, tzinfo=__import__("datetime").timezone.utc).timestamp())  # noqa: E731
    curve = [[d(2026, 1, 5), 0.0], [d(2026, 1, 28), 10.0], [d(2026, 2, 10), 4.0], [d(2026, 3, 3), 9.0]]
    assert sv.month_share(curve) == round(2 / 3, 4)  # янв +10, фев -6, мар +5
    assert sv.month_share([[1, 0.0]]) is None


def test_buyhold_linear_growth_and_roll_basis():
    bars = [[i * 60, 100.0 + i, 0, 0, 100.0 + i + 1, 1, 0] for i in range(5)]  # цена растёт на 1 за бар
    out = sv.buyhold_curve(bars, {}, [3.0], 0, 10**9, 2, True, 100, b.downsample)
    assert [p[1] for p in out] == [6.0, 12.0, 18.0, 24.0, 30.0]  # 1 пункт x 2 контракта x pv 3
    pts = sv.buyhold_curve(bars, {}, [3.0], 0, 10**9, 2, False, 100, b.downsample)
    assert pts[-1][1] == 10.0
    # перекат: на баре 3 контракт дороже на 50, разница вычитается - кривая остаётся линейной
    bars2 = [[i * 60, 100.0 + i, 0, 0, 100.0 + i + 1 + (50 if i >= 3 else 0), 1, 0] for i in range(5)]
    out2 = sv.buyhold_curve(bars2, {3: 50.0}, [1.0], 0, 10**9, 1, True, 100, b.downsample)
    assert [p[1] for p in out2] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert sv.buyhold_curve(bars, {}, [None], 0, 10**9, 1, True, 100, b.downsample) is None
    assert sv.buyhold_curve([], {}, [1.0], 0, 1, 1, True, 10, b.downsample) is None


def _ld(i, score, net, curve=True):
    return {"params": {"i": i}, "score": score, "net": net, "curve": [[1, 0.0], [2, 1.0]] if curve else None,
            "buyhold_curve": [[1, 0.0]] if curve else None, "metrics": {}}


def test_top10_inline_rest_curve_url_and_sort():
    lds = [_ld(i, float(i), 1.0) for i in range(12)] + [_ld(50, None, 99.0, False), _ld(51, 5.0, 7.0, False)]
    out, lazy = b.finalize_leaders("s", lds)
    assert [x["score"] for x in out][:3] == [11.0, 10.0, 9.0] and out[-1]["score"] is None
    inline = [x for x in out[:10] if x["params"]["i"] < 12]
    assert inline and all(x["curve"] and x["curve_url"] is None for x in inline)
    tail = [x for x in out[10:] if x["params"]["i"] < 12]
    assert tail and all(x["curve"] is None and x["buyhold_curve"] is None and x["curve_url"] for x in tail)
    assert tail[0]["curve_url"] == f"s.leader-{tail[0]['rank']}.json"
    assert [z["rank"] for z in lazy] == [x["rank"] for x in tail] and lazy[0]["curve"]
    # без кривой: curve_url нет вовсе (лениво собирать нечего)
    nocurve = [x for x in out if x["params"]["i"] in (50, 51)]
    assert all(x["curve_url"] is None for x in nocurve)
    # при равном score решает net
    two, _ = b.finalize_leaders("s", [_ld(1, 5.0, 1.0, False), _ld(2, 5.0, 9.0, False)])
    assert two[0]["params"]["i"] == 2
    big, _ = b.finalize_leaders("s", [_ld(i, float(i), 1.0, False) for i in range(150)])
    assert len(big) == 100


class _Ctx:
    def pv_at(self, sym, ts):
        return 2.0

    def buyhold(self, sym, t0, t1, n, to_rub, n_points, ds):
        return [[t0, 0.0], [t1, n * 10.0]]


def test_bf_leader_full_fields_and_headline():
    tr = [{"qty": 3, "side": "buy", "time": 10, "price": 100.0}, {"qty": 3, "side": "sell", "time": 20, "price": 110.0}]
    row = {"run_id": "camp-1-bf0", "net": 60.0, "trades": 2, "max_dd": 1.0, "sharpe": 1.0, "lb_net": 50.0,
           "params": {"symbol": "RIU6"}, "curve": [[10, 0.0], [20, 60.0]], "peak": sv.peak_from_trades(tr),
           "gross_pts": sv.gross_points(tr), "rf": 2.0, "peak_col": 3}
    ld = b.make_bf_leader(row, _Ctx(), "rub")
    assert ld["contracts_peak"] == 3 and ld["full_cost_rub"] == 3 * 100.0 * 2.0 and ld["unit"] == "rub"
    assert ld["return_pct"] == round(60.0 / 600.0 * 100, 2) and ld["buyhold_curve"][-1][1] == 30.0
    assert ld["metrics"]["rerun_note"] == "перепрогон на текущем движке" and ld["metrics"]["lb_net"] == 50.0
    assert ld["unit_source"] == "measured" and ld["l_share_source"] == "curve" and ld["score"] == 2.0 * 60.0 * ld["l_share"]
    nul = b.make_bf_leader({**row, "peak": None, "peak_col": None, "gross_pts": None}, None, "rub")
    assert nul["contracts_peak"] is None and nul["full_cost_rub"] is None and nul["return_pct"] is None
    assert nul["buyhold_curve"] is None and nul["unit"] is None and nul["unit_source"] is None


def test_row_leader_windows_fallback_and_nulls():
    ld = b.make_row_leader({"campaign_run": "c", "net": 10.0, "trades": 5, "max_dd": 1.0, "rf": 2.0, "wp": 3, "wt": 4,
                            "params": {}}, "points")
    assert ld["l_share"] == 0.75 and ld["l_share_source"] == "leaderboard_windows" and ld["score"] == 15.0
    assert ld["contracts_peak"] is None and ld["full_cost_rub"] is None and ld["curve"] is None
    assert ld["unit"] is None and ld["unit_source"] is None and ld["return_pct"] is None
    assert b.make_row_leader({"campaign_run": "c", "net": 1.0}, "rub")["l_share"] is None


def test_redirects_and_lazy_files(tmp_path):
    f = lambda run, st, net: dict(_row(run, st), net=net)  # noqa: E731
    runs = [f("camp-20260711-autox1", "cci", 5.0), f("camp-20260711-autox1", "roc", 9.0)]
    out = b.build([], runs, [], {}, {}, "now")
    red = b.build_redirects(out)
    assert red == {"autox-20260711": "autox-20260711-roc-ri"}  # лучшая по net (score нет)
    best = [d for _, d in out if d["slug"] == "autox-20260711-roc-ri"][0]
    assert "autox-20260711-cci-ri" in best["notes"]
    out[0][1]["_lazy"] = [{"rank": 11, "curve": [[1, 1.0]], "buyhold_curve": None}]
    b.write_all(out, str(tmp_path), red)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert f"{out[0][1]['slug']}.leader-11.json" in names and "slug_redirects.json" in names
    assert json.loads((tmp_path / "slug_redirects.json").read_text(encoding="utf-8")) == red
    assert "_lazy" not in json.loads((tmp_path / f"{out[0][1]['slug']}.json").read_text(encoding="utf-8"))
    b.write_all(out[:1], str(tmp_path), red)  # лишние ленивые файлы чистятся
    assert len(list(tmp_path.iterdir())) <= 4


def _row(run, st):
    return {"campaign_run": run, "net": 1.0, "trades": 5, "max_dd": 1.0, "strategy": st, "symbol": "RI",
            "date_from": "2026-01-01", "date_to": "2026-02-01", "created_at": "2026-10-01T00:00:00", "params": {}}


def test_generic_symbol_without_schedule_has_no_buyhold(tmp_path):
    (tmp_path / "MX.json").write_text(json.dumps({"key": "MX", "rows": [[1, 1, 1, 1, 1, 1], [2, 2, 2, 2, 2, 1]]}))
    ctx = sv.BarsCtx(str(tmp_path), {"MXU6": 1.0})
    assert ctx.buyhold("MX", 0, 10, 1, True, 10, b.downsample) is None  # MX нет в SWEEP_CONTRACTS
    assert ctx.pv_at("spRIZ6d0921", 1) is None
    assert ctx.pv_at("MXU6", 1) == 1.0
