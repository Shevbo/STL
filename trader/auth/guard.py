from fastapi import HTTPException, Request, WebSocket

from trader.auth.portal import verify_session_token

_SESSION_COOKIE = "shectory_session"


def _get_email(bridge_secret: str, request: Request) -> str | None:
    if not bridge_secret:
        return "debug"
    token = request.cookies.get(_SESSION_COOKIE)
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
        return True
    token = websocket.cookies.get(_SESSION_COOKIE)
    if not token:
        return False
    return verify_session_token(token, bridge_secret) is not None
