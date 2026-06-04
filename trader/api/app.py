import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import structlog
from cuid2 import Cuid as _Cuid

def cuid() -> str:
    return _Cuid().generate()
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
        db_pool = get_pool()
        # Ensure cache tables exist (idempotent)
        from trader.lab.market_store import ensure_ohlcv_table, ensure_instrument_meta_table
        await ensure_ohlcv_table(db_pool)
        await ensure_instrument_meta_table(db_pool)
        # Columns for the remote-agent job queue (idempotent).
        for _ddl in (
            "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS engine TEXT DEFAULT 'local'",
            "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS symbol TEXT",
            "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS job_body JSONB",
            "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
            "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS agent_id TEXT",
        ):
            try:
                await db_pool.execute(_ddl)
            except Exception as _exc:
                log.warning("lab.backtest_runs_migrate_failed", ddl=_ddl, error=str(_exc))
        scheduler = RobotScheduler(
            db_pool=db_pool,
            tx_client=tx,
            pos_client=pos,
        )
        await scheduler.start()
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

    # A restart kills any in-flight local backtest task, but its DB row stays
    # status='running' forever (orphan) and — worse — a hung task holds the
    # in-process backtest lock. On a clean start there are no live local tasks,
    # so any leftover 'running' local run is an orphan: fail it so the UI stops
    # polling it and the catalog/leaderboard stay truthful.
    if db_pool is not None:
        try:
            await db_pool.execute(
                "UPDATE backtest_runs SET status='failed', error_msg='orphaned by restart', "
                "finished_at=now() WHERE engine='local' AND status='running'"
            )
        except Exception as exc:
            log.warning("startup.orphan_reset_failed", error=str(exc))

    # VDS fallback sweeper: drains queued remote sweeps locally (throttled) only when
    # the i9 agent is down. No-op while the agent claims jobs promptly.
    fallback_task = asyncio.create_task(_vds_fallback_sweeper(app.state))

    yield

    fallback_task.cancel()

    await scheduler.stop_all()
    if settings.lab_db_url:
        from trader.db import close_pool
        await close_pool()
    await hub.stop()
    await feed.aclose()
    await auth.aclose()


# Only ONE backtest/sweep runs at a time. Concurrent runs used to stack heavy
# subprocesses and drive the VDS load average into the hundreds; serializing them
# keeps the box responsive. Hard cap on combos as a second guardrail.
_BACKTEST_LOCK = asyncio.Lock()
_MAX_COMBOS = 2000

# ── VDS fallback sweeper ──────────────────────────────────────────────────────
# When the external i9 agent is DOWN, queued remote sweep jobs would sit forever.
# This drains them on the VDS itself, but ONLY with spare capacity, so it can never
# repeat the overload that knocked the box over: it skips while load is high (a
# resource ceiling), runs ONE job at a time under the shared backtest lock, the
# grid subprocess is already nice(19)+SCHED_IDLE+ionice, and combos are capped.
# Jobs the real agent claims (within seconds) never go stale, so this only fires
# when nothing is claiming — i.e. the i9 is unavailable.
_FB_ENABLED = os.environ.get("VDS_FALLBACK_ENABLED", "1") not in ("0", "false", "False")
_FB_POLL_SEC = int(os.environ.get("VDS_FALLBACK_POLL_SEC", "45"))
_FB_MAX_LOAD = float(os.environ.get("VDS_FALLBACK_MAX_LOAD", "2.0"))   # 4-core box; leaves headroom
_FB_STALE_SEC = int(os.environ.get("VDS_FALLBACK_STALE_SEC", "180"))   # untaken this long → agent down
_FB_MAX_COMBOS = int(os.environ.get("VDS_FALLBACK_MAX_COMBOS", "150")) # cap per job on the VDS


