# tests/conftest.py
import pytest
from datetime import datetime, timezone, timedelta
from trader.auth.models import TokenResponse


@pytest.fixture
def mock_token():
    return TokenResponse(
        access_token="mock_jwt_token_abc123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def expired_token():
    return TokenResponse(
        access_token="expired_jwt_token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
