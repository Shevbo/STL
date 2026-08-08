"""«Долина смерти» в explain — зеркало make_on_bar:

1. dv_block: карточка честно говорит «входы закрыты до пробоя», и метит ВСЕ
   entry-заявки (в отличие от разножки долина гейтит и вход с нуля).
2. Сигнальную серию долина НЕ трогает (семантика v3 08.08.2026): сигнал всегда
   по сырым барам — как и в движке, где заморозка лишала робота выхода.
"""
from trader.lab.runtime import Bar
from trader.lab.strategies.library import REGISTRY, register

from robot_runner.explain import dv_block, explain


def _bars(closes, t0=60):
    return [Bar(time=t0 + i * 60, open=c, high=c, low=c, close=c, volume=1)
            for i, c in enumerate(closes)]


_SEEN = {"last": None}
if "_dvexp" not in REGISTRY:
    register("_dvexp", "t", "t", [],
             lambda bars, p: _SEEN.__setitem__("last", bars[-1].close),
             lambda p: 1)


def test_dv_block_message_in_tight_corridor():
    msg = dv_block({"dv_bars": 3, "dv_range_pts": 100}, _bars([87000, 87010, 87020]))
    assert "долина" in msg and "входы закрыты" in msg


def test_dv_block_silent_when_off_or_wide():
    wide = _bars([87000, 87400, 87020])
    assert dv_block({"dv_bars": 3, "dv_range_pts": 100}, wide) == ""
    tight = _bars([87000, 87010, 87020])
    assert dv_block({}, tight) == ""
    assert dv_block({"dv_bars": 3, "dv_range_pts": 100}, tight[:2]) == ""


def test_explain_marks_all_entries_and_signal_sees_raw_series():
    p = {"symbol": "SIM6", "qty": 1, "dv_bars": 3, "dv_range_pts": 100}
    bars = _bars([87200, 87000, 87002, 87004])   # хвост 87000..87004 — долина
    d = explain("_dvexp", bars, p, position=0)
    assert "долина" in d.get("entry_blocked", "")
    # сигнал считается по СЫРОЙ серии (долина гейтит только входы)
    assert _SEEN["last"] == 87004
    for o in d["planned_orders"]:
        if o.get("entry"):
            assert o.get("blocked")