async def _run_backtest_task(run_id: str, body: dict, pool, app_state) -> None:
    import json
    import itertools
    from trader.lab.backtest import run_backtest_grid

    async with _BACKTEST_LOCK:
      try:
        await pool.execute(
            "UPDATE backtest_runs SET status='running' WHERE id=$1", run_id
        )
        # A run may carry its OWN script_code + base_params (e.g. opening a chart for
        # a Botstore library strategy that has no installed robot). Otherwise fall
        # back to the referenced robots row.
        script_code = body.get("scriptCode")
        base_params = body.get("baseParams")
        if not script_code or base_params is None:
            robot_row = await pool.fetchrow(
                "SELECT script_code, params_json FROM robots WHERE id=$1", body["robotId"]
            )
            script_code = script_code or robot_row["script_code"]
            if base_params is None:
                base_params = (
                    robot_row["params_json"]
                    if isinstance(robot_row["params_json"], dict)
                    else json.loads(robot_row["params_json"])
                )
        if isinstance(base_params, str):
            base_params = json.loads(base_params)
        # Hard timeout: an uncached symbol pulls months of 1-min bars from ISS, and a
        # slow/hung ISS response would otherwise pin this task (and the backtest lock)
        # forever, blocking every later local run and leaving the chart spinning.
        bars = await asyncio.wait_for(
            _fetch_bars_for_backtest(
                body.get("symbol", base_params.get("symbol", "")),
                body["dateFrom"], body["dateTo"], app_state,
            ),
            timeout=150,
        )
        grid = body.get("paramsGrid", {})
        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = list(itertools.product(*values))
        param_sets = [{**base_params, **dict(zip(keys, combo))} for combo in combos]
        # Cap applies to LOCAL (VDS) runs only; remote runs go to the powerful host.
        if body.get("engine") != "remote" and len(param_sets) > _MAX_COMBOS:
            raise ValueError(
                f"Слишком много комбинаций: {len(param_sets)} > {_MAX_COMBOS}. "
                f"Переключите движок на «Мощный хост» или сузьте сетку."
            )
        symbol = body.get("symbol", base_params.get("symbol", ""))

        # Real ruble economics: point_value (= step_price/min_step) from MOEX ISS,
        # cached in instrument_meta. Without it PnL is in index points, not rubles.
        point_value = 1.0
        try:
            from trader.lab.market_store import refresh_instrument_spec
            spec = await refresh_instrument_spec(pool, symbol)
            point_value = (spec or {}).get("point_value") or 1.0
        except Exception as exc:
            log.warning("backtest.point_value_failed", symbol=symbol, error=str(exc))

        # Run the whole grid in ONE subprocess (bars serialized once, not per combo).
        # Scale timeout with combo count.
        graded = await run_backtest_grid(
            script_code, bars, symbol, param_sets,
            timeout=max(120, 8 * len(param_sets)),
            point_value=point_value,
        )

        for entry in graded:
            if not entry.get("ok"):
                log.warning("backtest.combo_failed", error=entry.get("error"), params=entry.get("params"))
                continue
            params = entry["params"]
            result = entry["result"]
            res_id = cuid()
            await pool.execute(
                """INSERT INTO backtest_results
                   (id, run_id, params, trades, equity_curve, sharpe, max_drawdown, win_rate, total_return, total_trades)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                res_id, run_id,
                params, result["trades"],
                result["equity_curve"],
                result.get("sharpe"), result.get("max_drawdown"),
                result.get("win_rate"), result.get("total_return"),
                result.get("total_trades"),
            )

        await pool.execute(
            "UPDATE backtest_runs SET status='done', finished_at=now() WHERE id=$1",
            run_id,
        )
      except Exception as exc:
        await pool.execute(
            "UPDATE backtest_runs SET status='failed', error_msg=$1 WHERE id=$2",
            str(exc), run_id,
        )


def _campaign_score(r: dict) -> float:
    return (r.get("sharpe") or 0) + 3 * (r.get("total_return") or 0) - 2 * (r.get("max_drawdown") or 0)


def _campaign_candidate(r: dict) -> bool:
    return ((r.get("total_return") or 0) > 0 and (r.get("sharpe") or -9) >= 0.5
            and (r.get("max_drawdown") or 9) <= 0.15 and 30 <= (r.get("total_trades") or 0) <= 3000)


def _is_sweep_run(run_id: str) -> bool:
    """A sweep run (metrics-only, mirrored to the leaderboard): plain campaign
    (camp-...) or adaptive optimizer (opt-...). UI chart runs use a bare cuid."""
    return bool(run_id) and (run_id.startswith("camp-") or run_id.startswith("opt-"))


def _sweep_campaign(run_id: str) -> str | None:
    """Campaign id = first 3 dash-parts of a sweep run_id (e.g. opt-20260605-1200)."""
    if not _is_sweep_run(run_id):
        return None
    parts = run_id.split("-")
    return "-".join(parts[:3]) if len(parts) >= 3 else run_id


async def _run_remote_job_on_vds(row, app_state) -> None:
    """Compute one queued remote sweep job locally on the VDS, metrics-only into the
    leaderboard (same shape as the agent). Serialized + capped + idle-priority."""
    import itertools
    import json as _json
    import re as _re
    from trader.lab.backtest import run_backtest_grid

    pool = app_state.db_pool
    run_id = row["id"]
    symbol = row["symbol"] or ""
    try:
        async with _BACKTEST_LOCK:
            job = row["job_body"]
            if isinstance(job, str):
                job = _json.loads(job)
            symbol = symbol or job.get("symbol", "")
            script_code = job.get("script_code")
            base_params = job.get("base_params") or {}
            if isinstance(base_params, str):
                base_params = _json.loads(base_params)
            if not script_code:   # only campaign-style jobs (self-contained) are handled
                await pool.execute(
                    "UPDATE backtest_runs SET status='failed', error_msg='vds fallback: no script_code', "
                    "finished_at=now() WHERE id=$1", run_id)
                return

            # Explicit combos (random explore / unioned refine grids) take priority;
            # otherwise expand the product grid. Cap either way.
            ps_list = job.get("param_sets")
            if ps_list:
                param_sets = [{**base_params, **ps} for ps in ps_list][:_FB_MAX_COMBOS]
            else:
                grid = job.get("paramsGrid", {})
                keys = list(grid.keys())
                values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
                combos = list(itertools.product(*values))
                param_sets = [{**base_params, **dict(zip(keys, c))} for c in combos][:_FB_MAX_COMBOS]

            bars = await asyncio.wait_for(
                _fetch_bars_for_backtest(symbol, job.get("dateFrom"), job.get("dateTo"), app_state),
                timeout=150,
            )
            if not bars:
                await pool.execute(
                    "UPDATE backtest_runs SET status='failed', error_msg='vds fallback: no bars', "
                    "finished_at=now() WHERE id=$1", run_id)
                return

            point_value = 1.0
            try:
                from trader.lab.market_store import refresh_instrument_spec
                spec = await refresh_instrument_spec(pool, symbol)
                point_value = (spec or {}).get("point_value") or 1.0
            except Exception:
                pass

            graded = await run_backtest_grid(
                script_code, bars, symbol, param_sets,
                timeout=max(120, 8 * len(param_sets)), point_value=point_value,
            )

            m = _re.search(r"make_on_bar\('([a-z_]+)'\)", script_code or "")
            strat_id = m.group(1) if m else None
            campaign = _sweep_campaign(run_id)
            if strat_id:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        for entry in graded:
                            if not entry.get("ok"):
                                continue
                            r = entry["result"]
                            try:
                                await conn.execute(
                                    """INSERT INTO optimization_leaderboard
                                         (campaign_run, strategy, symbol, params, total_return, sharpe,
                                          max_drawdown, win_rate, total_trades, score, candidate,
                                          net_profit, recovery_factor)
                                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                                    campaign, strat_id, symbol, entry["params"],
                                    r.get("total_return"), r.get("sharpe"), r.get("max_drawdown"),
                                    r.get("win_rate"), r.get("total_trades"),
                                    _campaign_score(r), _campaign_candidate(r),
                                    r.get("net_profit"), r.get("recovery_factor"),
                                )
                            except Exception as exc:
                                log.warning("vds_fallback.leaderboard_insert_failed", run_id=run_id, error=str(exc))
            await pool.execute(
                "UPDATE backtest_runs SET status='done', finished_at=now() WHERE id=$1", run_id)
            log.info("vds_fallback.done", run_id=run_id, symbol=symbol, combos=len(param_sets))
    except Exception as exc:  # noqa: BLE001
        try:
            await pool.execute(
                "UPDATE backtest_runs SET status='failed', error_msg=$1, finished_at=now() WHERE id=$2",
                f"vds fallback: {exc}", run_id)
        except Exception:
            pass


