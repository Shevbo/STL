"""Правка параметров робота НАКЛАДЫВАЕТСЯ на текущие, а не заменяет их целиком.

Инцидент 05.08.2026, РЕАЛЬНЫЙ робот lxk22tsffsxiiotb8kmpsato. Оператор поменял на
стенде qty 2->1 и avg_max 34->17. Форма строится по params_schema стратегии, а
инфраструктурные флаги в схему не входят по замыслу — и вместе с правкой молча
слетели `exit_only=true` и `allow_short=0`. Робот, стоявший «только на выход» и без
шортов, снова получил право открывать позиции в обе стороны и успел набрать контракт,
пока это не заметили.

Правило: ключ, которого НЕТ в присланном наборе, обязан сохраниться.
"""
import json


def merge_params(current: str | dict | None, sent: str | None) -> dict:
    """Та же склейка, что в relay_robot_params (api/quik_robots.py)."""
    try:
        base = json.loads(current) if isinstance(current, str) else dict(current or {})
    except ValueError:
        base = {}
    try:
        incoming = json.loads(sent or "{}")
    except ValueError:
        incoming = {}
    if not isinstance(base, dict) or not isinstance(incoming, dict) or not base:
        return incoming if isinstance(incoming, dict) else {}
    return {**base, **incoming}


LIVE = {"qty": 2, "avg_max": 34, "fast": 57, "slow": 48, "signal": 10,
        "exit_only": True, "allow_short": 0, "allow_long": 1, "symbol": "RIU6"}
# Форма прислала только поля схемы стратегии: exit_only в неё не входит.
FORM = json.dumps({"qty": 1, "avg_max": 17, "fast": 57, "slow": 48, "signal": 10,
                   "allow_short": 0, "allow_long": 1, "symbol": "RIU6"})


def test_operator_change_applies():
    out = merge_params(LIVE, FORM)
    assert out["qty"] == 1 and out["avg_max"] == 17


def test_exit_only_survives_a_params_edit():
    """Ровно то, что стоило контракта на реале."""
    assert merge_params(LIVE, FORM)["exit_only"] is True


def test_missing_key_is_kept_not_reset():
    out = merge_params(LIVE, json.dumps({"qty": 1}))
    assert out["allow_short"] == 0
    assert out["avg_max"] == 34          # не упомянут — значит не тронут


def test_explicit_value_wins_over_current():
    out = merge_params(LIVE, json.dumps({"allow_short": 1}))
    assert out["allow_short"] == 1       # менять флаг ЯВНО по-прежнему можно


def test_zero_is_a_value_not_an_absence():
    out = merge_params({"sl_frac": 50}, json.dumps({"sl_frac": 0}))
    assert out["sl_frac"] == 0


def test_broken_json_never_wipes_the_robot():
    assert merge_params(LIVE, "{не json")["exit_only"] is True


def test_empty_mirror_passes_the_form_through():
    """Зеркала нет — накладывать не на что, отдаём присланное как есть."""
    assert merge_params({}, json.dumps({"qty": 1})) == {"qty": 1}
