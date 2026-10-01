"""Одновременные запросы токена обязаны создать ОДНУ сессию Finam.

Без замка WsHub._pos_poll_loop гарантированно создавал две сессии за круг (он
берёт токен дважды через asyncio.gather), а тот же клиент делят tx, лента и
замеры задержки. Finam вытесняет предыдущую сессию, и кэшированный токен
оказывался мёртвым: 500 на счёте, 503 на заявках, НЕ 401 — поэтому авария
читалась как чужая. В журнале 01.10.2026 видно три auth.fetch_token в одну
секунду, после чего опрос падал 100% времени сутками.
"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from trader.auth.client import AsyncAuthClient
from trader.auth.models import TokenResponse


def _client(fetches: list[int], delay: float = 0.02) -> AsyncAuthClient:
    c = AsyncAuthClient("https://example.invalid", "secret", refresh_before_secs=60)

    async def fake_fetch() -> TokenResponse:
        fetches.append(1)
        await asyncio.sleep(delay)          # сетевой круг, в котором и была гонка
        return TokenResponse(token=f"t{len(fetches)}",
                             expires_at=datetime.now(UTC) + timedelta(minutes=15),
                             account_id="2035452")

    c._fetch_token = fake_fetch             # noqa: SLF001 — подмена сетевого вызова
    return c


@pytest.mark.asyncio
async def test_concurrent_callers_create_one_session():
    fetches: list[int] = []
    c = _client(fetches)
    tokens = await asyncio.gather(*(c.get_token() for _ in range(8)))
    assert len(fetches) == 1, f"создано сессий: {len(fetches)} — Finam вытеснит все кроме одной"
    assert len(set(tokens)) == 1, "все вызывающие обязаны получить ОДИН и тот же токен"


@pytest.mark.asyncio
async def test_pos_poll_pair_creates_one_session():
    """Тот самый случай: портфель и счёт берут токен одновременно."""
    fetches: list[int] = []
    c = _client(fetches)

    async def leg():
        return await c.get_token()

    a, b = await asyncio.gather(leg(), leg())
    assert len(fetches) == 1
    assert a == b


@pytest.mark.asyncio
async def test_cached_token_is_reused_without_fetching():
    fetches: list[int] = []
    c = _client(fetches)
    first = await c.get_token()
    second = await c.get_token()
    assert first == second
    assert len(fetches) == 1, "свежий токен не требует новой сессии"


@pytest.mark.asyncio
async def test_expired_token_is_refreshed_once_even_under_concurrency():
    fetches: list[int] = []
    c = _client(fetches)
    await c.get_token()
    c._cached_token = TokenResponse(                      # noqa: SLF001
        token="stale",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        account_id="2035452")
    tokens = await asyncio.gather(*(c.get_token() for _ in range(5)))
    assert len(fetches) == 2, "одно обновление на всех, а не по одному на вызов"
    assert set(tokens) == {"t2"}
