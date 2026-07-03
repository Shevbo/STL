"""Async gRPC client for the agent's loopback RunnerBridge.

Every stream reconnects forever with backoff — the runner may start before the
agent (zero-touch: order-independent startup) and must survive agent restarts.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import grpc
import grpc.aio
import structlog

from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2 as rb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2_grpc as rbg

log = structlog.get_logger()

_BACKOFF_MAX = 30.0


class BridgeClient:
    def __init__(self, addr: str) -> None:
        self._addr = addr
        self._channel: grpc.aio.Channel | None = None
        self._stub: rbg.RunnerBridgeStub | None = None

    async def start(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._addr)
        self._stub = rbg.RunnerBridgeStub(self._channel)

    async def _stream(self, open_stream, name: str) -> AsyncIterator:
        backoff = 1.0
        while True:
            try:
                async for item in open_stream():
                    backoff = 1.0
                    yield item
            except (grpc.aio.AioRpcError, ConnectionError) as exc:
                log.warning("bridge.stream_down", stream=name, error=str(exc))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    def ticks(self, codes: list[str]) -> AsyncIterator[pb.MarketDataTick]:
        return self._stream(
            lambda: self._stub.StreamTicks(rb.TickFilter(codes=codes)), "ticks")

    def order_events(self, prefix: str) -> AsyncIterator[pb.OrderUpdate]:
        return self._stream(
            lambda: self._stub.StreamOrderEvents(rb.EventsFilter(client_prefix=prefix)),
            "order_events")

    def control(self, version: str) -> AsyncIterator[rb.RunnerControl]:
        return self._stream(
            lambda: self._stub.StreamControl(rb.ControlHello(runner_version=version,
                                                             pid=os.getpid())),
            "control")

    async def place_order(self, *, client_id: str, code: str, side: str,
                          price: float, qty: int, collar: float = 0.002) -> None:
        pb_side = pb.SIDE_BUY if side == "buy" else pb.SIDE_SELL
        ack = await self._stub.PlaceRunnerOrder(pb.PlaceOrder(
            client_id=client_id, code=code, side=pb_side,
            price=price, quantity=qty, collar=collar))
        if not ack.ok:
            raise RuntimeError(f"bridge rejected order: {ack.error}")

    async def cancel_order(self, client_id: str, order_id: str) -> None:
        await self._stub.CancelRunnerOrder(
            pb.CancelOrder(client_id=client_id, order_id=order_id))

    async def report_status(self, report: pb.RobotStatusReport) -> None:
        try:
            await self._stub.ReportStatus(report)
        except (grpc.aio.AioRpcError, ConnectionError) as exc:
            log.warning("bridge.report_failed", error=str(exc))  # never crash on status

    async def aclose(self) -> None:
        if self._channel is not None:
            await self._channel.close()
