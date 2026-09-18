"""Разворот по сигналу, разделённый знаком результата (flip_hold_win).

Заведено 18.09.2026 по вопросу оператора «а что если выходить строго по стопу, а
не по перевороту». Перебор на RI 2022-2026 (scripts/exit_rule_sweep.py) показал:
снятие ВСЕХ флипов улучшает итог парно (t=+2.40 по 17 контрактам), но обе
асимметричные версии хуже полного снятия — держать прибыльную −892k пт, держать
убыточную −1.83 млн пт против +225k. Ось поэтому остаётся выключенной; тест
охраняет её поведение и главный инвариант: без стопа не армится.

Зеркалит арифметику из make_on_bar (ветка 1), как test_flip_amplitude.
"""
from trader.lab.strategies.library import REGISTRY, make_on_bar


def flip_held(price: float, avg: float, params: dict, *, cur_dir: int = 1,
              flip_now: bool = True) -> bool:
    sl_pct = float(params.get("sl_pct", 0) or 0) / 100.0
    tp = float(params.get("tp_atr", 0) or 0) / 10.0
    sl = tp * float(params.get("sl_frac", 0) or 0) / 100.0
    flip_min_pts = float(params.get("flip_min_pts", 0) or 0)
    armed = flip_min_pts > 0 and (sl_pct > 0 or sl > 0)
    held = flip_now and armed and abs(price - avg) < flip_min_pts
    hold_win = int(params.get("flip_hold_win", 0) or 0)
    if flip_now and not held and hold_win and avg > 0 and (sl_pct > 0 or sl > 0):
        won = (price - avg) * cur_dir > 0
        held = won if hold_win == 1 else not won
    return held


LIVE = {"sl_frac": 0, "sl_pct": 100, "tp_atr": 80}      # боевая спека lxk22


def test_mode1_holds_a_winning_position():
    """1 = прибыльную держим (выход по тейку), убыточную закрываем как раньше."""
    p = {**LIVE, "flip_hold_win": 1}
    assert flip_held(84500, 83900, p) is True                    # лонг в прибыли
    assert flip_held(83500, 83900, p) is False                   # лонг в убытке
    assert flip_held(83500, 83900, p, cur_dir=-1) is True        # шорт в прибыли
    assert flip_held(84500, 83900, p, cur_dir=-1) is False


def test_mode2_is_the_mirror():
    p = {**LIVE, "flip_hold_win": 2}
    assert flip_held(83500, 83900, p) is True                    # держим убыток
    assert flip_held(84500, 83900, p) is False


def test_off_by_default():
    assert flip_held(84500, 83900, LIVE) is False
    assert flip_held(84500, 83900, {**LIVE, "flip_hold_win": 0}) is False
    assert REGISTRY["macd_shectory1"]["default_params"]["flip_hold_win"] == 0


def test_not_armed_without_a_stop():
    """Инвариант 08.08.2026: удержать выход можно только когда есть пол по убытку."""
    no_stop = {"sl_frac": 0, "sl_pct": 0, "tp_atr": 80, "flip_hold_win": 1}
    assert flip_held(84500, 83900, no_stop) is False
    assert flip_held(84500, 83900, {"sl_pct": 0, "sl_frac": 50, "tp_atr": 80,
                                    "flip_hold_win": 1}) is True


def test_no_avg_no_hold():
    """Бумажная позиция без цены входа (avg=0): знак результата неизвестен."""
    assert flip_held(84500, 0, {**LIVE, "flip_hold_win": 1}) is False


def test_amplitude_gate_still_wins_when_it_fires():
    """flip_min_pts и flip_hold_win независимы: держит тот, у кого условие сработало."""
    p = {**LIVE, "flip_min_pts": 1750, "flip_hold_win": 1}
    assert flip_held(83500, 83900, p) is True                    # держит амплитуда
    assert flip_held(80000, 83900, p) is False                   # ушло далеко и в минус


def test_axis_in_schema_of_shectory1_only():
    keys = {p["key"] for p in REGISTRY["macd_shectory1"]["params_schema"]}
    assert "flip_hold_win" in keys
    assert "flip_hold_win" not in {p["key"] for p in REGISTRY["macd_cross"]["params_schema"]}
    assert callable(make_on_bar("macd_shectory1"))
