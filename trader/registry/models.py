from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class Instrument:
    symbol: str
    ticker: str
    mic: str
    name: str
    type: str
    is_archived: bool


@dataclass
class InstrumentDetail(Instrument):
    lot_size: Decimal
    min_step: Decimal
    expiration_date: date | None
    quote_currency: str


@dataclass
class TradingParams:
    symbol: str
    is_tradable: bool
    long_initial_margin: Decimal
    short_initial_margin: Decimal
