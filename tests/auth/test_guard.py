from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from trader.auth.guard import auth_ok, require_auth, ws_auth_ok
from trader.auth.portal import make_session_token, verify_session_token

SECRET = "test-secret"


def _valid_token(email: str = "user@example.com") -> str:
    return make_session_token(email, SECRET)


def _req(cookies: dict = {}):
    r = MagicMock()
    r.cookies = cookies
    return r


def _ws(cookies: dict = {}):
    ws = MagicMock()
    ws.cookies = cookies
    return ws


# --- session token ---

def test_make_and_verify_token():
    token = make_session_token("user@example.com", SECRET)
    assert verify_session_token(token, SECRET) == "user@example.com"


def test_verify_token_wrong_secret():
    token = make_session_token("user@example.com", SECRET)
    assert verify_session_token(token, "other-secret") is None


def test_verify_token_tampered():
    token = make_session_token("user@example.com", SECRET)
    assert verify_session_token(token + "x", SECRET) is None


def test_verify_token_expired():
    with patch("trader.auth.portal.time") as mock_time:
        mock_time.time.return_value = 1000
        token = make_session_token("user@example.com", SECRET)
    with patch("trader.auth.portal.time") as mock_time:
        mock_time.time.return_value = 1000 + 60 * 60 * 24 * 31
        assert verify_session_token(token, SECRET) is None


# --- auth_ok / require_auth ---

def test_auth_ok_no_secret_always_passes():
    assert auth_ok("", _req()) is True


def test_auth_ok_valid_cookie():
    token = _valid_token()
    assert auth_ok(SECRET, _req(cookies={"shectory_session": token})) is True


def test_auth_ok_invalid_cookie():
    assert auth_ok(SECRET, _req(cookies={"shectory_session": "bad"})) is False


def test_auth_ok_no_cookie():
    assert auth_ok(SECRET, _req()) is False


def test_require_auth_returns_email():
    token = _valid_token("a@b.com")
    email = require_auth(SECRET, _req(cookies={"shectory_session": token}))
    assert email == "a@b.com"


def test_require_auth_raises_401():
    with pytest.raises(HTTPException) as exc:
        require_auth(SECRET, _req())
    assert exc.value.status_code == 401


# --- ws_auth_ok ---

def test_ws_auth_ok_no_secret():
    assert ws_auth_ok("", _ws()) is True


def test_ws_auth_ok_valid_cookie():
    token = _valid_token()
    assert ws_auth_ok(SECRET, _ws(cookies={"shectory_session": token})) is True


def test_ws_auth_ok_no_cookie():
    assert ws_auth_ok(SECRET, _ws()) is False
