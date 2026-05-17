# tests/conftest.py
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import grpc as _grpc
import pytest

from trader.auth.models import TokenResponse

# Extend the grpc package path so generated stubs are importable as grpc.tradeapi.*
_GEN_GRPC = str(Path(__file__).parent.parent / "trader" / "proto" / "gen" / "grpc")
if _GEN_GRPC not in _grpc.__path__:
    _grpc.__path__.append(_GEN_GRPC)


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
