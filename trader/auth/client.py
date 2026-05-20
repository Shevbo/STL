from datetime import datetime

import httpx
import structlog

from trader.auth.models import TokenResponse

log = structlog.get_logger()

_TOKEN_PATH = "/v1/sessions"
_DETAILS_PATH = "/v1/sessions/details"


class AsyncAuthClient:
    def __init__(self, base_url: str, secret_token: str, refresh_before_secs: int = 60):
        self._base_url = base_url
        self._secret_token = secret_token
        self._refresh_before_secs = refresh_before_secs
        self._cached_token: TokenResponse | None = None
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    async def get_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._cached_token
            and not self._cached_token.is_expired(self._refresh_before_secs)
        ):
            return self._cached_token.access_token
        self._cached_token = await self._fetch_token()
        return self._cached_token.access_token

    @property
    def account_id(self) -> str:
        return self._cached_token.account_id if self._cached_token else ""

    async def _fetch_token(self) -> TokenResponse:
        log.info("auth.fetch_token", base_url=self._base_url)
        response = await self._http.post(
            _TOKEN_PATH,
            json={"secret": self._secret_token},
        )
        response.raise_for_status()
        access_token = response.json()["token"]

        details = await self._http.post(_DETAILS_PATH, json={"token": access_token})
        details.raise_for_status()
        details_json = details.json()
        expires_at = datetime.fromisoformat(
            details_json["expires_at"].replace("Z", "+00:00")
        )
        account_ids = details_json.get("account_ids", [])
        account_id = account_ids[0] if account_ids else ""
        if account_id:
            log.info("auth.account_id_detected", account_id=account_id)
        else:
            log.warning("auth.account_id_not_found")
        return TokenResponse(token=access_token, expires_at=expires_at, account_id=account_id)

    async def aclose(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
