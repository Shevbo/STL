import os

from fastapi import HTTPException, Request, WebSocket

from trader.auth.portal import verify_session_token

_SESSION_COOKIE = "shectory_session"


def _dev_bypass() -> bool:
    """Explicit local-dev opt-in. Without it, an empty bridge_secret fails closed.

    Historically an empty secret silently authenticated every request as "debug".
    That turns a misconfigured production deploy into fully open access (orders,
    robot control, strategy code exec). Now the bypass must be opted into via
    SHECTORY_AUTH_DEV_BYPASS=1; production (var unset) denies access instead.
    """
    return os.getenv("SHECTORY_AUTH_DEV_BYPASS", "").strip().lower() in ("1", "true", "yes")


def _get_email(bridge_secret: str, request: Request) -> str | None:
    if not bridge_secret:
        return "debug" if _dev_bypass() else None
    token = request.cookies.get(_SESSION_COOKIE)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            return None
    if not token:
        return None
    return verify_session_token(token, bridge_secret)


def auth_ok(bridge_secret: str, request: Request) -> bool:
    return _get_email(bridge_secret, request) is not None


def require_auth(bridge_secret: str, request: Request) -> str:
    email = _get_email(bridge_secret, request)
    if email is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return email


def ws_auth_ok(bridge_secret: str, websocket: WebSocket) -> bool:
    if not bridge_secret:
        return _dev_bypass()
    token = websocket.cookies.get(_SESSION_COOKIE)
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = websocket.query_params.get("token")
    if not token:
        return False
    return verify_session_token(token, bridge_secret) is not None
