from contextlib import asynccontextmanager

import httpx
import structlog
from cuid2 import cuid
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

    # LAB: init DB pool and scheduler
    from trader.db import init_pool, close_pool, get_pool
    from trader.lab.scheduler import RobotScheduler
    if settings.lab_db_url:
        await init_pool(settings.lab_db_url)
        scheduler = RobotScheduler(
            db_pool=get_pool(),
            tx_client=tx,
            pos_client=pos,
        )
        await scheduler.start()
        db_pool = get_pool()
    else:
        scheduler = RobotScheduler(db_pool=None)
        db_pool = None
        log.warning("lab.db_url_not_set", msg="LAB features disabled")

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
    app.state.scheduler = scheduler
    app.state.db_pool = db_pool

    yield

    await scheduler.stop_all()
    if settings.lab_db_url:
        from trader.db import close_pool
        await close_pool()
    await hub.stop()
    await feed.aclose()
    await auth.aclose()


async def _run_backtest_task(run_id: str, body: dict, pool, app_state) -> None:
    import json
    import itertools
    from trader.lab.backtest import run_backtest_isolated
    from trader.lab.runtime import Bar

    try:
        await pool.execute(
            "UPDATE backtest_runs SET status='running' WHERE id=$1", run_id
        )
        robot_row = await pool.fetchrow(
            "SELECT script_code, params_json FROM robots WHERE id=$1", body["robotId"]
        )
        script_code = robot_row["script_code"]
        base_params = (
            robot_row["params_json"]
            if isinstance(robot_row["params_json"], dict)
            else json.loads(robot_row["params_json"])
        )
        bars = await _fetch_bars_for_backtest(
            body.get("symbol", base_params.get("symbol", "")),
            body["dateFrom"], body["dateTo"], app_state,
        )
        grid = body.get("paramsGrid", {})
        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = list(itertools.product(*values))

        for combo in combos:
            params = {**base_params, **dict(zip(keys, combo))}
            try:
                result = await run_backtest_isolated(
                    script_code, bars, params.get("symbol", ""), params
                )
                res_id = cuid()
                await pool.execute(
                    """INSERT INTO backtest_results
                       (id, run_id, params, trades, equity_curve, sharpe, max_drawdown, win_rate, total_return, total_trades)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    res_id, run_id,
                    json.dumps(params), json.dumps(result["trades"]),
                    json.dumps(result["equity_curve"]),
                    result.get("sharpe"), result.get("max_drawdown"),
                    result.get("win_rate"), result.get("total_return"),
                    result.get("total_trades"),
                )
            except Exception as exc:
                log.warning("backtest.combo_failed", error=str(exc), params=params)

        await pool.execute(
            "UPDATE backtest_runs SET status='done', finished_at=now() WHERE id=$1",
            run_id,
        )
    except Exception as exc:
        await pool.execute(
            "UPDATE backtest_runs SET status='failed', error_msg=$1 WHERE id=$2",
            str(exc), run_id,
        )


async def _fetch_bars_for_backtest(symbol: str, date_from: str, date_to: str, app_state) -> list:
    from trader.lab.runtime import Bar
    token = await app_state.auth.get_token()
    url = f"{app_state.settings.finam_api_base_url}/v1/bars"
    async with httpx.AsyncClient(http2=True) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "symbol": symbol,
                "timeframe": "TIME_FRAME_M1",
                "from": date_from,
                "to": date_to,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        raw = resp.json()
    bars = []
    for b in raw.get("bars", []):
        def _f(v):
            return float(v["value"] if isinstance(v, dict) else v)
        bars.append(Bar(
            time=int(b["time"]) if isinstance(b["time"], (int, float)) else int(b.get("timestamp", 0)),
            open=_f(b["open"]),
            high=_f(b["high"]),
            low=_f(b["low"]),
            close=_f(b["close"]),
            volume=int(b.get("volume", 0)),
        ))
    return bars


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
        return {"ok": True, "email": user.email, "role": user.role, "token": token}

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
                resp_json = exc.response.json()
                msg = resp_json.get("message", msg)
                # Translate Finam error [666] uncovered position
                if "[666]" in msg or "непокрытая" in msg.lower() or "uncovered" in msg.lower():
                    raise HTTPException(
                        status_code=exc.response.status_code,
                        detail="Broker rejected: uncovered position risk. Verify: margin available, risk level (КПУР/КОУР), overnight position limits, or instrument restrictions."
                    )
            except HTTPException:
                raise
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

    # ── LAB: STL Links ───────────────────────────────────────────────
    @fastapi_app.get("/api/v1/stl-links")
    async def list_stl_links(request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        if pool is None:
            return []
        rows = await pool.fetch("SELECT * FROM stl_links ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    @fastapi_app.post("/api/v1/stl-links", status_code=201)
    async def create_stl_link(body: dict, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        new_id = cuid()
        await pool.execute(
            """INSERT INTO stl_links (id, user_email, broker, exchange, account_id, instruments, operations, enabled)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            new_id, body["userEmail"], body.get("broker", "finam"),
            body.get("exchange", "FORTS"), body["accountId"],
            body.get("instruments", []), body.get("operations", "RW"),
            body.get("enabled", True),
        )
        return {"id": new_id}

    # ── LAB: Robots ──────────────────────────────────────────────────
    @fastapi_app.get("/api/v1/robots")
    async def list_robots(request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        if pool is None:
            return []
        rows = await pool.fetch("SELECT * FROM robots ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    @fastapi_app.post("/api/v1/robots", status_code=201)
    async def create_robot(body: dict, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import json
        pool = request.app.state.db_pool
        new_id = cuid()
        await pool.execute(
            """INSERT INTO robots (id, user_email, stl_link_id, name, script_code, params_json, schedule)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            new_id, body["userEmail"], body["stlLinkId"], body["name"],
            body["scriptCode"], json.dumps(body.get("paramsJson", {})),
            body.get("schedule", "*/5 * * * *"),
        )
        return {"id": new_id}

    @fastapi_app.put("/api/v1/robots/{robot_id}")
    async def update_robot(robot_id: str, body: dict, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import json
        pool = request.app.state.db_pool
        await pool.execute(
            """UPDATE robots SET name=$1, script_code=$2, params_json=$3,
               schedule=$4, updated_at=now() WHERE id=$5""",
            body.get("name"), body.get("scriptCode"),
            json.dumps(body.get("paramsJson", {})),
            body.get("schedule", "*/5 * * * *"), robot_id,
        )
        return {"ok": True}

    @fastapi_app.post("/api/v1/robots/{robot_id}/deploy")
    async def deploy_robot(robot_id: str, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import json
        pool = request.app.state.db_pool
        await pool.execute(
            "UPDATE robots SET deployed=true, deployed_at=now() WHERE id=$1", robot_id
        )
        rows = await pool.fetch("SELECT * FROM robots WHERE id=$1", robot_id)
        if rows:
            from trader.lab.models import Robot
            robot = Robot(**{
                k: (json.loads(v) if k in ("params_json", "state_json") and isinstance(v, str) else v)
                for k, v in dict(rows[0]).items()
            })
            await request.app.state.scheduler.deploy_robot(robot)
        return {"ok": True}

    @fastapi_app.post("/api/v1/robots/{robot_id}/undeploy")
    async def undeploy_robot(robot_id: str, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        await pool.execute("UPDATE robots SET deployed=false WHERE id=$1", robot_id)
        await request.app.state.scheduler.stop_robot(robot_id)
        return {"ok": True}

    # ── LAB: Backtest ────────────────────────────────────────────────
    @fastapi_app.post("/api/v1/backtest/run", status_code=202)
    async def run_backtest(body: dict, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import json
        import asyncio as _asyncio
        pool = request.app.state.db_pool
        run_id = cuid()
        await pool.execute(
            """INSERT INTO backtest_runs (id, robot_id, params_grid, date_from, date_to)
               VALUES ($1,$2,$3,$4,$5)""",
            run_id, body["robotId"], json.dumps(body["paramsGrid"]),
            body["dateFrom"], body["dateTo"],
        )
        _asyncio.create_task(
            _run_backtest_task(run_id, body, pool, request.app.state)
        )
        return {"run_id": run_id}

    @fastapi_app.get("/api/v1/backtest/{run_id}/status")
    async def backtest_status(run_id: str, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        row = await pool.fetchrow(
            "SELECT status, error_msg, finished_at FROM backtest_runs WHERE id=$1", run_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")
        return dict(row)

    @fastapi_app.get("/api/v1/backtest/{run_id}/results")
    async def backtest_results(run_id: str, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        rows = await pool.fetch(
            "SELECT * FROM backtest_results WHERE run_id=$1 ORDER BY total_return DESC NULLS LAST",
            run_id,
        )
        return [dict(r) for r in rows]

    @fastapi_app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        log.error("api.unhandled_error", exc=str(exc), path=str(request.url))
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    return fastapi_app


app = create_app()
