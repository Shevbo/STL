from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["limit", "market"] = "limit"
    price: Decimal | None = None
    client_order_id: str = Field(default_factory=lambda: uuid4().hex[:20])


class OrderResponse(BaseModel):
    order_id: str
    status: str
