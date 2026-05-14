"""
Integration tests — requires real Finam credentials.

Setup:
  cp .env.example .env
  # Edit .env with real FINAM_SECRET_TOKEN and FINAM_ACCOUNT_ID

Run:
  poetry run pytest tests/auth/test_auth_integration.py -v -m integration
"""
import pytest

from trader.auth.client import AsyncAuthClient
from trader.config import Settings

pytestmark = pytest.mark.integration


async def test_real_auth_returns_jwt():
    settings = Settings()

    async with AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    ) as client:
        token = await client.get_token()

    assert token
    assert len(token) > 10


async def test_second_call_uses_cache():
    settings = Settings()

    async with AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    ) as client:
        token1 = await client.get_token()
        token2 = await client.get_token()  # should use cache

    assert token1 == token2
