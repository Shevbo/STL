import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger()


_VERIFY_PATH = "/api/internal/verify-portal-credentials"
_SESSION_COOKIE = "shectory_session"
_SESSION_TTL = 60 * 60 * 24 * 30


@dataclass
class PortalUser:
    email: str
    role: str
    full_name: str


def _check_local(
    email: str,
    password: str,
    local_email: str,
    local_pw_sha256: str,
) -> PortalUser | None:
    if not local_email or not local_pw_sha256:
        return None
    if email.strip().lower() != local_email.strip().lower():
        return None
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    if not hmac.compare_digest(pw_hash, local_pw_sha256.lower()):
        return None
    return PortalUser(email=local_email.strip().lower(), role="trader", full_name="")


async def verify_portal_credentials(
    email: str,
    password: str,
    portal_url: str,
    bridge_secret: str,
    local_email: str = "",
    local_pw_sha256: str = "",
) -> PortalUser | None:
    url = portal_url.rstrip("/") + _VERIFY_PATH
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.post(
                url,
                json={"email": email.strip().lower(), "password": password},
                headers={"Authorization": f"Bearer {bridge_secret}"},
            )
        if not r.is_success:
            return _check_local(email, password, local_email, local_pw_sha256)
        j = r.json()
        if not j.get("ok") or not j.get("email"):
            return None
        return PortalUser(
            email=j["email"],
            role=j.get("role", "user"),
            full_name=j.get("fullName", "") or "",
        )
    except Exception as exc:
        log.warning("portal_auth.error", exc=str(exc))
        return _check_local(email, password, local_email, local_pw_sha256)


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session_token(email: str, secret: str) -> str:
    expires = int(time.time()) + _SESSION_TTL
    payload = f"{email}:{expires}"
    sig = _sign(payload, secret)
    return f"{payload}:{sig}"


def verify_session_token(token: str, secret: str) -> str | None:
    """Returns email if valid, None otherwise."""
    try:
        parts = token.rsplit(":", 2)
        if len(parts) != 3:
            return None
        email, expires_str, sig = parts
        payload = f"{email}:{expires_str}"
        if not hmac.compare_digest(sig, _sign(payload, secret)):
            return None
        if int(time.time()) > int(expires_str):
            return None
        return email
    except Exception:
        return None
