"""us_open_fvg: причина входа в журнале и проскальзывание на фиксированном стопе.

Просьба real-trade 25.09.2026. По живому журналу за два месяца (42 круга,
net −23 043 ₽) нельзя было сказать, сколько убыточных кругов дал ложный пробой:
лог входа не писал, каким сигналом вошли. Плюс в инвертированной сделке стоп
выходит 4-8 тиков, а движок исполнял фиксированный стоп РОВНО по его цене —
для такого стопа это не поправка, а весь результат.
"""
import inspect

from trader.lab.strategies import us_open_fvg as S


def test_entry_sites_pass_a_reason():
    src = inspect.getsource(S.on_bar)
    assert 'enter(1, cur.close, "fvg")' in src and 'enter(-1, cur.close, "fvg")' in src
    assert 'enter(1, cur.close, "retest")' in src and 'enter(-1, cur.close, "retest")' in src


def test_reason_reaches_the_log_and_the_counter():
    src = inspect.getsource(S.on_bar)
    assert 'вход={why}' in src, "причина входа не попадает в журнал"
    assert 'stl.set_state("why_" + why' in src, "причина входа не считается"


def test_fixed_stop_has_a_slippage_model():
    src = inspect.getsource(S.on_bar)
    assert "sl - stop_slip * height" in src and "sl + stop_slip * height" in src
    assert 'params.get("stop_slip", 0)' in src


def test_stop_is_checked_before_take():
    """Бар накрывает и стоп, и тейк: стоп первым — как у раннера."""
    src = inspect.getsource(S.on_bar)
    i_sl = src.index("if cur.low <= sl:")
    i_tp = src.index("elif cur.high >= tp:")
    assert i_sl < i_tp


def test_slippage_is_in_the_schema():
    keys = {p["key"] for p in S.meta()["params_schema"]} if hasattr(S, "meta") else set()
    if not keys:
        import re
        keys = set(re.findall(r'"key": "(\w+)"', inspect.getsource(S)))
    assert "stop_slip" in keys
