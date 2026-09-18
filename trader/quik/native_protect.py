"""Защита позиции ПОСЛЕ входа силами самого QUIK (нативная стоп-заявка).

Зачем: движок умных заявок живёт в STL и стережёт уровни раз в секунду. Пока STL
жив, это работает; упал STL или оборвалась связь с агентом - позиция остаётся без
стопа и без тейка. Терминал же держит свою стоп-заявку сам, независимо от нас: она
переживает падение STL, обрыв канала и перезапуск агента.

Поэтому после входа по умной заявке защита передаётся под охрану терминала, а наши
собственные защитные заявки снимаются (см. quik_smart_orders: передача под охрану).

Что чем выражается (виды проверены сериями S2-S4 17.09.2026, execution-module.md 3b/3c):
  только стоп                 -> SIMPLE_STOP_ORDER
  только тейк (фикс)          -> TAKE_PROFIT_STOP_ORDER с откатом в ОДИН шаг цены
  только тейк следящий        -> TAKE_PROFIT_STOP_ORDER с откатом tp_trail
  стоп + любой из тейков      -> TAKE_PROFIT_AND_STOP_LIMIT_ORDER (одна запись, две ноги)
  подтягивающая (trail_after) -> нативного аналога у QUIK НЕТ, остаётся в STL

Модуль чистый: строит поля транзакции, никакого ввода-вывода.
"""

from __future__ import annotations

from .smart_orders import SmartOrder, quantize

# Запас лимитной цены ребёнка от уровня срабатывания, в шагах цены. Нативная
# стоп-заявка порождает ЛИМИТНУЮ заявку: без запаса она не исполнится на быстром
# движении и позиция останется незакрытой (ровно то, от чего стоп и ставится).
CUSHION_STEPS = 2
# Фиксированный тейк в терминале выражается тейк-профитом с минимальным откатом:
# отката 0 QUIK не принимает. Один шаг цены = «практически фиксированный».
_MIN_OFFSET_STEPS = 1


def _level(price: float, step: float, side: str) -> float:
    """Уровень на сетке шага цены. side - сторона ЗАЩИТНОЙ заявки; округляем всегда
    в сторону, невыгодную позиции, чтобы уровень не оказался недостижимым."""
    return quantize(price, step, side) if step > 0 else price


def _fmt(v: float) -> str:
    return f"{v:.10g}"


def build_native_protection(parent: SmartOrder, entry_price: float,
                            step: float) -> dict | None:
    """Поля нативной стоп-заявки, охраняющей вход по `parent` от цены `entry_price`.

    Возвращает {"side", "quantity", "fields", "kinds"} или None, если защита в
    терминале невыразима (нет блоков после сделки, стоит подтягивающая, нет цены).
    `kinds` - какие наши защитные заявки эта одна запись QUIK заменяет.
    """
    if entry_price <= 0 or parent.qty <= 0:
        return None
    if parent.trail_after > 0:
        return None                      # подтягивающая: у QUIK такого вида нет
    sl, tp, trail = parent.sl_offset, parent.tp_offset, parent.tp_trail
    if sl <= 0 and tp <= 0 and parent.sl_price <= 0 and parent.tp_price <= 0:
        return None

    long_side = parent.side == "buy"
    exit_side = "sell" if long_side else "buy"
    sign = 1 if long_side else -1        # «в пользу позиции»
    cushion = (step or 0.0) * CUSHION_STEPS
    fields: dict[str, str] = {"EXPIRY_DATE": "TODAY"}
    kinds: list[str] = []

    # Блок, заданный ЦЕНОЙ уровня, идёт в терминал этим же уровнем.
    take_level = parent.tp_price if parent.tp_price > 0 else 0.0
    stop_level = parent.sl_price if parent.sl_price > 0 else 0.0
    if take_level and (take_level <= entry_price if long_side else take_level >= entry_price):
        take_level = 0.0                  # вход уже за уровнем: тейк не ставим
    if stop_level and (stop_level >= entry_price if long_side else stop_level <= entry_price):
        stop_level = 0.0
    tp = tp or take_level
    sl = sl or stop_level
    if tp <= 0 and sl <= 0:
        return None
    if tp > 0:
        raw_take = take_level or (entry_price + sign * tp)
        take = _level(raw_take, step, "buy" if long_side else "sell")
        offset = trail if trail > 0 else (step or 1.0) * _MIN_OFFSET_STEPS
        fields.update({"STOPPRICE": _fmt(take), "OFFSET": _fmt(offset),
                       "OFFSET_UNITS": "PRICE_UNITS", "SPREAD": _fmt(cushion),
                       "SPREAD_UNITS": "PRICE_UNITS"})
        kinds.append("trail_tp" if trail > 0 else "tp")
    if sl > 0:
        stop = _level(stop_level or (entry_price - sign * sl), step, exit_side)
        limit = stop - sign * cushion    # лимит ребёнка ХУЖЕ уровня, иначе не нальётся
        key = "STOPPRICE2" if tp > 0 else "STOPPRICE"
        fields.update({key: _fmt(stop), "PRICE": _fmt(limit)})
        kinds.append("sl")

    if tp > 0 and sl > 0:
        fields["STOP_ORDER_KIND"] = "TAKE_PROFIT_AND_STOP_LIMIT_ORDER"
    elif tp > 0:
        fields["STOP_ORDER_KIND"] = "TAKE_PROFIT_STOP_ORDER"
    else:
        fields["STOP_ORDER_KIND"] = "SIMPLE_STOP_ORDER"
    return {"side": exit_side, "quantity": int(parent.qty), "fields": fields,
            "kinds": kinds}