async def _vds_fallback_sweeper(app_state) -> None:
    """Background loop: drain stale queued remote jobs on the VDS when the i9 agent is
    down — but only with spare capacity (load ceiling), one at a time, idle priority."""
    if not _FB_ENABLED:
        return
    pool = getattr(app_state, "db_pool", None)
    if pool is None:
        return
    await asyncio.sleep(60)   # let startup settle before doing any work
    while True:
        try:
            await asyncio.sleep(_FB_POLL_SEC)
            # Resource ceiling: only ever use spare CPU. Over the cap → wait.
            try:
                if os.getloadavg()[0] > _FB_MAX_LOAD:
                    continue
            except OSError:
                pass
            # Claim ONE remote job no agent has taken for _FB_STALE_SEC (→ agent down).
            row = await pool.fetchrow(
                """UPDATE backtest_runs SET status='running', claimed_at=now(), agent_id='vds-fallback'
                   WHERE id = (
                     SELECT id FROM backtest_runs
                     WHERE engine='remote' AND status='queued'
                       AND created_at < now() - make_interval(secs => $1::int)
                     ORDER BY created_at LIMIT 1
                     FOR UPDATE SKIP LOCKED)
                   RETURNING id, job_body, symbol""",
                _FB_STALE_SEC,
            )
            if not row:
                continue
            log.info("vds_fallback.claim", run_id=row["id"], symbol=row["symbol"])
            await _run_remote_job_on_vds(row, app_state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            log.warning("vds_fallback.loop_error", error=str(exc))


async def _fetch_bars_for_backtest(symbol: str, date_from: str, date_to: str, app_state) -> list:
    """
    Returns minute bars for backtest, ALWAYS fresh through the requested end date.

    The cache (ohlcv_bars) may stop short of date_to (a past campaign cached only up
    to its own end). We must never silently return a stale tail — the chart and the
    backtest both need data through "yesterday". So:
      1. Read the cache for the full range.
      2. If the cache is empty, or its newest bar is older than date_to, fetch the
         MISSING tail from MOEX ISS and upsert it.
      3. Return the now-complete range from cache.
    """
    from datetime import date as _date
    from trader.lab.iss_loader import load_bars_iss
    from trader.lab.market_store import get_bars, get_coverage, upsert_bars

    def _parse_date(s: str) -> _date:
        # accept ISO datetime or date string
        return _date.fromisoformat(s[:10])

    d_from = _parse_date(date_from)
    d_to = _parse_date(date_to)
    pool = getattr(app_state, "db_pool", None)

    if not pool:
        log.info("backtest.fetch_iss_nopool", symbol=symbol, from_date=str(d_from), to_date=str(d_to))
        return await load_bars_iss(symbol, d_from, d_to, interval=1)

    # 1. What do we already have?
    cov = await get_coverage(pool, symbol)
    cached_max = _date.fromisoformat(cov["max_date"]) if cov else None

    # 2. Decide what tail (if any) we must pull. Fetch from the day after the last
    #    cached bar (or the full range if nothing cached) up to d_to.
    need_from = None
    if cached_max is None:
        need_from = d_from
    elif cached_max < d_to:
        need_from = max(d_from, cached_max)   # overlap one day to avoid gaps
    if need_from is not None:
        log.info("backtest.fetch_iss", symbol=symbol, from_date=str(need_from), to_date=str(d_to))
        try:
            fresh = await load_bars_iss(symbol, need_from, d_to, interval=1)
            if fresh:
                await upsert_bars(pool, symbol, fresh)
        except Exception as exc:
            log.warning("backtest.fetch_iss_failed", symbol=symbol, error=str(exc))

    # 3. Return the complete range from cache.
    return await get_bars(pool, symbol, d_from, d_to)


async def _market_update_task(symbols: list[str], date_from: str, date_to: str, pool) -> None:
    """Background: download ISS bars for multiple symbols and cache in DB."""
    from datetime import date as _date
    from trader.lab.iss_loader import load_bars_iss
    from trader.lab.market_store import upsert_bars, ensure_ohlcv_table

    def _parse(s: str) -> _date:
        return _date.fromisoformat(s[:10])

    d_from = _parse(date_from)
    d_to = _parse(date_to)

    if pool:
        await ensure_ohlcv_table(pool)

    for sym in symbols:
        try:
            log.info("market.update.start", symbol=sym)
            bars = await load_bars_iss(sym, d_from, d_to, interval=1)
            if pool and bars:
                n = await upsert_bars(pool, sym, bars)
                log.info("market.update.done", symbol=sym, bars=n)
            else:
                log.warning("market.update.empty", symbol=sym)
        except Exception as exc:
            log.error("market.update.error", symbol=sym, error=str(exc))


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

    @fastapi_app.get("/api/v1/instruments/{symbol:path}/meta")
    async def get_instrument_meta_cached(symbol: str, request: Request):
        """
        DB-mirrored instrument meta (lot, price step, step value, initial margin).
        Returns cached row if present; otherwise fetches from Finam once, parses
        defensively, stores in instrument_meta, and returns it. Never 502s — if
        Finam is unavailable, returns whatever fields could be derived (or nulls).
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        from trader.lab.market_store import (
            ensure_instrument_meta_table, get_instrument_meta, upsert_instrument_meta,
        )
        pool = request.app.state.db_pool
        if pool is not None:
            await ensure_instrument_meta_table(pool)
            cached = await get_instrument_meta(pool, symbol)
            if cached and cached.get("initial_margin") is not None:
                return cached

        settings: Settings = request.app.state.settings
        auth_client: AsyncAuthClient = request.app.state.auth
        meta = {"symbol": symbol, "ticker": None, "name": None, "lot": None,
                "price_step": None, "price_step_value": None, "initial_margin": None, "raw": {}}

        def _num(v):
            if v is None:
                return None
            if isinstance(v, dict):
                v = v.get("value", v.get("units"))
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        try:
            token = await auth_client.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            account_id: str = request.app.state.account_id
            qp = {"account_id": account_id} if account_id else {}
            async with httpx.AsyncClient(http2=True) as client:
                # /params holds margin/step; /assets/{symbol} holds lot/ticker/name
                rp = await client.get(
                    f"{settings.finam_api_base_url}/v1/assets/{symbol}/params",
                    headers=headers, params=qp, timeout=10.0,
                )
                params_raw = rp.json() if rp.status_code == 200 else {}
                ra = await client.get(
                    f"{settings.finam_api_base_url}/v1/assets/{symbol}",
                    headers=headers, params=qp, timeout=10.0,
                )
                asset_raw = ra.json() if ra.status_code == 200 else {}

            meta["raw"] = {"params": params_raw, "asset": asset_raw}
            # Defensive field extraction across possible Finam shapes
            for key in ("initial_margin", "initialMargin", "imLong", "longInitialMargin",
                        "margin_buy", "marginBuy", "go", "guarantee"):
                val = _num(params_raw.get(key)) if isinstance(params_raw, dict) else None
                if val:
                    meta["initial_margin"] = val
                    break
            for src in (params_raw, asset_raw):
                if not isinstance(src, dict):
                    continue
                meta["price_step"] = meta["price_step"] or _num(src.get("min_step") or src.get("price_step") or src.get("step"))
                meta["price_step_value"] = meta["price_step_value"] or _num(src.get("step_price") or src.get("price_step_value") or src.get("step_value"))
                meta["lot"] = meta["lot"] or _num(src.get("lot_size") or src.get("lot"))
                meta["ticker"] = meta["ticker"] or src.get("ticker") or src.get("code")
                meta["name"] = meta["name"] or src.get("name") or src.get("short_name")
        except Exception as exc:
            log.warning("api.instrument_meta_fetch_failed", symbol=symbol, error=str(exc))

        if pool is not None:
            try:
                await upsert_instrument_meta(pool, meta)
            except Exception as exc:
                log.warning("api.instrument_meta_store_failed", symbol=symbol, error=str(exc))
        return meta

    # ── LAB: Strategy templates ──────────────────────────────────────
    @fastapi_app.get("/api/v1/strategies")
    async def list_strategies(request: Request):
        """Return available built-in strategy templates with param schemas."""
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        from trader.lab.strategies.donchian_breakout import STRATEGY_META as donchian_meta
        core = [
            {
                "id": "donchian_breakout",
                "name": donchian_meta["name"],
                "description": donchian_meta["description"],
                "source": donchian_meta["source"],
                "params_schema": donchian_meta["params_schema"],
                "script_code": "from trader.lab.strategies.donchian_breakout import on_bar, on_start, on_stop",
                "default_params": {p["key"]: p["default"] for p in donchian_meta["params_schema"]},
            },
            {
                "id": "ema_crossover",
                "name": "EMA Crossover",
                "description": "Покупка при пересечении быстрой EMA вверх, продажа при обратном кресте.",
                "source": "Built-in reference strategy",
                "params_schema": [
                    {"key": "symbol",      "label": "Инструмент",      "type": "text",   "default": "RIM6", "hint": "FORTS тикер"},
                    {"key": "fast_period", "label": "Быстрая EMA (N)", "type": "number", "default": 9,      "min": 2,  "max": 50,  "hint": "Период быстрой EMA"},
                    {"key": "slow_period", "label": "Медленная EMA (M)","type": "number", "default": 21,     "min": 5,  "max": 200, "hint": "Период медленной EMA"},
                ],
                "script_code": "from trader.lab.strategies.ema_crossover import on_bar, on_start, on_stop",
                "default_params": {"symbol": "RIM6", "fast_period": 9, "slow_period": 21},
            },
            {
                "id": "rsi_mean_reversion",
                "name": "RSI Mean Reversion",
                "description": "Покупка при перепроданности (RSI < 30), продажа при перекупленности (RSI > 70).",
                "source": "Built-in reference strategy",
                "params_schema": [
                    {"key": "symbol",     "label": "Инструмент",  "type": "text",   "default": "RIM6", "hint": "FORTS тикер"},
                    {"key": "period",     "label": "Период RSI",  "type": "number", "default": 14,     "min": 5,  "max": 100, "hint": "Период расчёта RSI"},
                    {"key": "oversold",   "label": "Перепродан",  "type": "number", "default": 30,     "min": 10, "max": 45,  "hint": "Вход: RSI ниже этого уровня"},
                    {"key": "overbought", "label": "Перекуплен",  "type": "number", "default": 70,     "min": 55, "max": 90,  "hint": "Выход: RSI выше этого уровня"},
                ],
                "script_code": "from trader.lab.strategies.rsi_mean_reversion import on_bar, on_start, on_stop",
                "default_params": {"symbol": "RIM6", "period": 14, "oversold": 30, "overbought": 70},
            },
            {
                "id": "supertrend",
                "name": "SuperTrend (ATR)",
                "description": (
                    "Трендследящая стратегия на полосах ATR (индикатор SuperTrend, Olivier Seban). "
                    "Строит верхнюю и нижнюю полосы вокруг средней цены на расстоянии множитель×ATR. "
                    "Когда цена пробивает верхнюю полосу — тренд считается восходящим, робот держит лонг; "
                    "пробой нижней полосы — нисходящий тренд, робот переворачивается в шорт. Всегда в рынке "
                    "после прогрева, торгует в обе стороны. Хорошо работает на трендовых движениях, теряет на "
                    "боковике (частые ложные перевороты)."
                ),
                "source": "https://github.com/jigneshpylab/ZerodhaPythonScripts",
                "params_schema": [
                    {"key": "symbol",     "label": "Инструмент",    "type": "text",   "default": "RIM6",
                     "hint": "FORTS тикер",
                     "desc": "Торгуемый фьючерс FORTS, например RIM6 (фьючерс на индекс РТС, июнь). "
                             "От инструмента зависят стоимость пункта и ГО."},
                    {"key": "atr_period", "label": "Период ATR",    "type": "number", "default": 10, "min": 5,  "max": 50,
                     "hint": "Окно расчёта ATR",
                     "desc": "Сколько баров берётся для расчёта средней истинной волатильности (ATR). "
                             "Меньше — полосы быстрее реагируют, больше сделок и шума; больше — "
                             "глаже, меньше ложных переворотов, но позже вход."},
                    {"key": "multiplier", "label": "Множитель ×10", "type": "number", "default": 30, "min": 10, "max": 60,
                     "hint": "Ширина полос = (множитель/10) × ATR",
                     "desc": "Во сколько ATR отстоят полосы от цены. Хранится ×10 (30 = 3.0). Меньше — "
                             "полосы ближе, чаще перевороты; больше — дальше, реже и крупнее сделки, "
                             "позиция дольше держится в тренде."},
                    {"key": "qty",        "label": "Контрактов",    "type": "number", "default": 1,  "min": 1,  "max": 10,
                     "hint": "Лотность на сделку",
                     "desc": "Сколько контрактов в одной сделке. Влияет на размер позиции, ГО и риск "
                             "пропорционально. Робот не усредняет — держит ровно qty в каждую сторону."},
                ],
                "script_code": "from trader.lab.strategies.supertrend import on_bar, on_start, on_stop",
                "default_params": {"symbol": "RIM6", "atr_period": 10, "multiplier": 30, "qty": 1},
            },
        ]
        # append the whole strategy LIBRARY (12+ classic robots)
        try:
            from trader.lab.strategies.library import list_strategies as _lib_list
            core.extend(_lib_list())
        except Exception as exc:
            log.error("api.library_list_failed", error=str(exc))
        return core

    @fastapi_app.get("/api/v1/forts-instruments")
    async def forts_instruments(request: Request):
        """Top FORTS futures by today's turnover (front contract per asset), from
        MOEX ISS (free). Used to populate the instrument dropdown in Backtest Lab."""
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import httpx as _httpx
        url = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
               "?iss.meta=off&iss.only=securities,marketdata"
               "&securities.columns=SECID,SHORTNAME,ASSETCODE,LASTTRADEDATE"
               "&marketdata.columns=SECID,VALTODAY")
        try:
            async with _httpx.AsyncClient(timeout=15.0,
                    headers={"User-Agent": "STL/1.0", "Accept": "application/json"}) as c:
                j = (await c.get(url)).json()
        except Exception as exc:
            log.warning("api.forts_instruments_failed", exc=str(exc))
            return []
        sec = j.get("securities", {})
        scols, sdata = sec.get("columns", []), sec.get("data", [])
        md = j.get("marketdata", {})
        mcols, mdata = md.get("columns", []), md.get("data", [])
        turnover = {}
        for row in mdata:
            r = dict(zip(mcols, row))
            turnover[r.get("SECID")] = r.get("VALTODAY") or 0
        # Keep, per ASSETCODE, the front contract (nearest LASTTRADEDATE) and sum
        # turnover across that asset's contracts so liquidity ranks the asset.
        by_asset: dict = {}
        for row in sdata:
            r = dict(zip(scols, row))
            secid, asset, ltd = r.get("SECID"), r.get("ASSETCODE"), r.get("LASTTRADEDATE")
            if not secid or not asset:
                continue
            vt = turnover.get(secid, 0) or 0
            cur = by_asset.get(asset)
            if cur is None:
                by_asset[asset] = {"front": secid, "front_ltd": ltd,
                                   "name": r.get("SHORTNAME", secid), "turnover": vt}
            else:
                cur["turnover"] += vt
                if ltd and (cur["front_ltd"] is None or ltd < cur["front_ltd"]):
                    cur["front"], cur["front_ltd"], cur["name"] = secid, ltd, r.get("SHORTNAME", secid)
        ranked = sorted(by_asset.values(), key=lambda x: x["turnover"], reverse=True)
        return [
            {"symbol": a["front"], "name": a["name"], "turnover": a["turnover"]}
            for a in ranked[:25] if a["turnover"] > 0
        ]

    # ── LAB: Botstore (robot catalog + leaderboard summary) ──────────
    @fastapi_app.get("/api/v1/botstore")
    async def botstore(request: Request):
        """
        Catalog of all portable robots with their best backtest result found
        during background optimization campaigns. One row per (strategy, symbol):
        best return on found params, # param variants tested, last run, period,
        drawdown, recovery factor, initial equity used.
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool

        # robot catalog (strategy templates)
        try:
            from trader.lab.strategies.library import list_strategies as _lib_list
            templates = _lib_list()
        except Exception:
            templates = []
        core_ids = {
            "ema_crossover": "EMA Crossover", "rsi_mean_reversion": "RSI Mean Reversion",
            "donchian_breakout": "Donchian Breakout", "supertrend": "SuperTrend (ATR)",
        }
        names = {t["id"]: t["name"] for t in templates}
        names.update(core_ids)

        rows = []
        if pool is not None:
            rows = await pool.fetch("""
                SELECT DISTINCT ON (strategy, symbol)
                       strategy, symbol, params, total_return, max_drawdown,
                       recovery_factor, sharpe, win_rate, total_trades, net_profit,
                       point_value, initial_margin, initial_equity, date_from, date_to,
                       created_at
                FROM optimization_leaderboard
                ORDER BY strategy, symbol, score DESC NULLS LAST
            """)
            counts = await pool.fetch("""
                SELECT strategy, count(*) AS variants, max(created_at) AS last_run
                FROM optimization_leaderboard GROUP BY strategy
            """)
        else:
            counts = []
        variants_by = {c["strategy"]: c["variants"] for c in counts}
        lastrun_by = {c["strategy"]: c["last_run"] for c in counts}

        best_by_strat: dict[str, list] = {}
        for r in rows:
            d = dict(r)
            for k in ("date_from", "date_to", "created_at"):
                if d.get(k) is not None:
                    d[k] = d[k].isoformat()
            best_by_strat.setdefault(r["strategy"], []).append(d)

        catalog = []
        all_ids = set(names) | set(variants_by) | set(best_by_strat)
        for sid in sorted(all_ids):
            lr = lastrun_by.get(sid)
            catalog.append({
                "id": sid,
                "name": names.get(sid, sid),
                "variants_tested": variants_by.get(sid, 0),
                "last_run": lr.isoformat() if lr else None,
                "results": best_by_strat.get(sid, []),
            })
        return {
            "initial_equity": 100000,
            "robots_count": len(catalog),
            "catalog": catalog,
        }

    @fastapi_app.get("/api/v1/botstore/{strategy}/results")
    async def botstore_strategy_results(strategy: str, request: Request):
        """
        FULL detail for one tested strategy: every (symbol × param-combo) row from the
        optimization leaderboard, so the UI can render per-instrument tables and let the
        user drill into any single combo. Also returns the campaign period so a drill-in
        chart knows the test window (its end is later extended to yesterday).
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        if pool is None:
            return {"strategy": strategy, "rows": [], "period": None}
        rows = await pool.fetch(
            """
            SELECT campaign_run, symbol, params, total_return, sharpe, max_drawdown,
                   win_rate, total_trades, net_profit, recovery_factor, score,
                   candidate, created_at
            FROM optimization_leaderboard
            WHERE strategy=$1
            ORDER BY symbol, score DESC NULLS LAST
            """,
            strategy,
        )
        out = []
        campaigns = set()
        for r in rows:
            d = dict(r)
            if d.get("created_at") is not None:
                d["created_at"] = d["created_at"].isoformat()
            if d.get("campaign_run"):
                campaigns.add(d["campaign_run"])
            out.append(d)

        period = None
        if campaigns:
            patterns = [c + "-%" for c in campaigns]
            prow = await pool.fetchrow(
                "SELECT MIN(date_from) AS df, MAX(date_to) AS dt FROM backtest_runs WHERE id LIKE ANY($1)",
                patterns,
            )
            if prow and prow["df"] and prow["dt"]:
                period = {"date_from": prow["df"].isoformat(), "date_to": prow["dt"].isoformat()}
        return {"strategy": strategy, "rows": out, "period": period}

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
        pool = request.app.state.db_pool
        new_id = cuid()
        await pool.execute(
            """INSERT INTO robots (id, user_email, stl_link_id, name, script_code, params_json, schedule)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            new_id, body["userEmail"], body["stlLinkId"], body["name"],
            body["scriptCode"], body.get("paramsJson", {}),
            body.get("schedule", "09:00-23:55"),
        )
        return {"id": new_id}

    @fastapi_app.put("/api/v1/robots/{robot_id}")
    async def update_robot(robot_id: str, body: dict, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        # Build a partial update — only set fields present in body.
        sets, args = [], []
        field_map = {
            "name": "name", "scriptCode": "script_code",
            "paramsJson": "params_json", "schedule": "schedule",
        }
        for body_key, col in field_map.items():
            if body_key in body:
                args.append(body[body_key])
                sets.append(f"{col}=${len(args)}")
        if not sets:
            return {"ok": True}
        sets.append("updated_at=now()")
        args.append(robot_id)
        await pool.execute(
            f"UPDATE robots SET {', '.join(sets)} WHERE id=${len(args)}",
            *args,
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

    @fastapi_app.delete("/api/v1/robots/{robot_id}")
    async def delete_robot(robot_id: str, request: Request):
        """Remove a robot from the platform: stop it, drop its FK-dependent rows
        (trades, metrics, backtest runs+results), then delete the robot itself."""
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        try:
            await request.app.state.scheduler.stop_robot(robot_id)
        except Exception:
            pass  # best-effort; robot may not be running
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM live_trades WHERE robot_id=$1", robot_id)
                await conn.execute("DELETE FROM live_metrics WHERE robot_id=$1", robot_id)
                # backtest_results reference backtest_runs.run_id → delete children first
                await conn.execute(
                    """DELETE FROM backtest_results WHERE run_id IN
                       (SELECT id FROM backtest_runs WHERE robot_id=$1)""", robot_id)
                await conn.execute("DELETE FROM backtest_runs WHERE robot_id=$1", robot_id)
                res = await conn.execute("DELETE FROM robots WHERE id=$1", robot_id)
        if res.endswith("0"):
            raise HTTPException(status_code=404, detail="Robot not found")
        return {"ok": True}

    @fastapi_app.get("/api/v1/robots/{robot_id}/live")
    async def robot_live(robot_id: str, request: Request):
        """
        Everything the robot detail window needs in one call:
        robot row, traded symbol, all recorded orders/fills (live_trades),
        ruble economics (point_value, initial_margin), paper/real flag, and a
        chart date range. Fills feed the chart markers + history table; the
        frontend computes round-trips and ruble equity from them.
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import json as _json
        from datetime import date as _date, timedelta as _td
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        row = await pool.fetchrow("SELECT * FROM robots WHERE id=$1", robot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Robot not found")
        robot = dict(row)

        def _as_dict(v):
            if isinstance(v, dict):
                return v
            if isinstance(v, str):
                try:
                    return _json.loads(v)
                except Exception:
                    return {}
            return {}

        params = _as_dict(robot.get("params_json"))
        state = _as_dict(robot.get("state_json"))
        symbol = params.get("symbol") or "RIM6"
        paper = not bool(state.get("live_real", False))
        # Planned operations the strategy published into its state (price triggers
        # where it intends to act next). Drawn as dotted lines in the robot window.
        planned_orders = state.get("plan") if isinstance(state.get("plan"), list) else []

        # Strategy template behind this robot (matched by script_code), so the
        # window can show param descriptions + a landing link. Best-effort.
        strategy = None
        try:
            templates = await list_strategies(request)
            code = robot.get("script_code") or ""
            strategy = next((t for t in templates if t.get("id") and t["id"] in code), None)
        except Exception:
            strategy = None

        trade_rows = await pool.fetch(
            """SELECT side, qty, price, order_id, status, timestamp
               FROM live_trades WHERE robot_id=$1 ORDER BY timestamp""",
            robot_id,
        )
        trades = [
            {
                "time": int(r["timestamp"].timestamp()),
                "iso": r["timestamp"].isoformat(),
                "side": r["side"],
                "qty": int(r["qty"]),
                "price": float(r["price"]),
                "order_id": r["order_id"],
                "status": r["status"],
            }
            for r in trade_rows
        ]

        # ruble economics from instrument_meta (cache; refresh once if missing)
        from trader.lab.market_store import (
            ensure_instrument_meta_table, get_instrument_meta, refresh_instrument_spec,
        )
        await ensure_instrument_meta_table(pool)
        meta = await get_instrument_meta(pool, symbol)
        if not meta or meta.get("point_value") is None:
            try:
                meta = await refresh_instrument_spec(pool, symbol)
            except Exception:
                meta = meta or {}
        point_value = (meta or {}).get("point_value") or 1.0
        initial_margin = (meta or {}).get("initial_margin")

        # Open (resting) orders the robot has placed on the exchange, drawn as
        # horizontal price lines on the chart. Paper mode fills instantly so there
        # are none; only real-mode resting orders appear. Best-effort: never 500.
        open_orders: list[dict] = []
        pos = getattr(request.app.state, "pos", None)
        if not paper and pos is not None:
            try:
                token = await pos._get_token()
                acc = pos._account_id
                fin_sym = symbol if "@" in symbol else f"{symbol}@RTSX"
                resp = await pos._http.get(
                    f"/v1/accounts/{acc}/orders",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10.0,
                )
                if resp.status_code == 200:
                    for o in (resp.json().get("orders") or []):
                        order = o.get("order") or {}
                        st = o.get("status", "")
                        if st not in ("ORDER_STATUS_NEW", "ORDER_STATUS_PENDING_NEW",
                                      "ORDER_STATUS_PARTIALLY_FILLED"):
                            continue
                        if order.get("symbol") not in (fin_sym, symbol):
                            continue
                        lp = (order.get("limit_price") or {}).get("value")
                        if lp is None:
                            continue
                        side = order.get("side", "")
                        open_orders.append({
                            "side": "buy" if "BUY" in side else "sell",
                            "price": float(lp),
                            "qty": int(float((order.get("quantity") or {}).get("value", 0) or 0)),
                            "order_id": o.get("order_id", ""),
                        })
            except Exception as exc:
                log.warning("api.robot_live_open_orders_failed", robot_id=robot_id, exc=str(exc))

        # chart date range: cover trades + recent context, clamp to today
        today = _date.today()
        if trades:
            # Start the chart one day before the first trade so the robot's
            # activity fills the view, instead of a 3-month wall of old candles.
            first_day = _date.fromtimestamp(trades[0]["time"])
            date_from = first_day - _td(days=1)
        else:
            # No trades yet — show the last ~10 days of context.
            date_from = today - _td(days=10)
        return {
            "robot": robot,
            "symbol": symbol,
            "paper": paper,
            "trades": trades,
            "point_value": point_value,
            "initial_margin": initial_margin,
            "open_orders": open_orders,
            "planned_orders": planned_orders,
            "strategy": strategy,
            "date_from": date_from.isoformat(),
            # +1 day so TODAY's intraday bars are included — date_to is parsed as
            # midnight, and `ts BETWEEN from AND to` would otherwise drop everything
            # after 00:00 today (leaving candles ending yesterday while trades go on).
            "date_to": (today + _td(days=1)).isoformat(),
        }

    # ── LAB: Backtest ────────────────────────────────────────────────
    @fastapi_app.post("/api/v1/backtest/run", status_code=202)
    async def run_backtest(body: dict, request: Request):
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import json
        import asyncio as _asyncio
        from datetime import datetime as _dt
        pool = request.app.state.db_pool
        run_id = cuid()

        def _parse_dt(s: str) -> _dt:
            return _dt.fromisoformat(s.replace("Z", "+00:00"))

        # engine: "local" → run on the VDS now (default); "remote" → enqueue for the
        # external Windows agent to pick up (keeps heavy sweeps off the VDS entirely).
        engine = body.get("engine", "local")
        symbol = body.get("symbol", "")
        # robot_id is a FK. An on-demand library run (scriptCode supplied, e.g. opening
        # a chart from the Botstore detail table) has no installed robot → resolve to
        # any valid robot row just to satisfy the constraint; script_code drives the run.
        robot_id = body.get("robotId")
        if body.get("scriptCode"):
            valid = await pool.fetchval("SELECT id FROM robots WHERE id=$1", robot_id) if robot_id else None
            if not valid:
                robot_id = await pool.fetchval("SELECT id FROM robots ORDER BY created_at LIMIT 1")
        if not robot_id:
            raise HTTPException(status_code=422, detail="robotId required (no robot available as FK)")
        await pool.execute(
            """INSERT INTO backtest_runs
                 (id, robot_id, params_grid, date_from, date_to, status, engine, symbol, job_body)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            run_id, robot_id, body.get("paramsGrid", {}),
            _parse_dt(body["dateFrom"]), _parse_dt(body["dateTo"]),
            ("queued" if engine == "remote" else "pending"),
            engine, symbol, json.dumps(body),
        )
        if engine != "remote":
            _asyncio.create_task(
                _run_backtest_task(run_id, body, pool, request.app.state)
            )
        return {"run_id": run_id, "engine": engine}

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

    # ── Optimization AGENT (external Windows host) ───────────────────────────
    def _agent_auth(request: Request) -> None:
        secret = request.app.state.settings.opt_agent_token.get_secret_value()
        if not secret:
            raise HTTPException(status_code=503, detail="Agent disabled (no token configured)")
        got = request.headers.get("x-agent-token", "")
        if got != secret:
            raise HTTPException(status_code=401, detail="Bad agent token")

    @fastapi_app.post("/api/v1/agent/claim")
    async def agent_claim(body: dict, request: Request):
        """Agent pulls the next queued remote run. Atomic claim via UPDATE..RETURNING
        so two agents never grab the same job. Returns the full job_body + run_id,
        plus the robot's script/base params and ruble economics so the agent is
        self-contained. 204 if nothing queued."""
        _agent_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        agent_id = body.get("agent_id", "agent")
        row = await pool.fetchrow(
            """UPDATE backtest_runs SET status='running', claimed_at=now(), agent_id=$1
               WHERE id = (
                 SELECT id FROM backtest_runs
                 WHERE engine='remote' AND status='queued'
                 ORDER BY created_at LIMIT 1
                 FOR UPDATE SKIP LOCKED)
               RETURNING id, robot_id, job_body, symbol""",
            agent_id,
        )
        if not row:
            return Response(status_code=204)
        import json as _json
        job = row["job_body"]
        if isinstance(job, str):
            job = _json.loads(job)
        # Campaign jobs carry their own script_code + base_params in job_body (so a
        # sweep can cover all library strategies without a robots row). UI jobs don't
        # → fall back to the robot referenced by robot_id.
        script_code = job.get("script_code")
        base_params = job.get("base_params")
        if not script_code or base_params is None:
            robot = await pool.fetchrow(
                "SELECT script_code, params_json FROM robots WHERE id=$1", row["robot_id"]
            )
            if robot:
                script_code = script_code or robot["script_code"]
                if base_params is None:
                    base_params = robot["params_json"] if isinstance(robot["params_json"], dict) else _json.loads(robot["params_json"])
        base_params = base_params or {}
        # ruble economics so the agent computes money-correct PnL
        point_value = 1.0
        try:
            from trader.lab.market_store import refresh_instrument_spec
            spec = await refresh_instrument_spec(pool, row["symbol"] or base_params.get("symbol", ""))
            point_value = (spec or {}).get("point_value") or 1.0
        except Exception:
            pass
        return {
            "run_id": row["id"],
            "symbol": row["symbol"] or base_params.get("symbol", ""),
            "script_code": script_code,
            "base_params": base_params,
            # Explicit combos (random explore / unioned refine grids) — when present
            # the agent runs them directly instead of expanding params_grid.
            "param_sets": job.get("param_sets"),
            "params_grid": job.get("paramsGrid", {}),
            "date_from": job.get("dateFrom"),
            "date_to": job.get("dateTo"),
            "point_value": point_value,
        }

    @fastapi_app.post("/api/v1/agent/result")
    async def agent_result(body: dict, request: Request):
        """Agent posts computed results for a run. Bulk-inserts backtest_results and
        marks the run done (or failed). Idempotent-ish: clears prior rows for the run."""
        _agent_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        run_id = body.get("run_id")
        if not run_id:
            raise HTTPException(status_code=422, detail="run_id required")
        if body.get("error"):
            await pool.execute(
                "UPDATE backtest_runs SET status='failed', error_msg=$1, finished_at=now() WHERE id=$2",
                str(body["error"]), run_id,
            )
            return {"ok": True, "status": "failed"}
        results = body.get("results", [])
        import json as _json
        # If this run is part of a campaign (job_body has script_code), also mirror
        # results into optimization_leaderboard so Botstore shows the hit-parade.
        meta = await pool.fetchrow("SELECT symbol, job_body FROM backtest_runs WHERE id=$1", run_id)
        strat_id = None
        campaign = None
        if meta and meta["job_body"]:
            jb = meta["job_body"]
            if isinstance(jb, str):
                jb = _json.loads(jb)
            sc = jb.get("script_code", "") or ""
            # script_code = "...make_on_bar('rsi_trend')" → extract the strategy id
            import re as _re
            m = _re.search(r"make_on_bar\('([a-z_]+)'\)", sc)
            strat_id = m.group(1) if m else None
            # sweep run_id = "<camp|opt>-YYYYMMDD-HHMM-..." → campaign = first 3 parts.
            campaign = _sweep_campaign(run_id)

        def _score(r):
            return (r.get("sharpe") or 0) + 3 * (r.get("total_return") or 0) - 2 * (r.get("max_drawdown") or 0)
        def _cand(r):
            return ((r.get("total_return") or 0) > 0 and (r.get("sharpe") or -9) >= 0.5
                    and (r.get("max_drawdown") or 9) <= 0.15 and 30 <= (r.get("total_trades") or 0) <= 3000)

        # Campaign runs (strat_id set) write ONLY the compact leaderboard row — the
        # bulky per-combo trades/equity arrays would hammer the small VDS Postgres
        # (this is what overloaded the box). A sweep run (camp-/opt-) stores metrics
        # only; a UI/chart run (bare cuid) keeps full backtest_results so the chart
        # can render trades + equity even though its script_code also has make_on_bar.
        is_campaign = bool(strat_id) and _is_sweep_run(run_id)
        async with pool.acquire() as conn:
            async with conn.transaction():
                if not is_campaign:
                    await conn.execute("DELETE FROM backtest_results WHERE run_id=$1", run_id)
                for entry in results:
                    if not entry.get("ok"):
                        continue
                    r = entry["result"]
                    if not is_campaign:
                        await conn.execute(
                            """INSERT INTO backtest_results
                               (id, run_id, params, trades, equity_curve, sharpe, max_drawdown, win_rate, total_return, total_trades)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                            cuid(), run_id, entry["params"],
                            r.get("trades", []), r.get("equity_curve", []),
                            r.get("sharpe"), r.get("max_drawdown"), r.get("win_rate"),
                            r.get("total_return"), r.get("total_trades"),
                        )
                    if strat_id:
                        try:
                            await conn.execute(
                                """INSERT INTO optimization_leaderboard
                                     (campaign_run, strategy, symbol, params, total_return, sharpe,
                                      max_drawdown, win_rate, total_trades, score, candidate,
                                      net_profit, recovery_factor)
                                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                                campaign, strat_id, meta["symbol"], entry["params"],
                                r.get("total_return"), r.get("sharpe"), r.get("max_drawdown"),
                                r.get("win_rate"), r.get("total_trades"), _score(r), _cand(r),
                                r.get("net_profit"), r.get("recovery_factor"),
                            )
                        except Exception as exc:
                            log.warning("agent.leaderboard_insert_failed", run_id=run_id, error=str(exc))
                await conn.execute(
                    "UPDATE backtest_runs SET status='done', finished_at=now() WHERE id=$1", run_id
                )
        return {"ok": True, "count": len(results)}

    # ── Market Data (MOEX ISS cache) ──────────────────────────────────────────

    @fastapi_app.post("/api/v1/market/update", status_code=202)
    async def market_update(body: dict, request: Request):
        """
        Trigger ISS download for a list of symbols and date range.
        Saves to ohlcv_bars DB cache. Runs in background.
        Body: { "symbols": ["RIM6","SIM6"], "dateFrom": "2026-01-01", "dateTo": "2026-05-01" }
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        import asyncio as _asyncio

        symbols = body.get("symbols", [])
        date_from = body.get("dateFrom", "")
        date_to = body.get("dateTo", "")
        if not symbols or not date_from or not date_to:
            raise HTTPException(status_code=422, detail="symbols, dateFrom, dateTo required")

        pool = request.app.state.db_pool
        _asyncio.create_task(
            _market_update_task(symbols, date_from, date_to, pool)
        )
        return {"status": "started", "symbols": symbols, "dateFrom": date_from, "dateTo": date_to}

    @fastapi_app.get("/api/v1/market/bars")
    async def market_bars(
        request: Request,
        symbol: str,
        date_from: str,
        date_to: str,
        resample_min: int = 60,   # aggregate 1-min bars into N-min candles
    ):
        """
        Return OHLCV bars from cache, resampled for display.
        resample_min=60 → hourly candles (good for 1-3 month view).
        resample_min=5  → 5-min candles.
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        from datetime import datetime as _dt
        pool = request.app.state.db_pool
        if pool is None:
            return []

        def _parse(s: str) -> _dt:
            return _dt.fromisoformat(s.replace("Z", "+00:00"))

        ts_from = _parse(date_from)
        ts_to   = _parse(date_to)

        # Epoch-based bucketing works for ANY bucket size (minutes..days),
        # unlike date_trunc('hour') which caps at 60-min granularity.
        bucket_secs = max(1, int(resample_min)) * 60
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    (FLOOR(EXTRACT(EPOCH FROM ts) / $4) * $4)::bigint AS bucket,
                    (array_agg(open  ORDER BY ts))[1]      AS open,
                    MAX(high)                              AS high,
                    MIN(low)                               AS low,
                    (array_agg(close ORDER BY ts DESC))[1] AS close,
                    SUM(volume)                            AS volume
                FROM ohlcv_bars
                WHERE symbol=$1 AND interval_min=1 AND ts BETWEEN $2 AND $3
                GROUP BY bucket
                ORDER BY bucket
                """,
                symbol, ts_from, ts_to, bucket_secs,
            )
        if rows:
            return [
                {"time": r["bucket"], "open": r["open"], "high": r["high"],
                 "low": r["low"], "close": r["close"], "volume": int(r["volume"])}
                for r in rows
            ]

        # Cache miss (e.g. range newer than last ISS sync). Fetch live from ISS
        # and resample in Python so the chart is never blank/stale. Same epoch
        # convention as the cache (ISS times stamped UTC), so markers stay aligned.
        try:
            from trader.lab.iss_loader import load_bars_iss
            iss_bars = await load_bars_iss(symbol, ts_from.date(), ts_to.date(), interval=1)
        except Exception as exc:
            log.warning("api.market_bars_iss_fallback_failed", symbol=symbol, exc=str(exc))
            return []
        buckets: dict[int, dict] = {}
        for b in iss_bars:
            if not (ts_from.timestamp() <= b.time <= ts_to.timestamp()):
                continue
            key = (b.time // bucket_secs) * bucket_secs
            agg = buckets.get(key)
            if agg is None:
                buckets[key] = {"time": key, "open": b.open, "high": b.high,
                                "low": b.low, "close": b.close, "volume": b.volume}
            else:
                agg["high"] = max(agg["high"], b.high)
                agg["low"] = min(agg["low"], b.low)
                agg["close"] = b.close
                agg["volume"] += b.volume
        return [buckets[k] for k in sorted(buckets)]

    @fastapi_app.get("/api/v1/market/coverage")
    async def market_coverage(request: Request, symbol: str | None = None):
        """
        Return available data coverage per symbol.
        Query param: ?symbol=RIM6 (optional, returns all if omitted).
        """
        require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
        pool = request.app.state.db_pool
        if pool is None:
            return []
        if symbol:
            from trader.lab.market_store import get_coverage
            cov = await get_coverage(pool, symbol)
            return [{"symbol": symbol, **cov}] if cov else []
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, interval_min,
                       MIN(ts)::date AS min_date,
                       MAX(ts)::date AS max_date,
                       COUNT(*) AS cnt
                FROM ohlcv_bars
                GROUP BY symbol, interval_min
                ORDER BY symbol
                """
            )
        return [dict(r) for r in rows]

    @fastapi_app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        log.error("api.unhandled_error", exc=str(exc), path=str(request.url))
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    return fastapi_app


app = create_app()
