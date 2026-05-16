# tests/conftest.py
from datetime import datetime, timedelta, timezone

import pytest

from trader.auth.models import TokenResponse


@pytest.fixture
def mock_token():
    return TokenResponse(
        token="mock_jwt_token_abc123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def expired_token():
    return TokenResponse(
        token="expired_jwt_token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
