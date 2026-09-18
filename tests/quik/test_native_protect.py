"""Нативная защита позиции силами QUIK: какие поля транзакции строятся из блоков
после сделки. Виды и их поведение проверены сериями S2-S4 (execution-module.md 3c)."""

from trader.quik.native_protect import build_native_protection
from trader.quik.smart_orders import SmartOrder, new_id

STEP = 10.0


def parent(**kw):
    base = dict(so_id=new_id(), kind="trail_tp", code="RIZ6", side="buy", qty=2,
                trail_offset=50, created_ms=0)
    base.update(kw)
    return SmartOrder(**base)


def test_stop_only_is_a_simple_stop_order():
    p = build_native_protection(parent(sl_offset=300), 87000, STEP)
    assert p["side"] == "sell" and p["quantity"] == 2 and p["kinds"] == ["sl"]
    f = p["fields"]
    assert f["STOP_ORDER_KIND"] == "SIMPLE_STOP_ORDER"
    assert f["STOPPRICE"] == "86700"          # вход минус стоп
    assert f["PRICE"] == "86680"              # лимит ребёнка ниже уровня на запас
    assert f["EXPIRY_DATE"] == "TODAY"


def test_fixed_take_is_a_take_profit_with_one_step_offset():
    f = build_native_protection(parent(tp_offset=500), 87000, STEP)["fields"]
    assert f["STOP_ORDER_KIND"] == "TAKE_PROFIT_STOP_ORDER"
    assert f["STOPPRICE"] == "87500" and f["OFFSET"] == "10"   # откат в один шаг
    assert f["SPREAD"] == "20" and f["OFFSET_UNITS"] == f["SPREAD_UNITS"] == "PRICE_UNITS"


def test_trailing_take_carries_the_retrace_as_offset():
    p = build_native_protection(parent(tp_offset=500, tp_trail=100), 87000, STEP)
    assert p["kinds"] == ["trail_tp"]
    f = p["fields"]
    assert f["STOP_ORDER_KIND"] == "TAKE_PROFIT_STOP_ORDER"
    assert f["STOPPRICE"] == "87500" and f["OFFSET"] == "100"


def test_stop_and_trailing_take_are_one_linked_record():
    p = build_native_protection(parent(sl_offset=300, tp_offset=500, tp_trail=100), 87000, STEP)
    assert sorted(p["kinds"]) == ["sl", "trail_tp"]
    f = p["fields"]
    assert f["STOP_ORDER_KIND"] == "TAKE_PROFIT_AND_STOP_LIMIT_ORDER"
    assert f["STOPPRICE"] == "87500"          # нога тейка
    assert f["STOPPRICE2"] == "86700"         # нога стопа
    assert f["PRICE"] == "86680" and f["OFFSET"] == "100"


def test_short_entry_is_mirrored():
    f = build_native_protection(parent(side="sell", sl_offset=300, tp_offset=500), 87000, STEP)["fields"]
    assert f["STOPPRICE"] == "86500" and f["STOPPRICE2"] == "87300" and f["PRICE"] == "87320"


def test_levels_land_on_the_price_grid_away_from_the_position():
    f = build_native_protection(parent(sl_offset=305, tp_offset=505), 87002, STEP)["fields"]
    assert f["STOPPRICE"] == "87510"          # тейк дальше: вверх по сетке
    assert f["STOPPRICE2"] == "86690"         # стоп дальше: вниз по сетке


def test_nothing_to_guard_or_no_native_equivalent():
    assert build_native_protection(parent(), 87000, STEP) is None          # блоков нет
    assert build_native_protection(parent(sl_offset=300), 0, STEP) is None  # нет цены входа
    # Подтягивающая ведёт стоп за ценой: у QUIK такого вида нет, остаётся в STL.
    assert build_native_protection(parent(trail_after=200), 87000, STEP) is None


def test_levels_go_to_the_terminal_as_they_are():
    p = build_native_protection(parent(sl_price=86700, tp_price=87500, tp_trail=100), 87000, STEP)
    f = p["fields"]
    assert f["STOP_ORDER_KIND"] == "TAKE_PROFIT_AND_STOP_LIMIT_ORDER"
    assert f["STOPPRICE"] == "87500" and f["STOPPRICE2"] == "86700" and f["OFFSET"] == "100"


def test_level_behind_the_entry_is_not_sent():
    # Купили выше уровня тейка: тейк не ставим (решение оператора), стоп ставим.
    p = build_native_protection(parent(sl_price=86700, tp_price=86900), 87000, STEP)
    assert p["kinds"] == ["sl"] and p["fields"]["STOP_ORDER_KIND"] == "SIMPLE_STOP_ORDER"
    assert build_native_protection(parent(tp_price=86900), 87000, STEP) is None


# ---- одиночная защита оператора на уже открытую позицию (18.09.2026) ----

def lone(**kw):
    base = dict(so_id=new_id(), kind="sl", code="RIZ6", side="buy", qty=10,
                trigger_price=84510, created_ms=0)
    base.update(kw)
    return SmartOrder(**base)


def test_manual_stop_on_a_short_goes_to_the_terminal():
    from trader.quik.native_protect import build_native_standalone
    p = build_native_standalone(lone(), STEP, position=-13)
    assert p["side"] == "buy" and p["quantity"] == 10
    assert p["fields"]["STOP_ORDER_KIND"] == "SIMPLE_STOP_ORDER"
    assert p["fields"]["STOPPRICE"] == "84510" and p["fields"]["PRICE"] == "84530"


def test_manual_take_uses_take_profit_not_a_plain_stop():
    from trader.quik.native_protect import build_native_standalone
    # Тейк лонга ждёт цену ВЫШЕ, а SIMPLE_STOP на продажу ждёт цену НИЖЕ.
    f = build_native_standalone(lone(kind="tp", side="sell", trigger_price=85500), STEP, 10)["fields"]
    assert f["STOP_ORDER_KIND"] == "TAKE_PROFIT_STOP_ORDER" and f["OFFSET"] == "10"
    f2 = build_native_standalone(lone(kind="trail_tp", side="sell", trigger_price=85500,
                                      trail_offset=150), STEP, 10)["fields"]
    assert f2["STOP_ORDER_KIND"] == "TAKE_PROFIT_STOP_ORDER" and f2["OFFSET"] == "150"


def test_only_position_closing_orders_are_handed_over():
    from trader.quik.native_protect import build_native_standalone
    assert build_native_standalone(lone(), STEP, position=0) is None       # позиции нет
    assert build_native_standalone(lone(), STEP, position=13) is None      # это ВХОД, не защита
    assert build_native_standalone(lone(qty=20), STEP, position=-13) is None   # больше позиции
    # Заявка с блоками после сделки - это вход: терминал детей к стопу не прицепит.
    assert build_native_standalone(lone(sl_offset=300), STEP, position=-13) is None
    # Подтягивающая и «по исполнению» нативного вида не имеют.
    assert build_native_standalone(lone(kind="trail_sl", trail_offset=100, trigger_price=0),
                                   STEP, position=-13) is None
    # Следящая без уровня активации: вести её в терминале нечем.
    assert build_native_standalone(lone(kind="trail_tp", trail_offset=100, trigger_price=0),
                                   STEP, position=-13) is None
