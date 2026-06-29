"""Async gRPC server: STL side of the QUIK agent link (sprint02 Phase 1).

READ-ONLY. Implements ``QuikAgentLink.Session`` (the single long-lived bidi
stream). The agent dials OUT to STL (it sits behind NAT), so STL is the server.

On connect:
  * verify the Bearer token from gRPC metadata ``authorization`` against the
    same HMAC session-secret mechanism as ``trader/auth`` (a dedicated agent
    token can be configured; if unset we fall back to the portal bridge secret).
  * stream AgentMessages -> store latest Register/Heartbeat/Securities/Tick/
    OrderBook/Params/Diagnostics/Alert, reply Ack(seq).
  * dispatch queued Commands back to the agent.

No order placement / routing / trade code paths exist here (Guard 3).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import grpc
import structlog

import trader.quik  # noqa: F401 — sets up the pb import path (see trader/quik/__init__.py)
from shectory.quik.v1 import quik_agent_pb2 as pb
from shectory.quik.v1 import quik_agent_pb2_grpc as pb_grpc
from trader.auth.portal import verify_session_token
from trader.quik.store import QuikAgentStore

log = structlog.get_logger()


def _bearer_from_metadata(context: grpc.aio.ServicerContext) -> str | None:
    for key, value in context.invocation_metadata() or ():
        if key.lower() == "authorization":
            v = value or ""
            return v[7:] if v.startswith("Bearer ") else v
    return None


def verify_agent_token(token: str | None, agent_secret: str, portal_secret: str) -> str | None:
    """Return the verified subject (email/agent) or None.

    Two accepted shapes, mirroring how ``trader/auth`` already validates:
      1. a signed session token (``subject:expires:hmac``) verified with the
         agent secret, then the portal secret as fallback;
      2. (only if no signed-token form validates) a direct shared-secret match
         against the configured agent secret — lets a keymaster-provisioned
         opaque bearer authenticate without minting a session token.
    """
    if not token:
        return None
    for secret in (agent_secret, portal_secret):
        if secret:
            sub = verify_session_token(token, secret)
            if sub:
                return sub
    # Opaque shared-secret bearer (constant-time compare).
    if agent_secret:
        import hmac
        if hmac.compare_digest(token, agent_secret):
            return "quik-agent"
    return None


def _security_to_dict(s) -> dict:
    return {
        "code": s.code, "name": s.name, "class_code": s.class_code,
        "price_step": s.price_step, "step_cost": s.step_cost,
        "received_at_unix_ms": s.received_at_unix_ms,
    }


def _tick_to_dict(t) -> dict:
    return {
        "code": t.code, "last": t.last, "bid": t.bid, "ask": t.ask,
        "open_interest": t.open_interest, "exchange_ts_unix_ms": t.exchange_ts_unix_ms,
        "received_at_unix_ms": t.received_at_unix_ms,
    }


def _order_book_to_dict(ob) -> dict:
    return {
        "code": ob.code,
        "bids": [{"price": lv.price, "quantity": lv.quantity} for lv in ob.bids],
        "asks": [{"price": lv.price, "quantity": lv.quantity} for lv in ob.asks],
        "received_at_unix_ms": ob.received_at_unix_ms,
    }


def _params_to_dict(p) -> dict:
    return {
        "rows": [
            {"code": r.code, "price_step": r.price_step,
             "step_cost": r.step_cost, "coef": r.coef}
            for r in p.rows
        ],
        "received_at_unix_ms": p.received_at_unix_ms,
    }


def _diag_to_dict(d) -> dict:
    return {
        "dde": int(d.dde), "quik": int(d.quik), "link": int(d.link),
        "last_tick_age_ms": d.last_tick_age_ms,
        "reconnects_since_start": d.reconnects_since_start,
        "uptime_sec": d.uptime_sec,
    }


class QuikAgentLinkServicer(pb_grpc.QuikAgentLinkServicer):
    """Servicer for the QuikAgentLink.Session bidi stream."""

    def __init__(
        self,
        store: QuikAgentStore,
        agent_secret: str,
        portal_secret: str,
        command_queues: dict[str, asyncio.Queue] | None = None,
        alert_forwarder=None,
    ) -> None:
        self.store = store
        self.agent_secret = agent_secret
        self.portal_secret = portal_secret
        # agent_id -> queue of pb.Command to send down the stream.
        self.command_queues = command_queues if command_queues is not None else {}
        # Optional AlertForwarder (Telegram + CRITICAL SMS stub). None disables it.
        self.alert_forwarder = alert_forwarder

    def enqueue_command(self, agent_id: str, command: "pb.Command") -> None:
        q = self.command_queues.setdefault(agent_id, asyncio.Queue())
        q.put_nowait(command)

    async def Session(
        self,
        request_iterator: AsyncIterator["pb.AgentMessage"],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator["pb.OrchestratorMessage"]:
        token = _bearer_from_metadata(context)
        subject = verify_agent_token(token, self.agent_secret, self.portal_secret)
        if subject is None:
            log.warning("quik.session.auth_rejected", peer=context.peer())
            # In a streaming-response servicer, set the status + details and return
            # (closes the stream UNAUTHENTICATED). context.abort() can race with the
            # response framing here and surface as INTERNAL on the client.
            await context.send_initial_metadata(())
            context.set_code(grpc.StatusCode.UNAUTHENTICATED)
            context.set_details("invalid or missing bearer token")
            return

        agent_id = subject
        self.store.ensure_agent(agent_id)
        cmd_q = self.command_queues.setdefault(agent_id, asyncio.Queue())
        log.info("quik.session.connected", agent=agent_id, peer=context.peer())

        async def _drain_commands() -> AsyncIterator["pb.OrchestratorMessage"]:
            while True:
                command = await cmd_q.get()
                yield pb.OrchestratorMessage(command=command)

        cmd_task: asyncio.Task | None = None
        try:
            async for msg in request_iterator:
                # Identify a more specific agent id once Register arrives.
                field = msg.WhichOneof("payload")
                if field == "register":
                    reg = msg.register
                    new_id = reg.host_name or agent_id
                    if new_id != agent_id:
                        # migrate queue + state to the host-named id
                        self.command_queues.setdefault(new_id, cmd_q)
                        agent_id = new_id
                    self.store.set_register(agent_id, {
                        "agent_version": reg.agent_version, "host_name": reg.host_name,
                        "quik_data_root": reg.quik_data_root,
                        "timezone_offset_min": reg.timezone_offset_min,
                        "build_rev": reg.build_rev,
                    })
                elif field == "heartbeat":
                    hb = msg.heartbeat
                    self.store.set_heartbeat(agent_id, {
                        "sent_at_unix_ms": hb.sent_at_unix_ms, "dde_alive": hb.dde_alive,
                        "quik_alive": hb.quik_alive, "last_tick_age_ms": hb.last_tick_age_ms,
                    })
                elif field == "securities":
                    snap = msg.securities
                    self.store.apply_securities(
                        agent_id, [_security_to_dict(s) for s in snap.items], snap.is_full)
                elif field == "tick":
                    self.store.set_tick(agent_id, _tick_to_dict(msg.tick))
                elif field == "order_book":
                    self.store.set_order_book(agent_id, _order_book_to_dict(msg.order_book))
                elif field == "params":
                    self.store.set_params(agent_id, _params_to_dict(msg.params))
                elif field == "diagnostics":
                    self.store.set_diagnostics(agent_id, _diag_to_dict(msg.diagnostics))
                elif field == "alert":
                    a = msg.alert
                    alert_dict = {
                        "severity": int(a.severity), "code": a.code,
                        "message": a.message, "raised_at_unix_ms": a.raised_at_unix_ms,
                    }
                    self.store.set_alert(agent_id, alert_dict)
                    # Fire-and-forget forward to Telegram (+ CRITICAL SMS stub).
                    # forward() never raises, so a Telegram failure can't break the
                    # gRPC stream; we also detach it as a task to avoid blocking.
                    if self.alert_forwarder is not None:
                        asyncio.ensure_future(
                            self.alert_forwarder.forward(alert_dict, agent_id))

                self.store.touch(agent_id, msg.seq)

                # Start the command pump lazily after first frame (we now know agent_id).
                if cmd_task is None:
                    cmd_task = asyncio.ensure_future(self._pump(cmd_q))

                # Ack every received frame.
                yield pb.OrchestratorMessage(ack=pb.Ack(ack_seq=msg.seq))

                # Flush any commands enqueued for this agent.
                while not cmd_q.empty():
                    command = cmd_q.get_nowait()
                    yield pb.OrchestratorMessage(command=command)
        except asyncio.CancelledError:
            raise
        except grpc.aio.AioRpcError as exc:
            log.warning("quik.session.rpc_error", agent=agent_id, code=str(exc.code()))
        finally:
            if cmd_task is not None:
                cmd_task.cancel()
            log.info("quik.session.disconnected", agent=agent_id)

    async def _pump(self, cmd_q: asyncio.Queue) -> None:
        # Placeholder keeper so an idle queue does not block; the Session loop
        # itself flushes commands after each Ack. Kept for symmetry / future use.
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return


class QuikAgentServer:
    """Owns the grpc.aio server and the shared store + servicer."""

    def __init__(
        self,
        listen: str,
        store: QuikAgentStore,
        agent_secret: str,
        portal_secret: str,
        alert_forwarder=None,
    ) -> None:
        self.listen = listen
        self.store = store
        self._server: grpc.aio.Server | None = None
        self.servicer = QuikAgentLinkServicer(
            store, agent_secret, portal_secret, alert_forwarder=alert_forwarder)

    async def start(self) -> None:
        self._server = grpc.aio.server()
        pb_grpc.add_QuikAgentLinkServicer_to_server(self.servicer, self._server)
        self._server.add_insecure_port(self.listen)
        await self._server.start()
        log.info("quik.server.started", listen=self.listen)

    async def stop(self, grace: float = 2.0) -> None:
        if self._server is not None:
            await self._server.stop(grace)
            log.info("quik.server.stopped")

    def enqueue_command(self, agent_id: str, command: "pb.Command") -> None:
        self.servicer.enqueue_command(agent_id, command)
