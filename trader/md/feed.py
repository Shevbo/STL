import asyncio
from collections.abc import AsyncIterator, Callable, Awaitable

import structlog

from trader.md.models import FeedState, Quote

log = structlog.get_logger()


class QuoteState:
    def __init__(self) -> None:
        self._latest: Quote | None = None
        self._event: asyncio.Event = asyncio.Event()
        self._closed: bool = False

    def update(self, quote: Quote) -> None:
        self._latest = quote
        self._event.set()

    def next_event(self) -> asyncio.Event:
        self._event.clear()
        return self._event


class MarketDataFeed:
    def __init__(
        self,
        qs,  # QuoteStream — typed loosely to avoid circular import
        watchdog_secs: float = 5.0,
        on_raw: Callable[[dict], None] | None = None,
    ) -> None:
        self._qs = qs
        self._watchdog_secs = watchdog_secs
        self._on_raw = on_raw
        self._slots: dict[str, QuoteState] = {}
        self._active_symbols: set[str] = set()
        self._state = FeedState.CONNECTING
        self._running = False
        self._reader_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._heartbeat = asyncio.Event()

    @property
    def state(self) -> FeedState:
        return self._state

    async def start(self, get_token: Callable[[], Awaitable[str]]) -> None:
        self._running = True
        await self._qs.start(get_token=get_token)
        self._reader_task = asyncio.create_task(self._reader())
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def add_symbol(self, symbol: str) -> None:
        if symbol not in self._slots:
            self._slots[symbol] = QuoteState()
        if symbol not in self._active_symbols:
            self._active_symbols.add(symbol)
            await self._qs.subscribe(symbol)

    def latest(self, symbol: str) -> Quote | None:
        slot = self._slots.get(symbol)
        return slot._latest if slot else None

    async def subscribe(self, symbol: str) -> AsyncIterator[Quote]:
        if symbol not in self._slots:
            await self.add_symbol(symbol)
        slot = self._slots[symbol]
        # conflation: a fresh subscriber immediately receives the latest known quote
        if self._running and not slot._closed and slot._latest is not None:
            yield slot._latest
        try:
            while self._running and not slot._closed:
                event = slot.next_event()
                await event.wait()
                if slot._latest is not None:
                    yield slot._latest
        finally:
            pass  # Event-based: no dangling Futures to cancel

    async def _reader(self) -> None:
        try:
            async for raw in self._qs.iter_quotes():
                if self._on_raw:
                    try:
                        self._on_raw(raw)
                    except Exception as exc:
                        log.warning("md.on_raw_error", exc=str(exc))

                symbol = raw.get("symbol", "")
                if symbol not in self._slots:
                    continue

                try:
                    quote = Quote.from_payload(symbol, raw)
                except Exception as exc:
                    log.warning("md.parse_error", exc=str(exc))
                    continue

                slot = self._slots[symbol]
                if slot._latest and quote.timestamp < slot._latest.timestamp:
                    log.warning("md.out_of_order", symbol=symbol)
                    continue

                slot.update(quote)
                self._heartbeat.set()
                if self._state == FeedState.CONNECTING:
                    self._state = FeedState.LIVE
        except Exception as exc:
            log.error("md.reader_crashed", exc=str(exc))
        finally:
            self._state = FeedState.CLOSED
            if self._watchdog_task:
                self._watchdog_task.cancel()
            for slot in self._slots.values():
                slot._closed = True
                slot._event.set()

    async def _watchdog(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._heartbeat.wait()),
                    timeout=self._watchdog_secs,
                )
                self._heartbeat.clear()
                if self._state == FeedState.STALE:
                    self._state = FeedState.LIVE
            except asyncio.TimeoutError:
                if self._state == FeedState.LIVE:
                    self._state = FeedState.STALE
                    log.warning("md.watchdog.stale")

    async def aclose(self) -> None:
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
        await self._qs.close()
        if self._reader_task:
            try:
                await asyncio.wait_for(self._reader_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader_task.cancel()
        self._state = FeedState.CLOSED
        for slot in self._slots.values():
            slot._closed = True
            slot._event.set()
