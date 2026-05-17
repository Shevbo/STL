# tests/auth/test_auth_client.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from trader.auth.client import AsyncAuthClient
from trader.auth.models import TokenResponse


@pytest.fixture
def auth_client():
    return AsyncAuthClient(
        base_url="https://api.example.com",
        secret_token="test_secret",
        refresh_before_secs=60,
    )


async def test_get_token_calls_fetch_on_first_call(auth_client, mock_token):
    with patch.object(auth_client, "_fetch_token", return_value=mock_token) as mock_fetch:
        token = await auth_client.get_token()

    mock_fetch.assert_called_once()
    assert token == "mock_jwt_token_abc123"


async def test_cached_token_is_reused(auth_client, mock_token):
    auth_client._cached_token = mock_token

    with patch.object(auth_client, "_fetch_token") as mock_fetch:
        token = await auth_client.get_token()

    mock_fetch.assert_not_called()
    assert token == mock_token.access_token


async def test_expired_token_triggers_refresh(auth_client, expired_token):
    auth_client._cached_token = expired_token
    new_token = TokenResponse(
        token="new_jwt_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    with patch.object(auth_client, "_fetch_token", return_value=new_token) as mock_fetch:
        token = await auth_client.get_token()

    mock_fetch.assert_called_once()
    assert token == "new_jwt_xyz"


async def test_get_token_force_refresh_bypasses_cache(respx_mock):
    """force_refresh=True must re-fetch even if token is not expired."""
    import httpx

    respx_mock.post("https://api.finam.ru/v1/sessions").mock(
        return_value=httpx.Response(200, json={"token": "fresh_tok"})
    )
    respx_mock.post("https://api.finam.ru/v1/sessions/details").mock(
        return_value=httpx.Response(
            200,
            json={"expires_at": "2099-01-01T00:00:00Z"},
        )
    )
    client = AsyncAuthClient(
        base_url="https://api.finam.ru",
        secret_token="sec",
    )
    # Prime the cache with a non-expired token
    await client.get_token()
    # Second call: force_refresh must bypass the cache and re-fetch
    respx_mock.post("https://api.finam.ru/v1/sessions").mock(
        return_value=httpx.Response(200, json={"token": "second_tok"})
    )
    respx_mock.post("https://api.finam.ru/v1/sessions/details").mock(
        return_value=httpx.Response(200, json={"expires_at": "2099-01-01T00:00:00Z"})
    )
    tok = await client.get_token(force_refresh=True)
    assert tok == "second_tok"
    await client.aclose()
