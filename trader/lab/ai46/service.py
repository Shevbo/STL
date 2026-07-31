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
import datetime
import time
from decimal import Decimal
from uuid import uuid4

import httpx
import structlog

from trader.lab.ai46 import llm as LLM
from trader.lab.ai46.order_flow import TradesStream
from trader.lab.ai46.runner import Ai46Runner
from trader.market_session import fetch_schedule, probe

log = structlog.get_logger()

ROBOT_ID = "team-46"
ROBOT_NAME = "MOEX AI Trading Bot — team-46"
_TICK_SECS = 60.0
_SCHED_TTL = 3600.0        # официальное расписание почти статично
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
        self._http: httpx.AsyncClient | None = None
        self._sched: dict = {}
        self._sched_at = 0.0
        self._was_open = True

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
        if self._http is not None:
            await self._http.aclose()
        await self._set_deployed(False)

    async def _set_deployed(self, on: bool) -> None:
        """Флаг «робот работает» в витрине. Раньше bootstrap ставил его только при
        создании строки (ON CONFLICT DO NOTHING), поэтому стенд месяц показывал
        «остановлен» у робота, который торговал каждую минуту."""
        if self.pool is None:
            return
        try:
            await self.pool.execute("UPDATE robots SET deployed=$2 WHERE id=$1", ROBOT_ID, on)
        except Exception as exc:  # noqa: BLE001
            log.warning("ai46.deployed_flag_failed", error=str(exc))

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
            # Список инструментов пишем КАЖДЫЙ старт: он пересчитывается по обороту,
            # а витрина без него показывала один тикер у портфеля из двух десятков.
            await self.pool.execute(
                "UPDATE robots SET params_json=$2, deployed=true WHERE id=$1",
                ROBOT_ID, {"symbol": self.symbols[0] if self.symbols else "",
                           "symbols": self.symbols},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai46.bootstrap_failed", error=str(exc))

    async def _session_open(self) -> bool:
        """Идут ли торги FORTS ПРЯМО СЕЙЧАС — по официальному расписанию ISS и часам
        БИРЖИ (SYSTIME), а не по локальному календарю.

        ISS молчит (open=None) -> считаем, что торги идут: бары всё равно приедут
        пустыми/несвежими, и глушить робота из-за сетевого сбоя нельзя.
        """
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=8.0)
        now = time.time()
        if now - self._sched_at > _SCHED_TTL:
            try:
                self._sched = await fetch_schedule(self._http)
                self._sched_at = now
            except Exception as exc:  # noqa: BLE001
                log.warning("ai46.schedule_fetch_failed", error=str(exc))
        try:
            st = await probe(self.symbols[:2], int(now * 1000),
                             client=self._http, schedule=self._sched)
        except Exception as exc:  # noqa: BLE001
            log.warning("ai46.session_probe_failed", error=str(exc))
            return True
        return st.open is not False

    async def _flush_positions(self) -> None:
        """Биржа закрылась — закрываем бумажные позиции по последней известной цене.

        Без этого позиция висела через ночь, а сессионный автомат продолжал тикать по
        стенным часам на ЗАМОРОЖЕННЫХ барах: из 14 040 филлов team-46 за месяц около
        3 600 записаны в 00:00-07:00 МСК, когда FORTS закрыт, по ценам вчерашнего
        закрытия. Это не сделки, а шум, который портил всю статистику.
        """
        if not self.runner.exec.positions:
            return
        self.runner.exec.set_time(time.time())
        for sym in list(self.runner.exec.positions):
            self.runner.exec.close_hard(sym, "contrarian")
        self.runner.sessions.clear()
        await self._persist_fills()
        log.info("ai46.session_closed_flush")

    async def _loop(self) -> None:
        from trader.lab.runtime import _load_bars_shared
        while self._running:
            try:
                if not await self._session_open():
                    if self._was_open:
                        await self._flush_positions()
                        self._was_open = False
                    await asyncio.sleep(_TICK_SECS)
                    continue
                self._was_open = True
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
        # ponytail: метаданные филла упакованы в order_id (`ai46:<seq>:<роль>:<вес в
        # сотых долях процента>`), а не в новые колонки live_trades — таблица общая
        # для всех роботов Lab, мигрировать её ради одного бумажного портфельного
        # робота незачем. Понадобится больше полей — тогда колонки.
        # ДВЕ ошибки, которые это чинит:
        #  * order_id был КОНСТАНТОЙ 'ai46' у всех 14 тыс. филлов, а витрина
        #    сопоставляет строку таблицы с событием ИМЕННО по order_id — карта
        #    схлопывалась в ОДНО событие, и вся колонка «Тип» показывала один ярлык
        #    (отсюда «сделки только AVG») и один и тот же фин.рез в каждой строке;
        #  * qty=1 — заглушка: у стратегии нет контрактов, размер позиции это доля
        #    портфеля. Теперь она записана и витрина считает доходность, а не рубли
        #    несуществующих контрактов.
        # Время берём фактическое время филла, а не now() момента записи: батч
        # executemany ставил всем строкам одну метку, и филлы разных инструментов
        # склеивались в одну секунду.
        # seq = время филла + номер в батче: счётчик _persisted обнуляется рестартом,
        # и голого номера хватило бы ровно до первого перезапуска.
        base = self._persisted
        rows = [
            (uuid4().hex, ROBOT_ID, f.ticker, f.side, 1, Decimal(str(f.price)),
             f"ai46:{int(f.time or time.time())}-{base + i}:{f.kind}:{round(f.size_pct * 10000)}",
             "paper",
             datetime.datetime.fromtimestamp(f.time or time.time(), tz=datetime.timezone.utc))
            for i, f in enumerate(new)
        ]
        try:
            await self.pool.executemany(
                """INSERT INTO live_trades
                     (id, robot_id, symbol, side, qty, price, order_id, status, timestamp)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                rows,
            )
            self._persisted = len(fills)
        except Exception as exc:  # noqa: BLE001
            log.warning("ai46.persist_failed", error=str(exc))
