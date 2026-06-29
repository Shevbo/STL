"""In-memory latest-state store for the QUIK agent link (sprint02 Phase 1).

Read-only. Holds the freshest Register / Heartbeat / Securities / Tick /
OrderBook / Params / Diagnostics / Alert per agent, plus a link-freshness lamp
(green / yellow / red) modelled on PiranhaAI's ``agent_link_fresh_sec``.

No order state, no routing. Pure data + status.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class AgentState:
    """Latest snapshot of one connected QUIK agent."""

    agent_id: str
    register: dict[str, Any] | None = None
    last_heartbeat: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    last_alert: dict[str, Any] | None = None
    # security code -> Security dict
    securities: dict[str, dict[str, Any]] = field(default_factory=dict)
    # security code -> latest MarketDataTick dict
    ticks: dict[str, dict[str, Any]] = field(default_factory=dict)
    # security code -> latest OrderBook dict
    order_books: dict[str, dict[str, Any]] = field(default_factory=dict)
    # link bookkeeping
    last_seen_ms: int = field(default_factory=_now_ms)
    connected_at_ms: int = field(default_factory=_now_ms)
    last_seq: int = 0


def _link_lamp(last_seen_ms: int, fresh_sec: int) -> str:
    """Green if seen within fresh_sec, yellow up to 3x, red beyond.

    Mirrors PiranhaAI's link-freshness lamp: a single staleness window with a
    generous yellow band before declaring the link down.
    """
    age_ms = _now_ms() - last_seen_ms
    fresh_ms = max(1, fresh_sec) * 1000
    if age_ms <= fresh_ms:
        return "green"
    if age_ms <= fresh_ms * 3:
        return "yellow"
    return "red"


class QuikAgentStore:
    """Thread/async-safe latest-state store keyed by agent id.

    Writes come from the gRPC server coroutine; reads come from FastAPI request
    handlers (possibly a different thread under the threadpool). A plain lock is
    enough — every operation is a fast dict update.
    """

    def __init__(self, link_fresh_sec: int = 15) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, AgentState] = {}
        self.link_fresh_sec = link_fresh_sec

    # ---- writes (from the gRPC server) ----
    def ensure_agent(self, agent_id: str) -> AgentState:
        with self._lock:
            st = self._agents.get(agent_id)
            if st is None:
                st = AgentState(agent_id=agent_id)
                self._agents[agent_id] = st
            return st

    def touch(self, agent_id: str, seq: int) -> None:
        with self._lock:
            st = self._agents.get(agent_id)
            if st is None:
                st = AgentState(agent_id=agent_id)
                self._agents[agent_id] = st
            st.last_seen_ms = _now_ms()
            if seq:
                st.last_seq = seq

    def set_register(self, agent_id: str, register: dict[str, Any]) -> None:
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).register = register

    def set_heartbeat(self, agent_id: str, hb: dict[str, Any]) -> None:
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).last_heartbeat = hb

    def set_diagnostics(self, agent_id: str, diag: dict[str, Any]) -> None:
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).diagnostics = diag

    def set_params(self, agent_id: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).params = params

    def set_alert(self, agent_id: str, alert: dict[str, Any]) -> None:
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).last_alert = alert

    def apply_securities(self, agent_id: str, items: list[dict[str, Any]], is_full: bool) -> None:
        with self._lock:
            st = self._agents.setdefault(agent_id, AgentState(agent_id=agent_id))
            if is_full:
                st.securities = {}
            for sec in items:
                code = sec.get("code")
                if code:
                    st.securities[code] = sec

    def set_tick(self, agent_id: str, tick: dict[str, Any]) -> None:
        code = tick.get("code")
        if not code:
            return
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).ticks[code] = tick

    def set_order_book(self, agent_id: str, ob: dict[str, Any]) -> None:
        code = ob.get("code")
        if not code:
            return
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).order_books[code] = ob

    def remove_agent(self, agent_id: str) -> None:
        with self._lock:
            self._agents.pop(agent_id, None)

    # ---- reads (from FastAPI) ----
    def agent_ids(self) -> list[str]:
        with self._lock:
            return list(self._agents.keys())

    def status(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Per-agent status: link lamp, diagnostics, last_seen, register summary."""
        with self._lock:
            agents = (
                [self._agents[agent_id]] if agent_id and agent_id in self._agents
                else list(self._agents.values())
            )
            out = []
            for st in agents:
                out.append({
                    "agent_id": st.agent_id,
                    "link": _link_lamp(st.last_seen_ms, self.link_fresh_sec),
                    "link_fresh_sec": self.link_fresh_sec,
                    "last_seen_ms": st.last_seen_ms,
                    "last_seen_age_ms": _now_ms() - st.last_seen_ms,
                    "connected_at_ms": st.connected_at_ms,
                    "last_seq": st.last_seq,
                    "register": st.register,
                    "last_heartbeat": st.last_heartbeat,
                    "diagnostics": st.diagnostics,
                    "last_alert": st.last_alert,
                    "securities_count": len(st.securities),
                    "tick_codes": sorted(st.ticks.keys()),
                    "order_book_codes": sorted(st.order_books.keys()),
                })
            return out

    def securities(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            st = self._pick(agent_id)
            return list(st.securities.values()) if st else []

    def tick(self, code: str, agent_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            st = self._pick(agent_id)
            return st.ticks.get(code) if st else None

    def order_book(self, code: str, agent_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            st = self._pick(agent_id)
            return st.order_books.get(code) if st else None

    def params(self, agent_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            st = self._pick(agent_id)
            return st.params if st else None

    def _pick(self, agent_id: str | None) -> AgentState | None:
        """Pick a named agent, or the single connected one when unambiguous."""
        if agent_id is not None:
            return self._agents.get(agent_id)
        if len(self._agents) == 1:
            return next(iter(self._agents.values()))
        return None
