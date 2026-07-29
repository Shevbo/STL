from trader.lab.runtime import Bar

from robot_runner.explain import explain, planned_orders


def _bar(t, o, h, lo, c):
    return Bar(time=t, open=o, high=h, low=lo, close=c, volume=0)


def test_fvg_bullish_signal_confirmed():
    # bar[i-2] high=100; bar[i] low=101 (gap up), body strongly positive
    bars = [_bar(0, 99, 100, 98, 99), _bar(60, 100, 101, 99, 100),
            _bar(120, 101.0, 103.0, 101.0, 103.0)]  # body=(103-101)/103≈194 x10⁴
    d = explain("fvg", bars, {"min_frac": 5, "qty": 1}, position=0)
    assert d["want"] == 1
    assert d["features"]["gap_up"] is True
    assert "СИГНАЛ ЛОНГ" in d["waiting_for"]
    assert d["planned_orders"] == [
        {"side": "buy", "qty": 1, "price": 103.0, "reason": "вход по сигналу",
         "entry": True}]


def test_fvg_gap_without_body_confirmation():
    # gap up present but doji body -> waiting for confirmation, no signal
    bars = [_bar(0, 99, 100, 98, 99), _bar(60, 100, 101, 99, 100),
            _bar(120, 102.0, 103.0, 101.0, 102.001)]
    d = explain("fvg", bars, {"min_frac": 50, "qty": 1}, position=0)
    assert d["want"] is None
    assert d["features"]["gap_up"] is True
    assert "жду подтверждения телом" in d["waiting_for"]
    assert d["planned_orders"] == []
    assert len(d["armed"]) == 2   # hypothetical both-side entries shown


def test_fvg_warmup():
    d = explain("fvg", [_bar(0, 1, 1, 1, 1)], {}, position=0)
    assert d["ready"] is False
    assert "накопление баров" in d["waiting_for"]


def test_planned_orders_flip_closes_first():
    got = planned_orders(want=-1, position=2, price=100.0, params={"qty": 1})
    assert got[0]["side"] == "sell" and got[0]["qty"] == 2   # close long
    assert got[1]["side"] == "sell" and got[1]["qty"] == 1   # open short
    assert planned_orders(want=1, position=1, price=100.0, params={}) == []  # already long


def test_generic_explainer_runs_registered_signal():
    bars = [_bar(i * 60, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(150)]
    d = explain("shectory_2ema", bars, {"fast": 3, "slow": 5, "qty": 1}, position=0)
    assert d["ready"] is True
    assert "strategy_id" in d and d["bars_count"] == 150


# 2026-07-24, operator: «стратегия us_open_fvg не найдена» in the signal box was
# alarming and uninformative — it reads like the robot has no strategy at all.
# A standalone-module strategy has no REGISTRY entry, but its LIVE exit levels
# live in the runtime state; explain must report those instead.
def test_module_strategy_reports_live_exit_levels():
    from robot_runner.explain import explain
    state = {"entered": 1, "dir": -1, "entry": 88230.0, "sl": 88600.0, "tp": 87100.0,
             "rh": 88400.0, "rl": 88100.0}
    d = explain("us_open_fvg", [], {"qty": 5}, position=-5, avg=88230.0, state=state)
    assert "не найдена" not in d["waiting_for"]
    assert d["exit_levels"] == {"tp": 87100.0, "sl": 88600.0, "entry": 88230.0, "dir": -1}
    assert "TP 87100" in d["waiting_for"] and "SL 88600" in d["waiting_for"]


def test_module_strategy_before_entry_and_after_done():
    from robot_runner.explain import explain
    waiting = explain("us_open_fvg", [], {"range_min": 6}, 0, state={})
    assert "окно открытия" in waiting["waiting_for"] and "не найдена" not in waiting["waiting_for"]

    ranged = explain("us_open_fvg", [], {}, 0, state={"rh": 88400.0, "rl": 88100.0})
    assert ranged["range"] == {"hi": 88400.0, "lo": 88100.0}

    done = explain("us_open_fvg", [], {}, 0, state={"done": 1})
    assert "уже сделана" in done["waiting_for"]


def test_registry_strategy_unaffected_by_state_arg():
    from robot_runner.explain import explain
    d = explain("fvg", [], {}, 0, state={"sl": 1, "tp": 2})
    assert "exit_levels" not in d          # registry path untouched
