"""features.ema_series / gap / dv — числа для ЛИНИЙ на мини-графике робота.

Проверяется главное: i-е значение серии относится к i-му бару ХВОСТА (а хвост —
traded_bars, подмножество ряда робота), считано окном прогрева стратегии, и на
барах без прогрева стоит None, а не выдуманная точка.
"""
from trader.lab import indicators as I
from trader.lab.strategies.library import REGISTRY
from trader.lab.runtime import Bar

from robot_runner.explain import dv_levels, explain, gap_levels


def _bars(n=200):
    return [Bar(time=60 * i, open=87000 + i * 3, high=87000 + i * 3 + 5,
                low=87000 + i * 3 - 5, close=87000 + i * 3 + (7 if i % 2 else -7),
                volume=1)
            for i in range(n)]


def _spec_need(sid, p):
    spec = REGISTRY[sid]
    return int(spec["warmup"]({**spec["default_params"], **p}))


def test_series_aligned_to_tail_and_warmup_window():
    p = {"symbol": "SIM6", "ema1": 3, "ema2": 10, "qty": 1}
    bars = _bars(200)
    need = _spec_need("shectory_2ema", p)
    # хвост с ДЫРОЙ: пропущенные бары (синтетические минуты) наружу не едут
    tail = bars[-40:-20] + bars[-10:]
    f = explain("shectory_2ema", bars, p, position=0, tail_bars=tail)["features"]
    s = f["ema_series"]
    assert len(s["fast"]) == len(s["slow"]) == len(tail)
    # каждое значение — EMA на окне прогрева, кончающемся ИМЕННО на этом баре
    for k, n in (("fast", 3), ("slow", 10)):
        for j, tb in enumerate(tail):
            i = [b.time for b in bars].index(tb.time)
            assert s[k][j] == round(I.ema_last([b.close for b in bars][i + 1 - need:i + 1], n), 2)
    # последняя точка серии совпадает с горизонтальным уровнем features.ema
    assert (s["fast"][-1], s["slow"][-1]) == (f["ema"]["fast"], f["ema"]["slow"])


def test_none_where_warmup_missing_and_no_key_without_tail():
    p = {"symbol": "SIM6", "ema1": 3, "ema2": 10}
    bars = _bars(200)
    need = _spec_need("shectory_2ema", p)
    assert need > 5, "иначе тест ничего не проверяет"
    tail = bars[:3] + bars[-5:]          # первые бары прогрева не набрали
    s = explain("shectory_2ema", bars, p, position=0,
                tail_bars=tail)["features"]["ema_series"]
    assert s["fast"][:3] == [None, None, None]
    assert all(v is not None for v in s["fast"][3:])
    # хвоста не передали — ключа нет (панель рисует только по нашим числам)
    assert "ema_series" not in explain("shectory_2ema", bars, p, position=0)["features"]


def test_3ema_gives_middle_series():
    p = {"symbol": "SIM6", "ema1": 5, "ema2": 20, "ema3": 60}
    bars = _bars(300)
    s = explain("shectory_3ema", bars, p, position=0,
                tail_bars=bars[-20:])["features"]["ema_series"]
    assert set(s) == {"fast", "mid", "slow"} and len(s["mid"]) == 20
    assert s["fast"][-1] != s["slow"][-1]


def test_gap_and_dv_levels():
    # разножка: опора из состояния стратегии + границы, с которых добор пройдёт
    assert gap_levels({"min_gap_pts": 150}, {"gap_ref": 87000}) == {
        "ref": 87000.0, "min_pts": 150.0, "lo": 86850.0, "hi": 87150.0}
    assert gap_levels({"min_gap_pts": 0}, {"gap_ref": 87000}) == {}   # фильтр выкл
    assert gap_levels({"min_gap_pts": 150}, {}) == {}                 # опоры ещё нет
    # долина: коридор закрытий ровно по окну dv_bars
    bars = _bars(50)
    dv = dv_levels({"dv_bars": 10, "dv_range_pts": 300}, bars)
    closes = [b.close for b in bars[-10:]]
    assert dv == {"bars": 10, "range_pts": 300.0,
                  "hi": round(max(closes), 2), "lo": round(min(closes), 2)}
    assert dv_levels({"dv_bars": 100, "dv_range_pts": 300}, bars) == {}  # баров мало
    assert dv_levels({"dv_bars": 10, "dv_range_pts": 0}, bars) == {}     # фильтр выкл


def test_features_carry_levels_through_explain():
    f = explain("shectory_2ema", _bars(200),
                {"symbol": "SIM6", "ema1": 3, "ema2": 10,
                 "min_gap_pts": 150, "dv_bars": 10, "dv_range_pts": 300},
                position=0, state={"gap_ref": 87000})["features"]
    assert f["gap"]["hi"] == 87150.0 and f["dv"]["bars"] == 10
