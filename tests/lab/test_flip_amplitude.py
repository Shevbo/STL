"""Амплитудный фильтр разворота по сигналу (flip_min_pts) у macd_shectory1.

Заведён 09.09.2026 после живого инцидента: agent-macdshort-RIU6-v1 и
lxk22tsffsxiiotb8kmpsato (оба macd_shectory1, fast=57/slow=48) синхронно
перевернулись по MACD-кроссоверу на локальном пике 84370, разворот оказался
ложным — один флип −22k gross. sig_macd возвращает ±1 на КАЖДОМ баре, поэтому
любое пересечение закрывает всю позицию по рынку; ничего этот выход не
фильтровало (dv/выходные/s2i трогают только обратную ногу, не сам выход).

Здесь зеркалится формула из make_on_bar: держим позицию, если цена отошла от
средней входа меньше flip_min_pts, и ТОЛЬКО когда включён стоп — удержанный
выход без стопа это инцидент «долины» 08.08 (шорт −290 пт против, пола нет).
"""
from trader.lab.strategies.library import REGISTRY, make_on_bar


def flip_held(price: float, avg: float, params: dict, *, flip_now: bool = True) -> bool:
    """Та же арифметика, что в make_on_bar (ветка 1)."""
    sl_pct = float(params.get("sl_pct", 0) or 0) / 100.0
    tp = float(params.get("tp_atr", 0) or 0) / 10.0
    sl = tp * float(params.get("sl_frac", 0) or 0) / 100.0
    flip_min_pts = float(params.get("flip_min_pts", 0) or 0)
    flip_armed = flip_min_pts > 0 and (sl_pct > 0 or sl > 0)
    return flip_now and flip_armed and abs(price - avg) < flip_min_pts


# Живой конфиг agent-macdshort на момент инцидента: sl_frac=0, sl_pct=100, tp_atr=40.
LIVE = {"sl_frac": 0, "sl_pct": 100, "tp_atr": 40}


def test_small_move_holds_the_position():
    # Пик 84370 против средней ~83900: 470 пт хода, порог 1750 -> флип НЕ исполняется.
    assert flip_held(84370, 83900, {**LIVE, "flip_min_pts": 1750}) is True


def test_move_past_threshold_lets_the_flip_through():
    assert flip_held(85700, 83900, {**LIVE, "flip_min_pts": 1750}) is False


def test_off_by_default():
    assert flip_held(84370, 83900, {**LIVE, "flip_min_pts": 0}) is False
    assert flip_held(84370, 83900, {**LIVE}) is False


def test_not_armed_without_a_stop():
    """Инвариант 08.08: удержать выход можно только когда есть чем ограничить убыток.
    Оба стопа выключены -> фильтр не армится, флип идёт как раньше."""
    no_stop = {"sl_frac": 0, "sl_pct": 0, "tp_atr": 40, "flip_min_pts": 1750}
    assert flip_held(84370, 83900, no_stop) is False
    # sl_frac (доля тейка) тоже армит — но только при tp_atr>0, иначе sl=0.
    assert flip_held(84370, 83900, {"sl_pct": 0, "sl_frac": 50, "tp_atr": 40,
                                    "flip_min_pts": 1750}) is True
    assert flip_held(84370, 83900, {"sl_pct": 0, "sl_frac": 50, "tp_atr": 0,
                                    "flip_min_pts": 1750}) is False


def test_no_flip_no_hold():
    # Сигнал не переворачивался -> держать нечего, ветка не наша.
    assert flip_held(84370, 83900, {**LIVE, "flip_min_pts": 1750}, flip_now=False) is False


def test_axis_in_schema_of_shectory1_only():
    keys = {p["key"] for p in REGISTRY["macd_shectory1"]["params_schema"]}
    assert "flip_min_pts" in keys
    assert REGISTRY["macd_shectory1"]["default_params"]["flip_min_pts"] == 0
    # Ключ живёт в SHECTORY1_PARAMS, а не в общем AVG_PARAMS: у прочего реестра
    # (перебран миллионами строк лидерборда) поведение по умолчанию не трогаем.
    assert "flip_min_pts" not in {p["key"] for p in REGISTRY["macd_cross"]["params_schema"]}
    assert callable(make_on_bar("macd_shectory1"))
