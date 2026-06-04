"""
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


def _base_ticker(symbol: str) -> str:
    """RIM6 -> RI, Si-6.26 / SiM6 -> SI, GZM6 -> GZ. Returns upper 2-letter prefix."""
    s = (symbol or "").split("@")[0].split("-")[0].strip().upper()
    return s[:2]


def fee_group(symbol: str) -> str:
    return _TICKER_GROUP.get(_base_ticker(symbol), _DEFAULT_GROUP)


def commission_for(symbol: str, price: float, qty: int, point_value: float,
                   taker: bool) -> float:
    """Total commission (rubles) for ONE fill of `qty` contracts of `symbol`.

    taker=True  → MOEX group fee on notional + broker fee  (backtests / market).
    taker=False → broker fee only                          (live / maker limit).
    """
    q = abs(int(qty)) or 1
    broker = BROKER_FEE_PER_CONTRACT * q
    if not taker:
        return broker
    notional = abs(price) * (point_value or 1.0)
    rate = MOEX_TAKER_RATE.get(fee_group(symbol), MOEX_TAKER_RATE[_DEFAULT_GROUP])
    exchange = rate * notional * q
    return broker + exchange
