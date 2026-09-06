"""
Модель торговых издержек FORTS: сколько стоит сделка робота у биржи и у брокера.

Здесь живёт ответ на вопрос, почему комиссия в бэктесте считается иначе, чем в живой
торговле лимитными заявками: перебор считает себя ТЕЙКЕРОМ и платит биржевой сбор с
каждого филла, а лимитка, простоявшая в стакане, исполняется МЕЙКЕРОМ и биржевого сбора
не платит вовсе — остаётся только брокерская часть. Отсюда же две поправки, без которых
модель врёт: круг, открытый и закрытый внутри одной сессии, платит бирже половину
(скальперская скидка), а торги в субботу и воскресенье стоят вдвое дороже будних.
Слова, которыми эту тему называют в работе: комиссия, сбор, издержки, стоимость оборота,
тейкер и мейкер, скальперская сделка, ГО и его множитель у брокера.

FORTS commission model — broker (Finam) + MOEX exchange fee.

Two execution modes:
  • TAKER (market / crossing the spread): pays MOEX exchange fee + broker fee.
    Used in BACKTESTS (conservative — assume we hit the book).
  • MAKER (limit resting in the book): MOEX fee = 0, only the broker fee.
    Used in LIVE trading (robots post limit orders near the spread).

MOEX fee = group_rate% × contract_notional, where notional = price × point_value.
Rates (taker, безадресные) from moex.com/s402, by instrument group. Maker = 0.
Broker = Finam base tariff, flat per contract.

Refs: https://www.moex.com/s402 (MOEX FORTS fees), Finam base tariff 0.45 ₽/contract.
"""
from __future__ import annotations

# Finam base-tariff broker fee, rubles per contract (per fill).
BROKER_FEE_PER_CONTRACT = 0.45

# СКАЛЬПЕРСКАЯ СКИДКА БИРЖИ. Позиция, открытая и закрытая ВНУТРИ одной сессии,
# платит бирже половину: ISS публикует обе ставки, и SCALPERFEE ровно вдвое меньше
# BUYSELLFEE на каждом проверенном контракте (RIU6 9.50/4.75, SiU6 3.97/1.99,
# BRV6 10.94/5.47, GZU6 1.81/0.91, MXU6 14.92/7.46 — 06.09.2026).
#
# Скидка касается ТОЛЬКО биржевого сбора; брокерская часть берётся с каждого филла
# целиком. Без неё модель завышала издержки вдвое на оборотистых днях — окно
# real-trade сверило факт QUIK против модели за две недели на RIU6 и получило долю
# 0.44-0.61 в активные дни против 1.07 в тихий, когда позиция ночевала. Ошибка не
# нейтральна: она наказывает именно высокочастотные стратегии, то есть искажает
# СРАВНЕНИЕ, а не только абсолютный итог.
SCALPER_DISCOUNT = 0.5

# ВЫХОДНЫЕ ДОРОЖЕ ВДВОЕ. На субботних и воскресных торгах FORTS и биржевой сбор, и
# брокерская часть удваиваются, и вместе с ними удваивается ГО (риск переноса через
# два нерабочих дня). Источник — оператор, 06.09.2026; в ISS отдельной выходной
# ставки нет, там published BUYSELLFEE будних торгов.
#
# Для стратегии это не мелочь: у нас M1-реестр, а FORTS торгует и в выходные, то
# есть примерно два дня из семи считались вдвое дешевле, чем стоят. Ошибка снова
# НЕ нейтральна — она сильнее бьёт по тем конфигам, что крутят объём по выходным.
WEEKEND_FEE_MULTIPLIER = 2.0


def is_weekend(ts: float | int | None) -> bool:
    """Суббота или воскресенье по МОСКОВСКОМУ календарю.

    Бары бэктеста проштампованы московской стенкой в UTC, поэтому день недели берётся
    из метки как есть. Для истинно-UTC источника (раннер на агенте) вызывающий обязан
    прибавить смещение сам — здесь его взять неоткуда.
    """
    if not ts:
        return False
    import datetime as _dt
    return _dt.datetime.fromtimestamp(float(ts), _dt.UTC).weekday() >= 5

# MOEX taker fee as a FRACTION of contract notional, by instrument group
# (exchange + clearing combined). Maker pays 0.
MOEX_TAKER_RATE = {
    "fx":       0.0000462,   # currency futures (Si, Eu, CNY, USDRUBF...)
    "index":    0.0000660,   # index futures (RTS/RI, MIX/MX)
    "stock":    0.0001980,   # single-stock futures (GAZR, SBRF, ...)
    "commodity":0.0001320,   # commodity (BR, GOLD/GD, SILV, NG...)
    "rate":     0.0001650,   # interest-rate futures
}
_DEFAULT_GROUP = "index"

