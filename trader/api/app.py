import asyncio
import hmac
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


def _auth(request: Request) -> str:
    """Shorthand for the per-endpoint auth plumbing (returns the caller's email).

    Collapses the bridge-secret + request plumbing that was pasted into ~28 routes.
    """
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _validate_script_or_400(script_code: str | None) -> None:
    """Reject a strategy script with a forbidden construct before it is stored/run."""
    if not script_code:
        return
    from trader.lab.script_guard import ScriptValidationError, validate_script
    try:
        validate_script(script_code)
    except ScriptValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid strategy script: {exc}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    if not settings.shectory_auth_bridge_secret:
        from trader.auth.guard import _dev_bypass
        if _dev_bypass():
            log.warning("auth.dev_bypass_enabled",
                        msg="SHECTORY_AUTH_DEV_BYPASS set - all requests authenticate as debug")
        else:
            log.error("auth.no_bridge_secret",
                      msg="shectory_auth_bridge_secret empty - all protected routes will deny. "
                          "Set the secret, or SHECTORY_AUTH_DEV_BYPASS=1 for local dev.")
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
            # Columns the agent/optimizer writes into backtest_results for sweeps
            "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS net_profit DOUBLE PRECISION",
            "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS recovery_factor DOUBLE PRECISION",
            "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS point_value DOUBLE PRECISION",
            "ALTER TABLE robots ADD COLUMN IF NOT EXISTS retire_comment TEXT",
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

        # Agent control channel (self-update flag the agent polls). Bump update_token
        # to make the agent pull fresh code and re-exec itself.
        try:
            await db_pool.execute(
                "CREATE TABLE IF NOT EXISTS agent_control (key TEXT PRIMARY KEY, value TEXT)"
            )
        except Exception as exc:
            log.warning("startup.agent_control_table_failed", error=str(exc))

        # Generic agent task queue: dispatch ANY repo module.func to the i9's cores
        # without rebuilding the agent. A task carries module/func + args (a list ⇒
        # fanned across the agent's process pool). Lets the i9 run team-46 sweeps and
        # future offloaded compute via the same self-updating agent.
        try:
            await db_pool.execute(
                """CREATE TABLE IF NOT EXISTS agent_tasks (
                     id TEXT PRIMARY KEY,
                     kind TEXT DEFAULT 'task',
                     module TEXT NOT NULL,
                     func TEXT NOT NULL,
                     args JSONB,
                     status TEXT DEFAULT 'queued',
                     agent_id TEXT,
                     result JSONB,
                     error TEXT,
                     created_at TIMESTAMPTZ DEFAULT now(),
                     claimed_at TIMESTAMPTZ,
                     finished_at TIMESTAMPTZ)"""
            )
        except Exception as exc:
            log.warning("startup.agent_tasks_table_failed", error=str(exc))

    # AI46 (team-46) — privileged backend strategy in PAPER mode, env-gated.
    # OFF by default: a plain deploy is a no-op until AI46_ENABLED is set on the host.
    # Symbols: AI46_SYMBOLS (comma list) overrides; otherwise top-N FORTS front
    # contracts by today's turnover (AI46_SYMBOL_COUNT, default 20).
    ai46 = None
    if os.environ.get("AI46_ENABLED", "0") not in ("0", "", "false", "False"):
        try:
            from trader.lab.ai46.service import Ai46Service
            syms_env = os.environ.get("AI46_SYMBOLS", "").strip()
            if syms_env:
                ai46_symbols = [s.strip() for s in syms_env.split(",") if s.strip()]
            else:
                from trader.lab.iss_loader import top_instruments
                ai46_symbols = await top_instruments(int(os.environ.get("AI46_SYMBOL_COUNT", "20")))
            ai46 = Ai46Service(
                db_pool, auth.get_token, ai46_symbols,
                llm_enabled=os.environ.get("AI46_LLM_ENABLED", "1") not in ("0", "false", "False"),
                order_flow_live=os.environ.get("AI46_ORDER_FLOW", "1") not in ("0", "false", "False"),
            )
            await ai46.start()
            log.info("ai46.lifespan_started", symbols=ai46_symbols)
        except Exception as exc:
            log.error("ai46.lifespan_start_failed", error=str(exc))
            ai46 = None
    app.state.ai46 = ai46

    # VDS fallback sweeper: drains queued remote sweeps locally (throttled) only when
    # the i9 agent is down. No-op while the agent claims jobs promptly.
    fallback_task = asyncio.create_task(_vds_fallback_sweeper(app.state))
    # Self-heal: re-queue sweep jobs orphaned in 'running' by a dead claimer, so one
    # stuck job can't freeze a whole campaign (no r1/r2) the way it did overnight.
    reaper_task = asyncio.create_task(_orphan_reaper(app.state))

    yield

    fallback_task.cancel()
    reaper_task.cancel()

    if ai46 is not None:
        await ai46.stop()
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
_FB_STALE_SEC = int(os.environ.get("VDS_FALLBACK_STALE_SEC", "600"))   # untaken this long → agent down
# (10 min: ride out brief i9 network blips without loading the VDS — the agent
#  retries and resumes on its own; the VDS only takes over on a real outage.)
_FB_MAX_COMBOS = int(os.environ.get("VDS_FALLBACK_MAX_COMBOS", "150")) # cap per job on the VDS
_FB_MIN_FREE_MB = int(os.environ.get("VDS_FALLBACK_MIN_FREE_MB", "1200"))  # skip if RAM below this


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
        _sym = body.get("symbol", base_params.get("symbol", ""))
        try:
            bars = await asyncio.wait_for(
                _fetch_bars_for_backtest(_sym, body["dateFrom"], body["dateTo"], app_state),
                timeout=150,
            )
        except asyncio.TimeoutError:
            raise ValueError(
                f"загрузка истории {_sym} с MOEX ISS не уложилась в 150с "
                f"(инструмент не кэширован, медленный ответ ISS) — попробуйте ещё раз"
            )
        # Explicit combos (BacktestLab sweep: paramSets/param_sets) take priority;
        # otherwise expand the product grid (paramsGrid). Without this the VDS task
        # ignored an explicit sweep and ran a single base-params combo → 0 results.
        ps_list = body.get("paramSets") or body.get("param_sets")
        if ps_list:
            param_sets = [{**base_params, **ps} for ps in ps_list]
        else:
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
        initial_margin = 0.0
        try:
            from trader.lab.market_store import refresh_instrument_spec
            spec = await refresh_instrument_spec(pool, symbol)
            point_value = (spec or {}).get("point_value") or 1.0
            initial_margin = (spec or {}).get("initial_margin") or 0.0
        except Exception as exc:
            log.warning("backtest.point_value_failed", symbol=symbol, error=str(exc))

        # Run the whole grid in ONE subprocess (bars serialized once, not per combo).
        # Scale timeout with combo count.
        graded = await run_backtest_grid(
            script_code, bars, symbol, param_sets,
            timeout=max(120, 8 * len(param_sets)),
            point_value=point_value, initial_margin=initial_margin,
        )

        # Batch all combo rows into one executemany instead of one round-trip per
        # combo. A grid sweep produces hundreds of combos; per-row awaited INSERTs
        # held the backtest lock while hammering the small VDS Postgres.
        rows = []
        for entry in graded:
            if not entry.get("ok"):
                log.warning("backtest.combo_failed", error=entry.get("error"), params=entry.get("params"))
                continue
            params = entry["params"]
            result = entry["result"]
            rows.append((
                cuid(), run_id,
                params, result["trades"], result["equity_curve"],
                result.get("sharpe"), result.get("max_drawdown"),
                result.get("win_rate"), result.get("total_return"),
                result.get("total_trades"),
                result.get("net_profit"), result.get("recovery_factor"), point_value,
            ))
        if rows:
            await pool.executemany(
                """INSERT INTO backtest_results
                   (id, run_id, params, trades, equity_curve, sharpe, max_drawdown, win_rate,
                    total_return, total_trades, net_profit, recovery_factor, point_value)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                rows,
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


async def _sweep_run_stats(pool) -> dict:
    """Per-strategy sweep progress from backtest_runs: completion % of the LATEST
    campaign, machine time split i9 vs VDS-fallback (seconds), last run time. The
    strategy is the 2nd-to-last dash token of a sweep run_id (<camp|opt>-...-strat-sym)."""
    if pool is None:
        return {}
    runs = await pool.fetch(
        "SELECT id, status, agent_id, claimed_at, finished_at, created_at "
        "FROM backtest_runs WHERE id LIKE 'opt-%' OR id LIKE 'camp-%'"
    )
    from collections import defaultdict
    items: dict = defaultdict(list)
    for r in runs:
        parts = r["id"].split("-")
        if len(parts) < 5:
            continue
        items[parts[-2]].append(("-".join(parts[:3]), r))
    out: dict = {}
    for strat, lst in items.items():
        latest = max(c for c, _ in lst)
        cur = [r for c, r in lst if c == latest]
        total = len(cur)
        finished = sum(1 for r in cur if r["status"] in ("done", "failed"))
        i9_s = vds_s = 0.0
        last = None
        for _c, r in lst:
            ca, fi = r["claimed_at"], r["finished_at"]
            if ca and fi and fi > ca:
                secs = (fi - ca).total_seconds()
                if r["agent_id"] == "vds-fallback":
                    vds_s += secs
                elif r["agent_id"]:
                    i9_s += secs
            t = fi or r["created_at"]
            if t and (last is None or t > last):
                last = t
        out[strat] = {
            "pct": round(100 * finished / total) if total else 0,
            "finished": finished, "total": total, "campaign": latest,
            "machine_secs_i9": round(i9_s), "machine_secs_vds": round(vds_s),
            "last_run": last.isoformat() if last else None,
        }
    return out


def _agent_alive(app_state) -> bool:
    """In-memory heartbeat: an external agent (i9) hit /claim within 30s. Covers an
    IDLE agent (it polls /claim every few seconds). A BUSY agent computing a job does
    NOT poll meanwhile, so also check the DB — see _agent_alive_db."""
    t = getattr(app_state, "last_agent_seen", None)
    if t is None:
        return False
    try:
        return (asyncio.get_event_loop().time() - t) < 30
    except Exception:
        return False


async def _agent_alive_db(pool) -> bool:
    """DB signal: the i9 agent claimed a job in the last 5 min (covers a BUSY agent
    mid-computation that isn't polling /claim). agent_id 'vds-fallback' is the VDS
    itself, not the i9 — excluded."""
    if pool is None:
        return False
    try:
        return bool(await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM backtest_runs WHERE agent_id IS NOT NULL "
            "AND agent_id <> 'vds-fallback' AND claimed_at > now() - interval '5 minutes')"
        ))
    except Exception:
        return False


async def _agent_alive_any(app_state, pool) -> bool:
    """i9 alive if EITHER signal fires (idle poll heartbeat OR a recent job claim)."""
    return _agent_alive(app_state) or await _agent_alive_db(pool)


async def _agent_is_paused(pool, engine: str) -> bool:
    """Check if sweep engine (remote=i9, local=VDS) is paused via agent_control."""
    if pool is None:
        return False
    try:
        v = await pool.fetchval("SELECT value FROM agent_control WHERE key=$1", f"pause_{engine}")
        return v == "1"
    except Exception:
        return False


async def _agent_set_pause(pool, engine: str, paused: bool) -> None:
    """Set or clear the pause flag for an engine."""
    if pool is None:
        return
    key = f"pause_{engine}"
    if paused:
        await pool.execute(
            "INSERT INTO agent_control(key, value) VALUES($1, '1')"
            " ON CONFLICT (key) DO UPDATE SET value='1'",
            key)
    else:
        await pool.execute("DELETE FROM agent_control WHERE key=$1", key)


def _top3_by_netprofit(rows: list) -> list:
    """Top-3 instruments by best net profit, from per-symbol best rows."""
    ranked = sorted(
        rows, key=lambda d: (d.get("net_profit") if d.get("net_profit") is not None else -1e18),
        reverse=True,
    )[:3]
    return [{"symbol": d.get("symbol"), "net_profit": d.get("net_profit"),
             "total_return": d.get("total_return")} for d in ranked]


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
            initial_margin = 0.0
            try:
                from trader.lab.market_store import refresh_instrument_spec
                spec = await refresh_instrument_spec(pool, symbol)
                point_value = (spec or {}).get("point_value") or 1.0
                initial_margin = (spec or {}).get("initial_margin") or 0.0
            except Exception:
                pass

            graded = await run_backtest_grid(
                script_code, bars, symbol, param_sets,
                timeout=max(300, 30 * len(param_sets)), point_value=point_value,
                initial_margin=initial_margin, metrics_only=True,   # sweeps: never hold equity → no OOM
            )

            m = _re.search(r"make_on_bar\('([a-z0-9_]+)'\)", script_code or "")
            strat_id = m.group(1) if m else None
            campaign = _sweep_campaign(run_id)
            if strat_id:
                lb_rows = [
                    (
                        campaign, strat_id, symbol, entry["params"],
                        entry["result"].get("total_return"), entry["result"].get("sharpe"),
                        entry["result"].get("max_drawdown"), entry["result"].get("win_rate"),
                        entry["result"].get("total_trades"),
                        _campaign_score(entry["result"]), _campaign_candidate(entry["result"]),
                        entry["result"].get("net_profit"), entry["result"].get("recovery_factor"),
                        entry["result"].get("ann_return_go"), entry["result"].get("ann_return_full"),
                    )
                    for entry in graded if entry.get("ok")
                ]
                if lb_rows:
                    try:
                        async with pool.acquire() as conn:
                            await conn.executemany(
                                """INSERT INTO optimization_leaderboard
                                     (campaign_run, strategy, symbol, params, total_return, sharpe,
                                      max_drawdown, win_rate, total_trades, score, candidate,
                                      net_profit, recovery_factor, ann_return_go, ann_return_full)
                                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""",
                                lb_rows,
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
                f"vds fallback: {type(exc).__name__}: {exc}", run_id)
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
            # Memory ceiling: never let the fallback push the box toward OOM. If less
            # than _FB_MIN_FREE_MB is available, skip this round (the agent will catch up).
            try:
                with open("/proc/meminfo") as _mi:
                    avail_kb = next((int(ln.split()[1]) for ln in _mi if ln.startswith("MemAvailable")), 0)
                if avail_kb and avail_kb < _FB_MIN_FREE_MB * 1024:
                    log.warning("vds_fallback.low_memory_skip", avail_mb=avail_kb // 1024)
                    continue
            except Exception:
                pass
            # Honour pause flag for local/VDS engine — skip if paused.
            if await _agent_is_paused(pool, "local"):
                continue
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


async def _orphan_reaper(app_state) -> None:
    """Re-queue sweep jobs stuck in 'running' because their claimer (agent or fallback)
    died mid-job. A SINGLE orphan otherwise blocks the orchestrator from ever finishing
    a round -> no r1/r2, the whole campaign freezes (this stalled it overnight). A sweep
    job runs in well under a minute, so >8 min in 'running' means the claimer is gone.
    Re-queued jobs are then re-claimed by the agent (or the VDS fallback when it's down),
    so the campaign self-heals with no manual SQL."""
    pool = getattr(app_state, "db_pool", None)
    if pool is None:
        return
    await asyncio.sleep(90)
    while True:
        try:
            res = await pool.execute(
                "UPDATE backtest_runs SET status='queued', agent_id=NULL, claimed_at=NULL "
                "WHERE (id LIKE 'opt-%' OR id LIKE 'camp-%') AND status='running' "
                "AND (agent_id IS NULL OR agent_id <> 'vds-fallback') "
                "AND claimed_at < now() - interval '8 minutes'"
            )
            if res and res != "UPDATE 0":
                log.info("orphan_reaper.requeued", result=res)
            # VDS-fallback jobs are slow (full grid) — give them 90 min before reaping.
            res2 = await pool.execute(
                "UPDATE backtest_runs SET status='queued', agent_id=NULL, claimed_at=NULL "
                "WHERE (id LIKE 'opt-%' OR id LIKE 'camp-%') AND status='running' "
                "AND agent_id = 'vds-fallback' "
                "AND claimed_at < now() - interval '90 minutes'"
            )
            if res2 and res2 != "UPDATE 0":
                log.info("orphan_reaper.vds_fallback_requeued", result=res2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            log.warning("orphan_reaper.failed", error=str(exc))
        await asyncio.sleep(180)


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
        # Do NOT return the token in the body. The session lives in the HttpOnly
        # cookie set above; returning it would let the SPA stash it in localStorage,
        # which defeats HttpOnly and exposes a 30-day token to any XSS.
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
        _auth(request)
        try:
            return await request.app.state.tx.place_order(body)
        except _httpx.HTTPStatusError as exc:
            # Log the raw broker response server-side only; do not reflect it to the
            # client verbatim (it can carry internal request/account context). Return
            # just the broker's curated `message` field, or a generic fallback.
            log.warning("orders.broker_error", status=exc.response.status_code,
                        body=exc.response.text)
            msg = "Broker rejected the order"
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
        _auth(request)
        return await request.app.state.pos.get_portfolio()

    @fastapi_app.get("/api/v1/instruments")
    async def list_instruments(request: Request):
        """Instrument dropdown for the main chart. Primary source is MOEX ISS (free):
        the front contract per FORTS asset, ranked by today's turnover, with @RTSX
        appended to match the Finam streaming symbol format. ISS is always current, so
        the list survives quarterly contract expiration automatically (the old Finam
        /v1/assets path returned an expired set and 502'd, leaving an empty dropdown).
        Falls back to Finam /v1/assets if ISS is unreachable; never 502s the dropdown."""
        _auth(request)
        import httpx as _httpx
        url = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
               "?iss.meta=off&iss.only=securities,marketdata"
               "&securities.columns=SECID,SHORTNAME,ASSETCODE,LASTTRADEDATE"
               "&marketdata.columns=SECID,VALTODAY")
        try:
            async with _httpx.AsyncClient(timeout=15.0,
                    headers={"User-Agent": "STL/1.0", "Accept": "application/json"}) as c:
                j = (await c.get(url)).json()
            sec = j.get("securities", {})
            scols, sdata = sec.get("columns", []), sec.get("data", [])
            md = j.get("marketdata", {})
            mcols, mdata = md.get("columns", []), md.get("data", [])
            turnover = {}
            for row in mdata:
                r = dict(zip(mcols, row))
                turnover[r.get("SECID")] = r.get("VALTODAY") or 0
            # Front contract per asset (nearest LASTTRADEDATE), turnover summed per asset.
            by_asset: dict = {}
            for row in sdata:
                r = dict(zip(scols, row))
                secid, asset, ltd = r.get("SECID"), r.get("ASSETCODE"), r.get("LASTTRADEDATE")
                if not secid or not asset:
                    continue
                vt = turnover.get(secid, 0) or 0
                cur = by_asset.get(asset)
                if cur is None:
                    by_asset[asset] = {"front": secid, "ltd": ltd,
                                       "name": r.get("SHORTNAME", secid), "turnover": vt}
                else:
                    cur["turnover"] += vt
                    if ltd and (cur["ltd"] is None or ltd < cur["ltd"]):
                        cur["front"], cur["ltd"], cur["name"] = secid, ltd, r.get("SHORTNAME", secid)
            ranked = sorted(by_asset.values(), key=lambda x: x["turnover"], reverse=True)
            instruments = [
                {"symbol": f"{a['front']}@RTSX", "ticker": a["front"], "name": a["name"]}
                for a in ranked if a["front"]
            ][:60]
            if instruments:
                return {"instruments": instruments}
        except Exception as exc:
            log.warning("api.instruments_iss_failed", exc=str(exc))

        # Fallback: Finam /v1/assets (@RTSX filter) — the original behaviour.
        settings: Settings = request.app.state.settings
        auth_client: AsyncAuthClient = request.app.state.auth
        try:
            token = await auth_client.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(http2=True) as client:
                resp = await client.get(
                    f"{settings.finam_api_base_url}/v1/assets",
                    headers=headers, timeout=10.0,
                )
                resp.raise_for_status()
                body = resp.json()
            instruments = [
                {"symbol": a.get("symbol", ""), "ticker": a.get("ticker", a.get("code", "")),
                 "name": a.get("name", a.get("short_name", ""))}
                for a in body.get("assets", []) if "@RTSX" in a.get("symbol", "")
            ]
            return {"instruments": instruments}
        except Exception as exc:
            log.error("api.instruments_error", exc=str(exc))
            return {"instruments": []}   # never 502 — chart keeps working with current symbol

    @fastapi_app.get("/api/v1/instruments/{symbol:path}/params")
    async def get_instrument_params(symbol: str, request: Request):
        _auth(request)
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
        _auth(request)
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

    # ── LAB: fee model (single source of truth for the frontend) ─────
    @fastapi_app.get("/api/v1/lab/fee-config")
    async def lab_fee_config(request: Request):
        """Expose the FORTS commission constants so the frontend renders commission
        with the SAME model as the backend instead of a hand-copied duplicate."""
        _require_any_auth(request)
        from trader.lab.commission import fee_config
        return fee_config()

    # ── LAB: Strategy templates ──────────────────────────────────────
    @fastapi_app.get("/api/v1/strategies")
    async def list_strategies(request: Request):
        """Return available built-in strategy templates with param schemas."""
        _require_any_auth(request)
        from trader.lab.strategies.donchian_breakout import STRATEGY_META as donchian_meta
        core = [
            {
                "id": "donchian_breakout",
                "name": donchian_meta["name"],
                "description": (
                    "Donchian Channel Breakout — стратегия Turtle Trading System 1 (Dennis & Eckhardt, 1983).\n\n"
                    "КАК РАБОТАЕТ:\n"
                    "1. Вычисляется канал Дончиана: максимум за entry_period баров (N=20) и минимум за exit_period баров (M=10).\n"
                    "2. ВХОД: когда цена закрытия пробивает максимум за N баров → покупаем (лонг).\n"
                    "3. ВЫХОД: когда цена закрытия падает ниже минимума за M баров → продаём, закрываем позицию.\n"
                    "4. Только лонг (нет шорта) — классические правила Черепах.\n\n"
                    "ЛОГИКА СДЕЛОК:\n"
                    "• Нет позиции + цена выше N-барового максимума → открываем лонг (qty контрактов).\n"
                    "• Есть лонг + цена ниже M-барового минимума → закрываем лонг.\n"
                    "• В остальное время — держим или ждём.\n\n"
                    "ДЛЯ ЧЕГО: ловит долгосрочные тренды через пробой канала. Простая и надёжная\n"
                    "механика, проверенная десятилетиями. Только лонг — для FORTS нужен\n"
                    "отдельный доступ к шортам."
                ),
                "source": donchian_meta["source"],
                "params_schema": donchian_meta["params_schema"],
                "script_code": "from trader.lab.strategies.donchian_breakout import on_bar, on_start, on_stop",
                "default_params": {p["key"]: p["default"] for p in donchian_meta["params_schema"]},
            },
            {
                "id": "ema_crossover",
                "name": "EMA Crossover",
                "description": (
                    "EMA Crossover — трендовая стратегия на пересечении двух скользящих средних.\n\n"
                    "КАК РАБОТАЕТ:\n"
                    "1. Вычисляются быстрая EMA (fast_period, например 9) и медленная (slow_period, например 21).\n"
                    "2. Когда быстрая пересекает медленную СНИЗУ ВВЕРХ → сигнал в лонг (покупка).\n"
                    "3. Когда быстрая пересекает медленную СВЕРХУ ВНИЗ → сигнал в шорт (продажа).\n"
                    "4. При смене сигнала закрывает текущую позицию и открывает новую в противоположную сторону.\n\n"
                    "ЛОГИКА СДЕЛОК:\n"
                    "• Всегда в рынке — либо лонг, либо шорт.\n"
                    "• Пересечение = переворот позиции.\n\n"
                    "ДЛЯ ЧЕГО: классический тренд-фолловер. Хорошо работает на направленных движениях;\n"
                    "на боковике часто переворачивается, теряя на комиссии."
                ),
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
                "description": (
                    "RSI Mean Reversion — контртрендовая на возврате от экстремумов RSI.\n\n"
                    "КАК РАБОТАЕТ:\n"
                    "1. Вычисляется RSI(period) — индикатор перекупленности/перепроданности (0-100).\n"
                    "2. Если RSI < oversold (например 30) → инструмент перепродан, покупаем (лонг).\n"
                    "3. Если RSI > overbought (например 70) → инструмент перекуплен, продаём (шорт).\n"
                    "4. Если RSI между уровнями → держим текущую позицию.\n\n"
                    "ЛОГИКА СДЕЛОК:\n"
                    "• Сигнал=1 (RSI ниже перепроданности): закрывает шорт, открывает лонг.\n"
                    "• Сигнал=−1 (RSI выше перекупленности): закрывает лонг, открывает шорт.\n"
                    "• Сигнал=0 (RSI в середине): закрывает любую позицию.\n\n"
                    "ДЛЯ ЧЕГО: ловит развороты когда рынок «перегрет». Хорошо в боковике;\n"
                    "в сильном тренде RSI может долго оставаться перекупленным/перепроданным."
                ),
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
                    "SuperTrend — трендследящая стратегия на полосах ATR (индикатор Olivier Seban).\n\n"
                    "КАК РАБОТАЕТ:\n"
                    "1. Вычисляется базовая линия = (максимум + минимум) / 2 предыдущего бара.\n"
                    "2. Вычисляется ATR(atr_period) — средняя волатильность за N баров.\n"
                    "3. Верхняя полоса = базовая + mult×ATR. Нижняя полоса = базовая − mult×ATR.\n"
                    "4. Цена закрытия выше верхней полосы → тренд ВВЕРХ, переворот в лонг.\n"
                    "5. Цена закрытия ниже нижней полосы → тренд ВНИЗ, переворот в шорт.\n"
                    "6. Между полосами — тренд не меняется, держим позицию.\n\n"
                    "ЛОГИКА СДЕЛОК:\n"
                    "• Переворот из шорта в лонг: закрывает шорт, открывает лонг (qty контрактов).\n"
                    "• Переворот из лонга в шорт: закрывает лонг, открывает шорт.\n"
                    "• Состояние тренда СОХРАНЯЕТСЯ между барами — нет повторных входов.\n"
                    "• Показывает плановые уровни переворота в окне робота.\n\n"
                    "ДЛЯ ЧЕГО: всегда в рынке (после прогрева), торгует в обе стороны. Хорошо\n"
                    "на трендовых движениях; на боковике — частые ложные перевороты."
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
        # Enrich ALL params with human-readable desc from the shared dictionary if the
        # strategy author didn't provide one. Makes param tooltips consistent.
        try:
            from trader.lab.strategies.library import PARAM_DESC as _pd
        except Exception:
            _pd = {}
        for s in core:
            for p in s.get("params_schema", []):
                if not p.get("desc") and _pd.get(p["key"]):
                    p["desc"] = _pd[p["key"]]
        return core

    @fastapi_app.get("/api/v1/forts-instruments")
    async def forts_instruments(request: Request):
        """Top FORTS futures by today's turnover (front contract per asset), from
        MOEX ISS (free). Used to populate the instrument dropdown in Backtest Lab."""
        _auth(request)
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
        _auth(request)
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
        descs = {t["id"]: t.get("description", "") for t in templates}
        sources = {t["id"]: t.get("source", "") for t in templates}
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

        # Adaptive sweep progress + machine time per strategy (from backtest_runs).
        sweep_by = await _sweep_run_stats(pool)

        catalog = []
        all_ids = set(names) | set(variants_by) | set(best_by_strat)
        for sid in sorted(all_ids):
            lr = lastrun_by.get(sid)
            catalog.append({
                "id": sid,
                "name": names.get(sid, sid),
                "description": descs.get(sid, ""),
                "source": sources.get(sid, ""),
                "variants_tested": variants_by.get(sid, 0),
                "last_run": lr.isoformat() if lr else None,
                "results": best_by_strat.get(sid, []),
                "sweep": sweep_by.get(sid),                       # % done, machine time, last
                "top3": _top3_by_netprofit(best_by_strat.get(sid, [])),  # hit-parade
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
        _auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            return {"strategy": strategy, "rows": [], "period": None}
        rows = await pool.fetch(
            """
            SELECT campaign_run, symbol, params, total_return, sharpe, max_drawdown,
                   win_rate, total_trades, net_profit, recovery_factor, score,
                   candidate, created_at, ann_return_go, ann_return_full
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

        # Sweep progress/machine-time for this strategy + top-3 instruments by net profit
        # (best row per symbol).
        sweep = (await _sweep_run_stats(pool)).get(strategy)
        best_per_sym: dict = {}
        for d in out:
            sym = d.get("symbol")
            cur = best_per_sym.get(sym)
            if cur is None or (d.get("net_profit") or -1e18) > (cur.get("net_profit") or -1e18):
                best_per_sym[sym] = d
        top3 = _top3_by_netprofit(list(best_per_sym.values()))
        return {"strategy": strategy, "rows": out, "period": period, "sweep": sweep, "top3": top3}

    @fastapi_app.get("/api/v1/agent/activity")
    async def agent_activity(request: Request):
        """Live picture of the background optimizer for the Botstore panel: is the i9
        agent online, the current campaign's progress, throughput, and the last few
        processed jobs. So the user can watch the headless agent from the web UI."""
        _auth(request)
        pool = request.app.state.db_pool
        out = {"online": False, "agent_id": None, "last_seen": None, "vds_fallback": False,
               "vds_load": None, "campaign": None, "current": None, "counts": {}, "pct": 0,
               "throughput_per_min": 0, "recent": [], "paused_remote": False, "paused_local": False}
        try:
            la = os.getloadavg()
            out["vds_load"] = round(la[0], 2)
        except Exception:
            pass
        if pool is None:
            return out
        out["online"] = await _agent_alive_any(request.app.state, pool)
        out["paused_remote"] = await _agent_is_paused(pool, "remote")
        out["paused_local"] = await _agent_is_paused(pool, "local")
        # last claim by the real i9 agent + by the VDS fallback
        i9 = await pool.fetchrow(
            "SELECT agent_id, max(claimed_at) AS t FROM backtest_runs "
            "WHERE agent_id IS NOT NULL AND agent_id <> 'vds-fallback' GROUP BY agent_id ORDER BY t DESC LIMIT 1")
        if i9:
            out["agent_id"] = i9["agent_id"]
            out["last_seen"] = i9["t"].isoformat() if i9["t"] else None
        fb = await pool.fetchval(
            "SELECT max(claimed_at) FROM backtest_runs WHERE agent_id='vds-fallback' "
            "AND claimed_at > now() - interval '3 minutes'")
        out["vds_fallback"] = fb is not None

        rows = await pool.fetch(
            "SELECT id, status, agent_id, finished_at FROM backtest_runs "
            "WHERE id LIKE 'opt-%' OR id LIKE 'camp-%' ORDER BY created_at DESC LIMIT 500")
        if rows:
            latest = _sweep_campaign(rows[0]["id"])
            out["campaign"] = latest
            cur = [r for r in rows if _sweep_campaign(r["id"]) == latest]
            from collections import Counter
            c = Counter(r["status"] for r in cur)
            counts = {k: c.get(k, 0) for k in ("done", "failed", "queued", "running")}
            out["counts"] = counts
            tot = sum(counts.values())
            out["pct"] = round(100 * (counts["done"] + counts["failed"]) / tot) if tot else 0
            # what is being swept right now (strategy + instrument of the running job)
            run_now = next((r for r in cur if r["status"] == "running"), None)
            if run_now:
                pn = run_now["id"].split("-")
                out["current"] = {"strategy": pn[-2] if len(pn) >= 2 else "",
                                  "symbol": pn[-1] if pn else ""}
            # throughput: jobs finished in the last minute (any sweep)
            import datetime as _dt
            now = _dt.datetime.now(_dt.timezone.utc)
            out["throughput_per_min"] = sum(
                1 for r in rows if r["finished_at"] and (now - r["finished_at"]).total_seconds() <= 60)
            # recent finished jobs (strategy/symbol parsed from run_id: ...-strat-sym)
            fin = [r for r in rows if r["finished_at"]]
            fin.sort(key=lambda r: r["finished_at"], reverse=True)
            for r in fin[:8]:
                parts = r["id"].split("-")
                out["recent"].append({
                    "strategy": parts[-2] if len(parts) >= 2 else r["id"],
                    "symbol": parts[-1] if len(parts) >= 1 else "",
                    "status": r["status"],
                    "agent": "i9" if (r["agent_id"] and r["agent_id"] != "vds-fallback") else
                             ("VDS" if r["agent_id"] == "vds-fallback" else "?"),
                    "finished_at": r["finished_at"].isoformat(),
                })
        return out

    @fastapi_app.get("/api/v1/agent/campaign")
    async def agent_campaign(request: Request, id: str = ""):
        """Detail of one sweep campaign for the click-to-expand view: meta (strategies,
        instruments, rounds), the sampling spec, and a 2D density heatmap of the swept
        results over total_return x recovery_factor (from optimization_leaderboard)."""
        _auth(request)
        import json as _json
        pool = request.app.state.db_pool
        out = {"campaign": id, "combos": 0, "strategies": [], "symbols": [], "rounds": [],
               "return_range": None, "rf_range": None, "grid": [], "grid_w": 0, "grid_h": 0,
               "max_count": 0, "best": [], "started": None}
        if pool is None or not id:
            return out
        rows = await pool.fetch(
            "SELECT total_return, recovery_factor, net_profit, sharpe, max_drawdown, "
            "strategy, symbol, params, created_at FROM optimization_leaderboard "
            "WHERE campaign_run=$1 LIMIT 80000", id)
        out["combos"] = len(rows)
        if not rows:
            return out
        out["strategies"] = sorted({r["strategy"] for r in rows if r["strategy"]})
        out["symbols"] = sorted({r["symbol"] for r in rows if r["symbol"]})
        out["started"] = min(r["created_at"] for r in rows).isoformat()
        rounds_rows = await pool.fetch(
            "SELECT DISTINCT split_part(id,'-',4) AS rd FROM backtest_runs WHERE id LIKE $1", id + "-%")
        out["rounds"] = sorted([x["rd"] for x in rounds_rows if x["rd"] and x["rd"].startswith("r")])

        rets = sorted(r["total_return"] for r in rows if r["total_return"] is not None)
        rfs = sorted(r["recovery_factor"] for r in rows if r["recovery_factor"] is not None)

        def _pct(arr, q):
            if not arr:
                return 0.0
            return arr[min(len(arr) - 1, max(0, int(q * (len(arr) - 1))))]

        # clip axes to 2nd..98th percentile so a few blow-ups don't squash the map
        r_lo, r_hi = _pct(rets, 0.02), _pct(rets, 0.98)
        f_lo, f_hi = _pct(rfs, 0.02), _pct(rfs, 0.98)
        if r_hi <= r_lo:
            r_hi = r_lo + 1e-9
        if f_hi <= f_lo:
            f_hi = f_lo + 1e-9
        GW, GH = 24, 16

        def _bin(v, lo, hi, n):
            k = int((v - lo) / (hi - lo) * n)
            return 0 if k < 0 else (n - 1 if k >= n else k)

        grid = [[0] * GW for _ in range(GH)]
        for r in rows:
            if r["total_return"] is None or r["recovery_factor"] is None:
                continue
            col = _bin(r["total_return"], r_lo, r_hi, GW)
            # RF row 0 = TOP of the heatmap = highest RF, so invert
            row = GH - 1 - _bin(r["recovery_factor"], f_lo, f_hi, GH)
            grid[row][col] += 1
        out["grid"] = grid
        out["grid_w"], out["grid_h"] = GW, GH
        out["max_count"] = max((max(g) for g in grid), default=0)
        out["return_range"] = [r_lo, r_hi]
        out["rf_range"] = [f_lo, f_hi]

        best = sorted([r for r in rows if r["net_profit"] is not None],
                      key=lambda r: r["net_profit"], reverse=True)[:6]
        for r in best:
            p = r["params"]
            if isinstance(p, str):
                p = _json.loads(p)
            out["best"].append({
                "strategy": r["strategy"], "symbol": r["symbol"],
                "total_return": r["total_return"], "recovery_factor": r["recovery_factor"],
                "net_profit": r["net_profit"], "params": p,
            })
        return out

    # ── LAB: STL Links ───────────────────────────────────────────────
    @fastapi_app.get("/api/v1/stl-links")
    async def list_stl_links(request: Request):
        _auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            return []
        rows = await pool.fetch("SELECT * FROM stl_links ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    @fastapi_app.post("/api/v1/stl-links", status_code=201)
    async def create_stl_link(body: dict, request: Request):
        _auth(request)
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
        _auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            return []
        rows = await pool.fetch("SELECT * FROM robots ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    @fastapi_app.post("/api/v1/robots", status_code=201)
    async def create_robot(body: dict, request: Request):
        _auth(request)
        _validate_script_or_400(body.get("scriptCode"))
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
        _auth(request)
        pool = request.app.state.db_pool
        # Build a partial update — only set fields present in body.
        sets, args = [], []
        field_map = {
            "name": "name", "scriptCode": "script_code",
            "paramsJson": "params_json", "schedule": "schedule",
        }
        if "scriptCode" in body:
            _validate_script_or_400(body["scriptCode"])
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
        _auth(request)
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
        _auth(request)
        pool = request.app.state.db_pool
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        comment = body.get("comment") or None
        await pool.execute(
            "UPDATE robots SET deployed=false, retire_comment=$2 WHERE id=$1",
            robot_id, comment,
        )
        await request.app.state.scheduler.stop_robot(robot_id)
        return {"ok": True}

    @fastapi_app.get("/api/v1/robots/showcase")
    async def robots_showcase(request: Request):
        """All deployed robots + their trades for the showcase dashboard.
        Returns robots with point_value/initial_margin so the frontend can
        compute P&L without a per-robot API call."""
        _auth(request)
        import json as _json
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        from trader.lab.market_store import ensure_instrument_meta_table, get_instrument_meta

        rows = await pool.fetch(
            """SELECT id, name, params_json, state_json, deployed_at, retire_comment,
                      deployed, schedule
               FROM robots ORDER BY deployed DESC, deployed_at DESC NULLS LAST""",
        )

        await ensure_instrument_meta_table(pool)

        result = []
        for row in rows:
            d = dict(row)
            params = d.get("params_json") or {}
            if isinstance(params, str):
                try:
                    params = _json.loads(params)
                except Exception:
                    params = {}
            state = d.get("state_json") or {}
            if isinstance(state, str):
                try:
                    state = _json.loads(state)
                except Exception:
                    state = {}
            symbol = params.get("symbol") or ""
            paper = not bool(state.get("live_real", False))
            meta = await get_instrument_meta(pool, symbol) or {}
            point_value = meta.get("point_value") or 1.0
            initial_margin = meta.get("initial_margin") or 0.0

            trade_rows = await pool.fetch(
                """SELECT side, qty, price, status, timestamp
                   FROM live_trades WHERE robot_id=$1 ORDER BY timestamp""",
                d["id"],
            )
            trades = [
                {
                    "time": int(r["timestamp"].timestamp()),
                    "side": r["side"],
                    "qty": int(r["qty"]),
                    "price": float(r["price"]),
                    "status": r["status"],
                }
                for r in trade_rows
            ]

            result.append({
                "id": d["id"],
                "name": d["name"],
                "symbol": symbol,
                "schedule": d.get("schedule") or "",
                "deployed": d.get("deployed", False),
                "deployed_at": d["deployed_at"].isoformat() if d.get("deployed_at") else None,
                "retire_comment": d.get("retire_comment"),
                "paper": paper,
                "point_value": point_value,
                "initial_margin": initial_margin,
                "params": params,
                "trades": trades,
            })
        return result

    @fastapi_app.get("/api/v1/robots/live-feed")
    async def robots_live_feed(request: Request, limit: int = 100):
        """Last N trades across all deployed robots for the global feed."""
        _auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        rows = await pool.fetch(
            """SELECT lt.side, lt.qty, lt.price, lt.status, lt.timestamp,
                      lt.symbol, lt.robot_id,
                      r.name as robot_name
               FROM live_trades lt
               JOIN robots r ON r.id = lt.robot_id
               WHERE r.deployed = true
               ORDER BY lt.timestamp DESC
               LIMIT $1""",
            min(limit, 500),
        )
        return [
            {
                "time": int(r["timestamp"].timestamp()),
                "iso": r["timestamp"].isoformat(),
                "robot_id": r["robot_id"],
                "robot_name": r["robot_name"],
                "symbol": r["symbol"],
                "side": r["side"],
                "qty": int(r["qty"]),
                "price": float(r["price"]),
                "status": r["status"],
            }
            for r in rows
        ]

    @fastapi_app.delete("/api/v1/robots/{robot_id}")
    async def delete_robot(robot_id: str, request: Request):
        """Remove a robot from the platform: stop it, drop its FK-dependent rows
        (trades, metrics, backtest runs+results), then delete the robot itself."""
        _auth(request)
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
        _auth(request)
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
        _require_any_auth(request)
        import json
        import asyncio as _asyncio
        from datetime import datetime as _dt
        pool = request.app.state.db_pool
        run_id = cuid()

        def _parse_dt(s: str) -> _dt:
            return _dt.fromisoformat(s.replace("Z", "+00:00"))

        # engine: "local" → run on the VDS now; "remote" → enqueue for the i9 agent;
        # "auto" → i9 if it's alive (keeps load off the important host), else VDS.
        engine = body.get("engine", "local")
        if engine == "auto":
            engine = "remote" if await _agent_alive_any(request.app.state, pool) else "local"
        symbol = body.get("symbol", "")
        # Remote agent expects snake_case fields. An on-demand chart run (single
        # backtest) has no combos → default to one empty set. A sweep run already
        # carries paramSets/paramsGrid → translate field names for the agent.
        if engine == "remote" and body.get("scriptCode"):
            body = {**body, "engine": "remote",
                    "script_code": body.get("scriptCode"),
                    "base_params": {**(body.get("baseParams") or {})}}
            has_combos = body.get("paramSets") or body.get("paramsGrid")
            if not has_combos:
                body["param_sets"] = [{}]
                body["paramsGrid"] = {}
            elif body.get("paramSets"):
                body["param_sets"] = body.pop("paramSets")
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
        _auth(request)
        pool = request.app.state.db_pool
        row = await pool.fetchrow(
            "SELECT status, error_msg, finished_at, engine, agent_id, claimed_at, created_at "
            "FROM backtest_runs WHERE id=$1", run_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")
        d = dict(row)
        for k in ("finished_at", "claimed_at", "created_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        # where it runs + the host's pulse, so the UI can reassure the user it's alive.
        aid = d.get("agent_id") or ""
        d["runner"] = ("i9" if (aid and aid != "vds-fallback") else
                       "VDS (фоновый)" if aid == "vds-fallback" else
                       "VDS" if d.get("engine") == "local" else "очередь")
        d["agent_alive"] = await _agent_alive_any(request.app.state, pool)
        try:
            la = os.getloadavg()
            d["vds_load"] = round(la[0], 2)
        except Exception:
            d["vds_load"] = None
        return d

    @fastapi_app.get("/api/v1/backtest/{run_id}/results")
    async def backtest_results(run_id: str, request: Request, full: int = 0):
        _auth(request)
        pool = request.app.state.db_pool
        # Default: metrics-only (no trades/equity_curve) so the hit-parade list loads
        # fast — a 100+ combo sweep otherwise returns many MB and hangs the UI. The
        # chart fetches one row with full=1 (or re-runs a single backtest) on demand.
        if full:
            rows = await pool.fetch(
                "SELECT * FROM backtest_results WHERE run_id=$1 ORDER BY total_return DESC NULLS LAST",
                run_id,
            )
        else:
            rows = await pool.fetch(
                "SELECT id, run_id, params, sharpe, max_drawdown, win_rate, total_return, "
                "total_trades, net_profit, recovery_factor, point_value "
                "FROM backtest_results WHERE run_id=$1 ORDER BY total_return DESC NULLS LAST",
                run_id,
            )
        return [dict(r) for r in rows]

    # ── Optimization AGENT (external Windows host) ───────────────────────────
    def _agent_auth(request: Request) -> None:
        secret = request.app.state.settings.opt_agent_token.get_secret_value()
        if not secret:
            raise HTTPException(status_code=503, detail="Agent disabled (no token configured)")
        got = request.headers.get("x-agent-token", "")
        if not hmac.compare_digest(got, secret):
            raise HTTPException(status_code=401, detail="Bad agent token")

    def _require_any_auth(request: Request) -> None:
        """Accept either a browser session token OR an X-Agent-Token (for CLI campaign tools)."""
        from trader.auth.guard import auth_ok
        if auth_ok(request.app.state.settings.shectory_auth_bridge_secret, request):
            return
        secret = request.app.state.settings.opt_agent_token.get_secret_value()
        if secret and hmac.compare_digest(request.headers.get("x-agent-token", ""), secret):
            return
        raise HTTPException(status_code=401, detail="Unauthorized")

    @fastapi_app.post("/api/v1/agent/claim")
    async def agent_claim(body: dict, request: Request):
        """Agent pulls the next queued remote run. Atomic claim via UPDATE..RETURNING
        so two agents never grab the same job. Returns the full job_body + run_id,
        plus the robot's script/base params and ruble economics so the agent is
        self-contained. 204 if nothing queued or sweep is paused."""
        _agent_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        # Honour pause flag: the agent stays alive but idles — current job finishes.
        agent_id = body.get("agent_id", "agent")
        engine = "local" if agent_id == "vds-fallback" else "remote"
        if await _agent_is_paused(pool, engine):
            return Response(status_code=204)
        # Heartbeat: any /claim means an external agent (i9) is alive → on-demand chart
        # runs can be routed to it instead of loading the VDS.
        try:
            request.app.state.last_agent_seen = asyncio.get_event_loop().time()
        except Exception:
            pass
        # Interactive UI runs (bare cuid, NOT camp-/opt-) jump ahead of sweep jobs so a
        # chart opens fast even mid-campaign.
        row = await pool.fetchrow(
            """UPDATE backtest_runs SET status='running', claimed_at=now(), agent_id=$1
               WHERE id = (
                 SELECT id FROM backtest_runs
                 WHERE engine='remote' AND status='queued'
                 ORDER BY (id LIKE 'opt-%' OR id LIKE 'camp-%'), created_at
                 LIMIT 1
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
        # ruble economics so the agent computes money-correct PnL + real-ГО return
        point_value = 1.0
        initial_margin = 0.0
        try:
            from trader.lab.market_store import refresh_instrument_spec
            spec = await refresh_instrument_spec(pool, row["symbol"] or base_params.get("symbol", ""))
            point_value = (spec or {}).get("point_value") or 1.0
            initial_margin = (spec or {}).get("initial_margin") or 0.0
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
            "initial_margin": initial_margin,
        }

    @fastapi_app.get("/api/v1/agent/done-pairs")
    async def agent_done_pairs(request: Request):
        """Return all (strategy, symbol) pairs that already have results in the leaderboard.
        Used by campaign scripts to skip already-tested combinations."""
        _agent_auth(request)
        pool = request.app.state.db_pool
        rows = await pool.fetch(
            "SELECT DISTINCT strategy, symbol FROM optimization_leaderboard"
        )
        return [{"strategy": r["strategy"], "symbol": r["symbol"]} for r in rows]

    @fastapi_app.get("/api/v1/agent/control")
    async def agent_control(request: Request):
        """Self-update channel: the agent polls this; when `update_token` differs from
        the one it last applied, it pulls fresh code and re-execs. Bump the token with:
        psql -c "INSERT INTO agent_control(key,value) VALUES('update_token', now()::text)
                 ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value" """
        _agent_auth(request)
        pool = request.app.state.db_pool
        token = None
        if pool is not None:
            try:
                token = await pool.fetchval("SELECT value FROM agent_control WHERE key='update_token'")
            except Exception:
                token = None
        return {"update_token": token}

    # ── Generic agent task queue (run any repo module.func on the i9) ────────
    @fastapi_app.post("/api/v1/agent/task/enqueue")
    async def agent_task_enqueue(body: dict, request: Request):
        """Queue a generic task for the i9 agent: {module, func, args, id?}. args may be
        a list (fanned across the agent's process pool). Auth: session or X-Agent-Token."""
        _require_any_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        module, func = body.get("module"), body.get("func")
        if not module or not func:
            raise HTTPException(status_code=422, detail="module and func required")
        import json as _json
        tid = body.get("id") or ("task-" + cuid())
        await pool.execute(
            """INSERT INTO agent_tasks (id, module, func, args, status)
               VALUES ($1,$2,$3,$4::jsonb,'queued')
               ON CONFLICT (id) DO UPDATE SET module=EXCLUDED.module, func=EXCLUDED.func,
                 args=EXCLUDED.args, status='queued', result=NULL, error=NULL,
                 claimed_at=NULL, finished_at=NULL, created_at=now()""",
            tid, module, func, _json.dumps(body.get("args")),
        )
        return {"ok": True, "task_id": tid}

    @fastapi_app.post("/api/v1/agent/task/claim")
    async def agent_task_claim(body: dict, request: Request):
        """Agent claims the next queued task atomically (UPDATE..RETURNING). 204 if none."""
        _agent_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        try:
            request.app.state.last_agent_seen = asyncio.get_event_loop().time()
        except Exception:
            pass
        row = await pool.fetchrow(
            """UPDATE agent_tasks SET status='running', claimed_at=now(), agent_id=$1
               WHERE id = (SELECT id FROM agent_tasks WHERE status='queued'
                           ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
               RETURNING id, module, func, args""",
            body.get("agent_id", "agent"),
        )
        if not row:
            return Response(status_code=204)
        import json as _json
        args = row["args"]
        if isinstance(args, str):
            args = _json.loads(args)
        return {"task_id": row["id"], "module": row["module"], "func": row["func"], "args": args}

    @fastapi_app.post("/api/v1/agent/task/result")
    async def agent_task_result(body: dict, request: Request):
        """Agent posts a task result {task_id, results|error}."""
        _agent_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        tid = body.get("task_id")
        if not tid:
            raise HTTPException(status_code=422, detail="task_id required")
        import json as _json
        if body.get("error"):
            await pool.execute(
                "UPDATE agent_tasks SET status='failed', error=$1, finished_at=now() WHERE id=$2",
                str(body["error"]), tid)
            return {"ok": True, "status": "failed"}
        await pool.execute(
            "UPDATE agent_tasks SET status='done', result=$1::jsonb, finished_at=now() WHERE id=$2",
            _json.dumps(body.get("results")), tid)
        return {"ok": True, "status": "done"}

    @fastapi_app.get("/api/v1/agent/task/{task_id}")
    async def agent_task_get(task_id: str, request: Request):
        """Read a task's status + result."""
        _require_any_auth(request)
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        row = await pool.fetchrow(
            "SELECT id, status, agent_id, error, result, created_at, claimed_at, finished_at "
            "FROM agent_tasks WHERE id=$1", task_id)
        if not row:
            raise HTTPException(status_code=404, detail="task not found")
        import json as _json
        res = row["result"]
        if isinstance(res, str):
            res = _json.loads(res)
        return {
            "task_id": row["id"], "status": row["status"], "agent_id": row["agent_id"],
            "error": row["error"], "result": res,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "claimed_at": row["claimed_at"].isoformat() if row["claimed_at"] else None,
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        }

    @fastapi_app.get("/api/v1/agent/bars/{key}")
    async def agent_bars(key: str, request: Request):
        """Serve pre-fetched 1m bars to the i9 agent so it doesn't re-fetch a slow
        continuous series from ISS itself (the 120-contract roll enumeration hangs on
        the agent's network). Files live in agent_bars/<key>.json = {"key","rows":
        [[time,open,high,low,close,volume],...]}, uploaded out-of-band."""
        _agent_auth(request)
        import json as _json
        import os as _os
        safe = "".join(c for c in key if c.isalnum())
        path = _os.path.join("agent_bars", f"{safe}.json")
        if not _os.path.exists(path):
            raise HTTPException(status_code=404, detail=f"bars not found for {safe}")
        with open(path, encoding="utf-8") as f:
            return _json.load(f)

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
            m = _re.search(r"make_on_bar\('([a-z0-9_]+)'\)", sc)
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
        # Multi-combo runs: keep trades+equity only for the BEST result (by profit×RF)
        # so the leader chart has data. Strip from the rest to avoid DB bloat + nginx 413.
        def _best_score(e: dict) -> float:
            # Mirror the frontend profit×RF ranking; tolerate None (recovery_factor is
            # None when there's no drawdown). Losers (np<=0) score below all winners.
            if not e.get("ok"):
                return -1e18
            rr = e.get("result") or {}
            np = rr.get("net_profit") or rr.get("total_return") or 0
            rf = rr.get("recovery_factor") or 0
            return np * max(rf, 0.01) if np > 0 else np
        if len(results) > 1:
            best_idx = max(range(len(results)), key=lambda i: _best_score(results[i]))
            for i, entry in enumerate(results):
                if i != best_idx and entry.get("ok") and entry.get("result"):
                    entry["result"].pop("trades", None)
                    entry["result"].pop("equity_curve", None)
        # Build all rows first, then one executemany per table instead of a round-trip
        # per combo (hundreds of awaited INSERTs on the small VDS Postgres).
        ok = [e for e in results if e.get("ok")]
        result_rows = [
            (
                cuid(), run_id, e["params"],
                e["result"].get("trades", []), e["result"].get("equity_curve", []),
                e["result"].get("sharpe"), e["result"].get("max_drawdown"),
                e["result"].get("win_rate"), e["result"].get("total_return"),
                e["result"].get("total_trades"),
                e["result"].get("net_profit"), e["result"].get("recovery_factor"),
            )
            for e in ok
        ] if not is_campaign else []
        # Mirror to the leaderboard ONLY for real sweeps (camp-/opt-, non-null
        # campaign_run). A UI/chart run has strat_id but campaign=None → NULL
        # campaign_run would abort the txn.
        lb_rows = [
            (
                campaign, strat_id, meta["symbol"], e["params"],
                e["result"].get("total_return"), e["result"].get("sharpe"),
                e["result"].get("max_drawdown"), e["result"].get("win_rate"),
                e["result"].get("total_trades"), _score(e["result"]), _cand(e["result"]),
                e["result"].get("net_profit"), e["result"].get("recovery_factor"),
                e["result"].get("ann_return_go"), e["result"].get("ann_return_full"),
            )
            for e in ok
        ] if is_campaign else []
        async with pool.acquire() as conn:
            async with conn.transaction():
                if not is_campaign:
                    await conn.execute("DELETE FROM backtest_results WHERE run_id=$1", run_id)
                    if result_rows:
                        await conn.executemany(
                            """INSERT INTO backtest_results
                               (id, run_id, params, trades, equity_curve, sharpe, max_drawdown, win_rate,
                                total_return, total_trades, net_profit, recovery_factor)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
                            result_rows,
                        )
                if lb_rows:
                    try:
                        await conn.executemany(
                            """INSERT INTO optimization_leaderboard
                                 (campaign_run, strategy, symbol, params, total_return, sharpe,
                                  max_drawdown, win_rate, total_trades, score, candidate,
                                  net_profit, recovery_factor, ann_return_go, ann_return_full)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""",
                            lb_rows,
                        )
                    except Exception as exc:
                        log.warning("agent.leaderboard_insert_failed", run_id=run_id, error=str(exc))
                await conn.execute(
                    "UPDATE backtest_runs SET status='done', finished_at=now() WHERE id=$1", run_id
                )
        return {"ok": True, "count": len(results)}

    # ── Agent pause / resume (web UI controls for Botstore) ──────────────────

    @fastapi_app.post("/api/v1/agent/pause")
    async def agent_pause(body: dict, request: Request):
        """Pause the sweep agent for an engine (remote=i9, local=VDS).
        The agent stays alive but idles — current job finishes gracefully."""
        _auth(request)
        engine = body.get("engine", "remote")
        if engine not in ("remote", "local"):
            raise HTTPException(status_code=422, detail="engine must be 'remote' or 'local'")
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        await _agent_set_pause(pool, engine, True)
        return {"ok": True, "engine": engine, "paused": True}

    @fastapi_app.post("/api/v1/agent/resume")
    async def agent_resume(body: dict, request: Request):
        """Resume the sweep agent for an engine."""
        _auth(request)
        engine = body.get("engine", "remote")
        if engine not in ("remote", "local"):
            raise HTTPException(status_code=422, detail="engine must be 'remote' or 'local'")
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(status_code=503, detail="DB unavailable")
        await _agent_set_pause(pool, engine, False)
        return {"ok": True, "engine": engine, "paused": False}

    # ── Market Data (MOEX ISS cache) ──────────────────────────────────────────

    @fastapi_app.post("/api/v1/market/update", status_code=202)
    async def market_update(body: dict, request: Request):
        """
        Trigger ISS download for a list of symbols and date range.
        Saves to ohlcv_bars DB cache. Runs in background.
        Body: { "symbols": ["RIM6","SIM6"], "dateFrom": "2026-01-01", "dateTo": "2026-05-01" }
        """
        _auth(request)
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
        _auth(request)
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
        _auth(request)
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
