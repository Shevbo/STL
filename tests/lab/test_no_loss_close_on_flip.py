"""Убыток НЕ закрывается по развороту сигнала — поведение ПО УМОЛЧАНИЮ.

Распоряжение оператора 21.09.2026. MACD-кроссовер приходит ПОСЛЕ того, как ход
выдохся, то есть у локального экстремума: робот с полной лестницей закрывался ровно
в точке максимального убытка эпизода, а обратную ногу открывал в сторону уже
законченного движения (21.09: покупка вершины 84640, продажа дна 84320, пять
эпизодов, −38 389 руб за день).

Тест зеркалит арифметику из make_on_bar — так же, как test_flip_amplitude и
test_flip_hold_win: поведение движка на настоящих минутках проверяется парой
прогонов на i9 (запрет включён / flip_close_loss=1), синтетика для этого не годится.

ЦЕНА РЕШЕНИЯ ИЗВЕСТНА: та же ось, проверенная 18.09 как flip_hold_win=2 на RI
2022-2026, дала −1.83 млн пт против −1.49 млн у прежнего поведения. Оператор
распорядился, зная это.
"""
from trader.lab.strategies.library import REGISTRY


def held(price: float, avg: float, cur_dir: int, params: dict) -> bool:
    """Держим ли убыточную позицию на развороте (ветка 1 make_on_bar)."""
    sl_pct = float(params.get("sl_pct", 0) or 0) / 100.0
    tp = float(params.get("tp_atr", 0) or 0) / 10.0
    sl = tp * float(params.get("sl_frac", 0) or 0) / 100.0
    close_loss = int(params.get("flip_close_loss", 0) or 0)
    return (not close_loss and avg > 0 and (sl_pct > 0 or sl > 0)
            and (price - avg) * cur_dir < 0)


LIVE = {"sl_pct": 100, "sl_frac": 0, "tp_atr": 80}     # боевая спека lxk22


def test_loss_is_held_by_default():
    assert held(84300, 84600, 1, LIVE) is True         # лонг в убытке
    assert held(84900, 84600, -1, LIVE) is True        # шорт в убытке


def test_profit_still_closes():
    """Запрет только про убыток: прибыльный разворот закрывается как раньше."""
    assert held(84900, 84600, 1, LIVE) is False
    assert held(84300, 84600, -1, LIVE) is False


def test_flag_restores_old_behaviour():
    assert held(84300, 84600, 1, {**LIVE, "flip_close_loss": 1}) is False


def test_not_armed_without_a_stop():
    """Держать убыток без пола нельзя — инцидент «долины» 08.08.2026."""
    assert held(84300, 84600, 1, {"sl_pct": 0, "sl_frac": 0, "tp_atr": 80}) is False
    # sl_frac армит, но только при включённом тейке (иначе sl = 0)
    assert held(84300, 84600, 1, {"sl_pct": 0, "sl_frac": 50, "tp_atr": 80}) is True
    assert held(84300, 84600, 1, {"sl_pct": 0, "sl_frac": 50, "tp_atr": 0}) is False


def test_paper_position_without_price_is_not_held():
    """avg=0 у бумажной позиции: знак результата неизвестен, ведём себя как раньше."""
    assert held(84300, 0, 1, LIVE) is False


def test_flag_in_schema():
    keys = {p["key"] for p in REGISTRY["macd_shectory1"]["params_schema"]}
    assert "flip_close_loss" in keys
    assert REGISTRY["macd_shectory1"]["default_params"]["flip_close_loss"] == 0
