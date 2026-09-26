"""features.ema в signal_json — текущие EMA робота для линий на мини-графике.

Главное, что проверяется: значение посчитано на ОКНЕ ПРОГРЕВА стратегии, а не
на видимом хвосте. Панель, посчитав EMA по сорока видимым барам, нарисовала бы
линию, похожую на EMA робота и ею не являющуюся — ровно та подмена, из-за
которой замороженный знак MACD шесть суток выдавал 778 лонгов и ноль шортов.
"""
from trader.lab import indicators as I
from trader.lab.strategies.library import REGISTRY
from trader.lab.runtime import Bar

from robot_runner.explain import explain


def _bars(n=200):
    # пила с наклоном: быстрая и медленная EMA гарантированно разные числа
    return [Bar(time=60 * i, open=87000 + i * 3, high=87000 + i * 3 + 5,
                low=87000 + i * 3 - 5, close=87000 + i * 3 + (7 if i % 2 else -7),
                volume=1)
            for i in range(n)]


def test_2ema_values_match_strategy_warmup_window():
    p = {"symbol": "SIM6", "ema1": 3, "ema2": 10, "qty": 1}
    bars = _bars(200)
    ema = explain("shectory_2ema", bars, p, position=0)["features"]["ema"]
    assert (ema["fast_n"], ema["slow_n"]) == (3, 10)
    need = REGISTRY["shectory_2ema"]["warmup"]({**REGISTRY["shectory_2ema"]["default_params"], **p})
    closes = [b.close for b in bars[-need:]]
    assert ema["fast"] == round(I.ema_last(closes, 3), 2)
    assert ema["slow"] == round(I.ema_last(closes, 10), 2)
    # окно = прогрев стратегии, а не весь хвост баров
    assert len(closes) == need < len(bars)


def test_macd_cross_reports_fast_slow():
    ema = explain("macd_cross", _bars(400), {"symbol": "SIM6", "fast": 12, "slow": 26},
                  position=0)["features"]["ema"]
    assert (ema["fast_n"], ema["slow_n"]) == (12, 26)
    assert ema["fast"] > 0 and ema["slow"] > 0


def test_3ema_adds_middle_line():
    ema = explain("shectory_3ema", _bars(300),
                  {"symbol": "SIM6", "ema1": 5, "ema2": 20, "ema3": 60},
                  position=0)["features"]["ema"]
    assert (ema["fast_n"], ema["mid_n"], ema["slow_n"]) == (5, 20, 60)


def test_no_key_without_crossing_or_when_degenerate_or_cold():
    # стратегия без пересечения EMA — ключа нет, панель ничего не рисует
    d = explain("bollinger_mr", _bars(200), {"symbol": "SIM6"}, position=0)
    assert "ema" not in d.get("features", {})
    # совпавшие периоды = вырождение, сигнала нет — значит и линий нет
    d = explain("shectory_2ema", _bars(200), {"symbol": "SIM6", "ema1": 10, "ema2": 10},
                position=0)
    assert "ema" not in d.get("features", {})
    # прогрева не хватает — молчим, а не считаем по чему попало
    d = explain("shectory_2ema", _bars(20), {"symbol": "SIM6", "ema1": 3, "ema2": 10},
                position=0)
    assert "ema" not in d.get("features", {})
