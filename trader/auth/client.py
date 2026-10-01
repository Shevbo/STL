import asyncio
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
        # ЗАМОК НА ОБНОВЛЕНИЕ СЕССИИ. Без него N одновременных вызовов создавали N
        # сессий в Finam: каждый уходил в _fetch_token, и в кэш попадала та, что
        # присвоилась последней. Finam вытесняет предыдущую сессию, поэтому
        # кэшированный токен не обязан быть живым — а запросы с вытесненным
        # токеном Finam отбивает 500 на счёте и 503 на заявках, НЕ 401, и по
        # статусу это читалось как авария на их стороне.
        #
        # Гонка была гарантированной, а не случайной: WsHub._pos_poll_loop берёт
        # токен ДВАЖДЫ за круг через asyncio.gather (портфель и счёт), плюс тот же
        # клиент делят tx, лента и замеры задержки. В журнале 01.10.2026 видно три
        # auth.fetch_token в одну секунду (05:16:29) и два в 05:02:19, после чего
        # опрос падал 100% времени, раз в пять секунд, сутками.
        self._refresh_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(http2=True, base_url=base_url)

    def _token_fresh(self) -> bool:
        return bool(
            self._cached_token
            and not self._cached_token.is_expired(self._refresh_before_secs)
        )

    async def get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_fresh():
            return self._cached_token.token
        seen = self._cached_token          # что лежало в кэше ДО очереди за замком
        async with self._refresh_lock:
            # Пока мы ждали очереди, сосед мог обновить сессию — тогда своя не
            # нужна, иначе замок лишь выстроил бы те же лишние сессии в ряд.
            #
            # Сравниваем ОБЪЕКТ, а не свежесть. По свежести force_refresh вернул
            # бы ровно тот токен, который вызывающий и отверг: он просит новую
            # сессию именно потому, что знает — этот мёртв, хотя по часам ещё
            # «свежий». Вытесненный токен Finam отбивает 500/503, а не 401, так
            # что срока жизни тут недостаточно, чтобы судить о годности.
            if self._cached_token is not seen and self._token_fresh():
                return self._cached_token.token
            self._cached_token = await self._fetch_token()
            return self._cached_token.token

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
