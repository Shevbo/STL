from decimal import Decimal

import httpx
import structlog

from trader.registry.models import Instrument, InstrumentDetail, TradingParams

log = structlog.get_logger()

_ASSETS_ALL_PATH = "/v1/assets/all"
_ASSET_DETAIL_PATH = "/v1/assets/{symbol}"
_ASSET_PARAMS_PATH = "/v1/assets/{symbol}/params"


class InstrumentRegistry:
    def __init__(self, base_url: str, get_token):
        self._base_url = base_url
        self._get_token = get_token
        self._cache: dict[str, Instrument] | None = None
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    async def _token(self) -> str:
        result = self._get_token()
        if hasattr(result, "__await__"):
            return await result
        return result

    async def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._token()}"}

    def _parse_instrument(self, data: dict) -> Instrument:
        return Instrument(
            symbol=data["symbol"],
            ticker=data["ticker"],
            mic=data["mic"],
            name=data["name"],
            type=data.get("type", ""),
            is_archived=data.get("is_archived", False),
        )

    async def _load_all(self) -> dict[str, Instrument]:
        cache: dict[str, Instrument] = {}
        cursor = 0
        headers = await self._auth_headers()
        while True:
            params = {"only_active": "true", "cursor": str(cursor)}
            response = await self._http.get(
                _ASSETS_ALL_PATH, headers=headers, params=params
            )
            response.raise_for_status()
            body = response.json()
            for item in body.get("assets", []):
                inst = self._parse_instrument(item)
                cache[inst.symbol] = inst
            next_cursor = body.get("next_cursor", 0)
            if not next_cursor:
                break
            cursor = next_cursor
        log.info("registry.loaded", count=len(cache))
        return cache

    async def search(self, ticker: str) -> list[Instrument]:
        if self._cache is None:
            self._cache = await self._load_all()
        return [inst for inst in self._cache.values() if inst.ticker == ticker]

    async def get_detail(self, symbol: str, account_id: str) -> InstrumentDetail:
        from datetime import date, datetime

        headers = await self._auth_headers()
        path = _ASSET_DETAIL_PATH.format(symbol=symbol)
        response = await self._http.get(
            path, headers=headers, params={"account_id": account_id}
        )
        response.raise_for_status()
        data = response.json()

        decimals = data.get("decimals", 0)
        raw_min_step = data.get("min_step", 0)
        min_step = Decimal(str(raw_min_step)) / (Decimal("10") ** decimals)

        lot_size = Decimal(data.get("lot_size", {}).get("value", "1"))

        expiration_date: date | None = None
        future_details = data.get("future_details")
        if future_details and future_details.get("expiration_date"):
            expiration_date = datetime.fromisoformat(
                future_details["expiration_date"].replace("Z", "+00:00")
            ).date()

        return InstrumentDetail(
            symbol=symbol,
            ticker=data.get("ticker", ""),
            mic=data.get("mic", ""),
            name=data.get("name", ""),
            type=data.get("type", ""),
            is_archived=data.get("is_archived", False),
            lot_size=lot_size,
            min_step=min_step,
            expiration_date=expiration_date,
            quote_currency=data.get("quote_currency", ""),
        )

    async def get_params(self, symbol: str, account_id: str) -> TradingParams:
        headers = await self._auth_headers()
        path = _ASSET_PARAMS_PATH.format(symbol=symbol)
        response = await self._http.get(
            path, headers=headers, params={"account_id": account_id}
        )
        response.raise_for_status()
        data = response.json()

        is_tradable_obj = data.get("is_tradable")
        is_tradable = bool(is_tradable_obj.get("value")) if is_tradable_obj else False

        def parse_money(obj: dict | None) -> Decimal:
            if not obj:
                return Decimal("0")
            units = Decimal(str(obj.get("units", "0")))
            nanos = Decimal(str(obj.get("nanos", 0))) / Decimal("1000000000")
            return units + nanos

        return TradingParams(
            symbol=symbol,
            is_tradable=is_tradable,
            long_initial_margin=parse_money(data.get("long_initial_margin")),
            short_initial_margin=parse_money(data.get("short_initial_margin")),
        )

    async def aclose(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