# Map a FORTS base ticker (first 2 letters of secid, e.g. "RI" from "RIM6") to a
# fee group. Covers the liquid contracts; unknown → index rate (conservative-ish).
_TICKER_GROUP = {
    # index
    "RI": "index", "MX": "index", "MM": "index", "RV": "index",
    # currency
    "SI": "fx", "EU": "fx", "CR": "fx", "CN": "fx", "ED": "fx", "UC": "fx",
    "AE": "fx", "GB": "fx", "JP": "fx", "TR": "fx",
    # single-stock (common)
    "GZ": "stock", "SR": "stock", "VB": "stock", "LK": "stock", "GM": "stock",
    "RN": "stock", "MN": "stock", "NK": "stock", "TT": "stock", "AF": "stock",
    "FE": "stock", "CH": "stock", "PL": "stock", "TN": "stock", "MG": "stock",
    "SG": "stock", "BS": "stock", "YN": "stock", "PO": "stock", "HY": "stock",
    # commodity
    "BR": "commodity", "GD": "commodity", "SV": "commodity", "PD": "commodity",
    "PT": "commodity", "NG": "commodity", "CU": "commodity", "AL": "commodity",
    "GL": "commodity", "SA": "commodity", "SL": "commodity",
}


def fee_config() -> dict:
    """The full fee model as plain data, so the frontend can render commission with
    the SAME constants instead of a hand-copied duplicate (single source of truth)."""
    return {
        "brokerFeePerContract": BROKER_FEE_PER_CONTRACT,
        "moexTakerRate": dict(MOEX_TAKER_RATE),
        "tickerGroup": dict(_TICKER_GROUP),
        "defaultGroup": _DEFAULT_GROUP,
        "scalperDiscount": SCALPER_DISCOUNT,
    }


def _base_ticker(symbol: str) -> str:
    """RIM6 -> RI, Si-6.26 / SiM6 -> SI, GZM6 -> GZ. Returns upper 2-letter prefix."""
    s = (symbol or "").split("@")[0].split("-")[0].strip().upper()
    return s[:2]


def fee_group(symbol: str) -> str:
    return _TICKER_GROUP.get(_base_ticker(symbol), _DEFAULT_GROUP)


def commission_for(symbol: str, price: float, qty: int, point_value: float,
                   taker: bool, scalper: bool = False,
                   ts: float | int | None = None) -> float:
    """Total commission (rubles) for ONE fill of `qty` contracts of `symbol`.

    taker=True  → MOEX group fee on notional + broker fee  (backtests / market).
    taker=False → broker fee only                          (live / maker limit).
    scalper=True → биржевая часть вдвое: филл принадлежит кругу, открытому и
                   закрытому в ОДНОЙ сессии. Брокерская часть не скидывается.
    ts           → метка филла: на выходных торгах ОБЕ части удваиваются.
    """
    q = abs(int(qty)) or 1
    weekend = WEEKEND_FEE_MULTIPLIER if is_weekend(ts) else 1.0
    broker = BROKER_FEE_PER_CONTRACT * q * weekend
    if not taker:
        return broker
    notional = abs(price) * (point_value or 1.0)
    rate = MOEX_TAKER_RATE.get(fee_group(symbol), MOEX_TAKER_RATE[_DEFAULT_GROUP])
    exchange = rate * notional * q * weekend
    if scalper:
        exchange *= SCALPER_DISCOUNT
    return broker + exchange


def exchange_part(symbol: str, price: float, qty: int, point_value: float,
                  ts: float | int | None = None) -> float:
    """Только БИРЖЕВАЯ часть тейкерского сбора (рубли). Отдельно от брокерской,
    потому что скальперская скидка касается биржевой и НЕ касается брокерской."""
    q = abs(int(qty)) or 1
    rate = MOEX_TAKER_RATE.get(fee_group(symbol), MOEX_TAKER_RATE[_DEFAULT_GROUP])
    w = WEEKEND_FEE_MULTIPLIER if is_weekend(ts) else 1.0
    return rate * abs(price) * (point_value or 1.0) * q * w


def margin_for(exchange_margin: float, ts: float | int | None = None,
               account_multiplier: float = 1.0) -> float:
    """ГО ПОД ОДИН КОНТРАКТ на счёте, рубли.

    Два множителя, и оба измерены, а не выбраны:
      account_multiplier — во сколько раз брокер держит больше биржи. С 01.09.2026
        у счёта статус КПУР, и замеры margin_multiplier_samples упали с ~2.3-2.8
        до 1.00-1.11 (минимум 1.001): брокер держит практически биржевое ГО.
        До 01.09 туда надо подставлять 2.4 — старые отчёты считались по нему.
      выходные — биржа удваивает требование на субботу и воскресенье.
    """
    w = WEEKEND_FEE_MULTIPLIER if is_weekend(ts) else 1.0
    return float(exchange_margin) * float(account_multiplier) * w


def taker_points(symbol: str, price: float, qty: int, point_value: float | None = None) -> float:
    """Taker commission expressed in PRICE POINTS — for a consumer that tracks P&L in
    points and doesn't know ₽/point (the agent runner). MOEX fee = rate × notional_rub
    and notional_rub = price_points × point_value × qty, so in POINTS it is
    rate × price × qty — the point_value CANCELS, giving an exact exchange fee without
    it. The fixed broker fee (₽/contract) only enters when point_value is known; it is
    ~0.6% of the total, so omitting it (point_value=None) is a sub-percent understatement."""
    q = abs(int(qty)) or 1
    rate = MOEX_TAKER_RATE.get(fee_group(symbol), MOEX_TAKER_RATE[_DEFAULT_GROUP])
    pts = rate * abs(price) * q
    if point_value and point_value > 0:
        pts += (BROKER_FEE_PER_CONTRACT * q) / point_value
    return pts
