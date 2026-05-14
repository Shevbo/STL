from datetime import datetime, timezone, timedelta
from trader.auth.models import TokenResponse


def test_token_expired_when_past_expiry():
    token = TokenResponse(
        access_token="tok_123",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert token.is_expired(buffer_secs=0) is True


def test_token_not_expired_when_far_future():
    token = TokenResponse(
        access_token="tok_123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert token.is_expired(buffer_secs=0) is False


def test_token_expired_with_buffer():
    # Expires in 30 seconds, buffer is 60 → treated as expired
    token = TokenResponse(
        access_token="tok_123",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    assert token.is_expired(buffer_secs=60) is True
