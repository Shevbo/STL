from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Literal

import httpx
import structlog

from trader.pos.models import AccountSummary, Position
from trader.util import unwrap_decimal

log = structlog.get_logger()


def _dec(obj) -> Decimal:
    return unwrap_decimal(obj)


class PositionsClient:
    def __init__(
        self,
        base_url: str,
        get_token: Callable[[], Awaitable[str]],
        account_id: str,
    ) -> None:
        self._get_token = get_token
        self._account_id = account_id
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    async def get_portfolio(self) -> list[Position]:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._http.get(f"/v1/accounts/{self._account_id}", headers=headers)
        resp.raise_for_status()
        body = resp.json()

        result: list[Position] = []
        for p in body.get("positions", []):
            raw_qty = _dec(p.get("quantity", "0"))
            qty = int(raw_qty)
            side: Literal["long", "short", "flat"]
            if qty > 0:
                side = "long"
            elif qty < 0:
                side = "short"
            else:
                side = "flat"
            result.append(Position(
                symbol=p.get("symbol", ""),
                account_id=self._account_id,
                side=side,
                quantity=abs(qty),
                avg_price=Decimal(0),
                current_price=_dec(p.get("current_price", "0")),
                var_margin=_dec(p.get("unrealized_pnl", "0")),
            ))
        return result

    async def get_account_summary(self) -> AccountSummary:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._http.get(f"/v1/accounts/{self._account_id}", headers=headers)
        resp.raise_for_status()
        body = resp.json()

        forts = body.get("portfolio_forts", {})
        return AccountSummary(
            deposit=_dec(body.get("equity", "0")),
            free=_dec(forts.get("available_cash", "0")),
            in_position=_dec(forts.get("money_reserved", "0")),
            variation_margin=_dec(body.get("unrealized_profit", "0")),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
