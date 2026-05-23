from collections.abc import Awaitable, Callable

import httpx
import structlog

from trader.tx.models import OrderRequest, OrderResponse

log = structlog.get_logger()

_SIDE_MAP = {"buy": "SIDE_BUY", "sell": "SIDE_SELL"}
_TYPE_MAP = {"limit": "ORDER_TYPE_LIMIT", "market": "ORDER_TYPE_MARKET"}


class TxClient:
    def __init__(
        self,
        base_url: str,
        get_token: Callable[[], Awaitable[str]],
        account_id: str,
    ) -> None:
        self._get_token = get_token
        self._account_id = account_id
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    async def place_order(self, req: OrderRequest) -> OrderResponse:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        body: dict = {
            "client_order_id": req.client_order_id,
            "symbol": req.symbol,
            "side": _SIDE_MAP[req.side],
            "quantity": {"value": str(req.quantity)},
            "type": _TYPE_MAP[req.order_type],
            "time_in_force": "TIME_IN_FORCE_DAY",
            "comment": "STL",
        }
        if req.price is not None:
            body["limit_price"] = {"value": f"{req.price:.1f}"}
        path = f"/v1/accounts/{self._account_id}/orders/"
        log.info("tx.place_order", symbol=req.symbol, side=req.side, quantity=req.quantity)
        resp = await self._http.post(path, json=body, headers=headers)
        if not resp.is_success:
            log.error("tx.place_order_error", status=resp.status_code, body=resp.text)
        resp.raise_for_status()
        data = resp.json()
        return OrderResponse(
            order_id=data.get("order_id", data.get("id", "")),
            status=data.get("status", "submitted"),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
