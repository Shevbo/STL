from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class FeedState(Enum):
    CONNECTING = "connecting"
    LIVE = "live"
    STALE = "stale"
    CLOSED = "closed"


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    bid_size: int
    ask: Decimal
    ask_size: int
    last: Decimal
    last_size: int
    timestamp: datetime  # always UTC-aware

    @classmethod
    def from_payload(cls, symbol: str, data: dict) -> "Quote":
        def dec(obj) -> Decimal:
            if isinstance(obj, dict):
                return Decimal(obj["value"])
            return Decimal(str(obj))

        return cls(
            symbol=symbol,
            bid=dec(data["bid"]),
            bid_size=int(data.get("bid_size", 0)),
            ask=dec(data["ask"]),
            ask_size=int(data.get("ask_size", 0)),
            last=dec(data["last"]),
            last_size=int(data.get("last_size", 0)),
            timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
        )
