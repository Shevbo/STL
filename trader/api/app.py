from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from trader.api.ws_hub import WsHub
from trader.auth.client import AsyncAuthClient
from trader.auth.guard import require_auth, ws_auth_ok
from trader.auth.portal import make_session_token, verify_portal_credentials
from trader.config import Settings
from trader.md.feed import MarketDataFeed
from trader.md.grpc_client import BarsStream, OrderBookStream, QuoteStream
from trader.pos.client import PositionsClient
from trader.pos.models import Position
from trader.tx.client import TxClient
from trader.tx.models import OrderRequest, OrderResponse

log = structlog.get_logger()

_SESSION_COOKIE = "shectory_session"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    auth = AsyncAuthClient(
        secret_token=settings.finam_secret_token.get_secret_value(),
        base_url=settings.finam_api_base_url,
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    )

    await auth.get_token()
    account_id = settings.finam_account_id or auth.account_id

    qs = QuoteStream()
    feed = MarketDataFeed(qs=qs)
    bars_stream = BarsStream()
    book_stream = OrderBookStream()

    pos = PositionsClient(
        base_url=settings.finam_api_base_url,
        get_token=auth.get_token,
        account_id=account_id,
    )
    tx = TxClient(
        base_url=settings.finam_api_base_url,
        get_token=auth.get_token,
        account_id=account_id,
    )

    hub = WsHub(
        feed,
        pos_client=pos,
        mvp_symbol=settings.finam_mvp_symbol,
        bars_stream=bars_stream,
        book_stream=book_stream,
        base_url=settings.finam_api_base_url,
        get_token=auth.get_token,
        account_id=account_id,
    )

    try:
        await qs.start(get_token=auth.get_token)
        await feed.start(get_token=auth.get_token)
        await bars_stream.start(get_token=auth.get_token)
        await book_stream.start(get_token=auth.get_token)
        await hub.start(symbols=[settings.finam_mvp_symbol] if settings.finam_mvp_symbol else [])
    except Exception as exc:
        log.error("startup.md_failed", exc=str(exc))

    app.state.hub = hub
    app.state.tx = tx
    app.state.pos = pos
    app.state.settings = settings
    app.state.auth = auth
    app.state.account_id = account_id

    yield

    await hub.stop()
    await feed.aclose()
    await auth.aclose()


class LoginRequest(BaseModel):
    email: str
    password: str


def create_app() -> FastAPI:
    fastapi_app = FastAPI(lifespan=lifespan)

    @fastapi_app.post("/api/auth/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        settings: Settings = request.app.state.settings
        secret = settings.shectory_auth_bridge_secret
        if not secret:
            return {"ok": True}
        user = await verify_portal_credentials(
            body.email, body.password,
            settings.shectory_portal_url, secret,
            local_email=settings.shectory_local_user_email,
            local_pw_sha256=settings.shectory_local_user_password_sha256,
        )
        if not user:
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        token = make_session_token(user.email, secret)
        response.set_cookie(
            _SESSION_COOKIE, token,
            httponly=True, samesite="lax",
            secure=False, path="/", max_age=_COOKIE_MAX_AGE,
        )
        return {"ok": True, "email": user.email, "role": user.role}

    @fastapi_app.get("/api/auth/me")
    async def auth_me(request: Request):
        settings: Settings = request.app.state.settings
        email = require_auth(settings.shectory_auth_bridge_secret, request)
        return {"ok": True, "email": email}

    @fastapi_app.post("/api/auth/logout")
    async def logout(response: Response):
        response.delete_cookie(_SESSION_COOKIE, path="/")
        return {"ok": True}

    @fastapi_app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        settings: Settings = websocket.app.state.settings
        if not ws_auth_ok(settings.shectory_auth_bridge_secret, websocket):
            await websocket.close(code=4401)
            return
        await websocket.app.state.hub.connect(websocket)

    @fastapi_app.post("/api/v1/orders", response_model=OrderResponse)
    async def place_order(body: OrderRequest, request: Request):
        import httpx as _httpx
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        try:
            return await request.app.state.tx.place_order(body)
        except _httpx.HTTPStatusError as exc:
            msg = exc.response.text
            try:
                msg = exc.response.json().get("message", msg)
            except Exception:
                pass
            raise HTTPException(status_code=exc.response.status_code, detail=msg)

    @fastapi_app.get("/api/v1/portfolio", response_model=list[Position])
    async def get_portfolio(request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        return await request.app.state.pos.get_portfolio()

    @fastapi_app.get("/api/v1/instruments")
    async def list_instruments(request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        settings: Settings = request.app.state.settings
        auth_client: AsyncAuthClient = request.app.state.auth
        try:
            token = await auth_client.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(http2=True) as client:
                resp = await client.get(
                    f"{settings.finam_api_base_url}/v1/assets",
                    headers=headers,
                    timeout=10.0,
                )
                resp.raise_for_status()
                body = resp.json()
            instruments = [
                {
                    "symbol": a.get("symbol", ""),
                    "ticker": a.get("ticker", a.get("code", "")),
                    "name": a.get("name", a.get("short_name", "")),
                }
                for a in body.get("assets", [])
                if "@RTSX" in a.get("symbol", "")
            ]
            return {"instruments": instruments}
        except Exception as exc:
            log.error("api.instruments_error", exc=str(exc))
            raise HTTPException(status_code=502, detail="Finam API unavailable")

    @fastapi_app.get("/api/v1/instruments/{symbol:path}/params")
    async def get_instrument_params(symbol: str, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        settings: Settings = request.app.state.settings
        auth_client: AsyncAuthClient = request.app.state.auth
        try:
            token = await auth_client.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            params: dict = {}
            account_id: str = request.app.state.account_id
            if account_id:
                params["account_id"] = account_id
            async with httpx.AsyncClient(http2=True) as client:
                resp = await client.get(
                    f"{settings.finam_api_base_url}/v1/assets/{symbol}/params",
                    headers=headers,
                    params=params,
                    timeout=10.0,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.error("api.instrument_params_error", exc=str(exc), symbol=symbol)
            raise HTTPException(status_code=502, detail="Finam API unavailable")

    @fastapi_app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        log.error("api.unhandled_error", exc=str(exc), path=str(request.url))
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    return fastapi_app


app = create_app()
