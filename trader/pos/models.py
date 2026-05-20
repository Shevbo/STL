from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class Position(BaseModel):
    symbol: str
    account_id: str
    side: Literal["long", "short", "flat"]
    quantity: int
    avg_price: Decimal = Decimal(0)
    current_price: Decimal
    var_margin: Decimal


class AccountSummary(BaseModel):
    deposit: Decimal
    free: Decimal
    in_position: Decimal
    variation_margin: Decimal
