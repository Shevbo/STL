from robot_runner.bars import BarBuilder
from robot_runner.explain import explain, management_levels


def _bars(n=20, base=89000.0):
    b = BarBuilder()
    t0 = 1_783_230_000_000
    for i in range(n + 1):
        # gentle 20-pt range per minute -> nonzero ATR
        b.on_tick(t0 + i * 60_000, base + (i % 3) * 20)
    return b.bars()


PARAMS = {"symbol": "RIU6", "qty": 1, "avg_max": 1, "tp_atr": 60,
          "min_frac": 12, "avg_atr_n": 5, "avg_step_atr": 24}


def test_tp_level_present_when_long():
    bars = _bars()
    levels = management_levels(bars, PARAMS, position=1, avg=89060.0)
    assert len(levels) == 1                      # avg_max=1 -> no averaging level
    tp = levels[0]
    assert tp["side"] == "sell" and tp["qty"] == 1 and tp["level"] is True
    assert tp["price"] > 89060.0                  # long TP above avg
    assert "тейк-профит" in tp["reason"]


def test_tp_level_short_below_avg():
    bars = _bars()
    lv = management_levels(bars, PARAMS, position=-1, avg=89060.0)
    assert lv and lv[0]["side"] == "buy" and lv[0]["price"] < 89060.0


def test_averaging_level_when_enabled():
    p = {**PARAMS, "avg_max": 3, "avg_step_atr": 24}
    lv = management_levels(_bars(), p, position=1, avg=89060.0)
    sides = {x["side"] for x in lv}
    assert sides == {"sell", "buy"}               # TP up + averaging add below


def test_flat_position_no_levels():
    assert management_levels(_bars(), PARAMS, position=0, avg=0.0) == []


def test_explain_merges_levels_into_planned():
    d = explain("fvg", _bars(), PARAMS, position=1, avg=89060.0)
    assert any(o.get("level") for o in d["planned_orders"])
