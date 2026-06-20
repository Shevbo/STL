"""Ai46Service — backend task that runs the team-46 strategy in PAPER mode.

Env-gated by AI46_ENABLED (off by default — deploy is a no-op until enabled).
Each minute it loads 1m bars per symbol (shared ISS cache), ticks the Ai46Runner
(features → detector → contrarian → LLM PM gate via Lineman → paper executor),
and persists new paper fills to live_trades under robot_id 'team-46' so the
Showcase lists "MOEX AI Trading Bot — team-46". Live order flow (OFI) comes from
the Finam LatestTrades stream when available.

Runs as a privileged backend task — NOT a sandboxed on_bar robot (it needs the
network for the LLM gate and gRPC streams, which script_guard forbids).
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from uuid import uuid4

import structlog

from trader.lab.ai46 import llm as LLM
from trader.lab.ai46.order_flow import TradesStream
from trader.lab.ai46.runner import Ai46Runner

log = structlog.get_logger()

ROBOT_ID = "team-46"
ROBOT_NAME = "MOEX AI Trading Bot — team-46"
_TICK_SECS = 60.0
_STL_LINK = "stl-finam-forts-01"
_OWNER = "bshevelev75@gmail.com"


class Ai46Service:
    def __init__(self, pool, get_token, symbols, *, llm_enabled: bool = True,
                 order_flow_live: bool = True) -> None:
        self.pool = pool
        self.get_token = get_token
        self.symbols = list(symbols)
        self.runner = Ai46Runner(symbols, klod=LLM.KlodClient(enabled=llm_enabled))
        self._trades = TradesStream(self.runner.flow) if order_flow_live else None
        self._task: asyncio.Task | None = None
        self._running = False
        self._persisted = 0

    async def start(self) -> None:
        await self._bootstrap_robot()
        if self._trades is not None:
            try:
                await self._trades.start(self.get_token)
                for s in self.symbols:
                    await self._trades.subscribe(s)
            except Exception as exc:  # noqa: BLE001
                log.warning("ai46.trades_stream_failed", error=str(exc))
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("ai46.started", symbols=self.symbols)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        if self._trades is not None:
            await self._trades.close()

    async def _bootstrap_robot(self) -> None:
        if self.pool is None:
            return
        try:
            await self.pool.execute(
                """INSERT INTO robots
                     (id, user_email, stl_link_id, name, script_code, params_json, schedule, deployed)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,true)
                   ON CONFLICT (id) DO NOTHING""",
                ROBOT_ID, _OWNER, _STL_LINK, ROBOT_NAME,
                "# team-46 backend AI strategy (not a sandbox script)",
                {"symbol": self.symbols[0] if self.symbols else ""}, "09:00-23:55",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai46.bootstrap_failed", error=str(exc))

    async def _loop(self) -> None:
        from trader.lab.runtime import _load_bars_shared
        while self._running:
            try:
                bars_by: dict[str, list] = {}
                for s in self.symbols:
                    try:
                        bars_by[s] = await _load_bars_shared(s, 7, interval=1)
                    except Exception:  # noqa: BLE001
                        bars_by[s] = []
                await self.runner.tick(time.time(), bars_by)
                await self._persist_fills()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.error("ai46.tick_error", error=str(exc))
            await asyncio.sleep(_TICK_SECS)

    async def _persist_fills(self) -> None:
        if self.pool is None:
            return
        fills = self.runner.exec.fills
        new = fills[self._persisted:]
        if not new:
            return
        rows = [
            (uuid4().hex, ROBOT_ID, f.ticker, f.side, 1, Decimal(str(f.price)), "ai46", "paper")
            for f in new
        ]
        try:
            await self.pool.executemany(
                """INSERT INTO live_trades
                     (id, robot_id, symbol, side, qty, price, order_id, status, timestamp)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())""",
                rows,
            )
            self._persisted = len(fills)
        except Exception as exc:  # noqa: BLE001
            log.warning("ai46.persist_failed", error=str(exc))
