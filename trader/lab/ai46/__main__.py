"""Standalone AI46 (team-46) runner: `python -m trader.lab.ai46`.

Runs the paper strategy in ITS OWN PROCESS. In-process (inside the STL API
uvicorn) the pure-Python HMM/GARCH re-fits blocked the single event loop —
py-spy showed hmm_regime bursts freezing every API request for up to ~30s.
A research strategy must not share a process with the trading API.

Env (same names the in-process gate used): AI46_ENABLED is NOT checked here —
running this module IS the intent. AI46_SYMBOLS / AI46_SYMBOL_COUNT /
AI46_LLM_ENABLED / AI46_ORDER_FLOW / AI46_MODEL_REFRESH_SECS as before.
The STL API must run with AI46_ENABLED=0 (or unset) so it does not start a
second copy in-process.
"""

import asyncio
import os

import structlog

from trader.auth.client import AsyncAuthClient
from trader.config import Settings
from trader.db import close_pool, get_pool, init_pool
from trader.lab.ai46 import llm as LLM
from trader.lab.ai46.service import Ai46Service

log = structlog.get_logger()


async def main() -> None:
    settings = Settings()
    if not settings.lab_db_url:
        raise SystemExit("lab_db_url is not set — AI46 needs Postgres")
    await init_pool(settings.lab_db_url)
    pool = get_pool()

    auth = AsyncAuthClient(
        secret_token=settings.finam_secret_token.get_secret_value(),
        base_url=settings.finam_api_base_url,
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    )
    await auth.get_token()

    syms_env = os.environ.get("AI46_SYMBOLS", "").strip()
    if syms_env:
        symbols = [s.strip() for s in syms_env.split(",") if s.strip()]
    else:
        from trader.lab.iss_loader import top_instruments
        symbols = await top_instruments(int(os.environ.get("AI46_SYMBOL_COUNT", "20")))

    svc = Ai46Service(
        pool, auth.get_token, symbols,
        llm_enabled=os.environ.get("AI46_LLM_ENABLED", "1") not in ("0", "false", "False"),
        order_flow_live=os.environ.get("AI46_ORDER_FLOW", "1") not in ("0", "false", "False"),
    )
    await svc.start()
    log.info("ai46.standalone_started", symbols=symbols)
    try:
        while True:  # the service runs its own loop task; just stay alive
            await asyncio.sleep(3600)
    finally:
        await svc.stop()
        await auth.aclose()
        await close_pool()


if __name__ == "__main__":
    # LLM import is only referenced to keep the dependency explicit for bundlers.
    _ = LLM
    asyncio.run(main())
