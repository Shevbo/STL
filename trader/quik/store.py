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
    # agent's CURRENTLY effective hard limits (echoed via LimitsState); lets STL/UI
    # confirm a SetLimits push applied and surface a whitelist/cap divergence.
    limits_state: dict[str, Any] | None = None
    # security code -> Security dict
    securities: dict[str, dict[str, Any]] = field(default_factory=dict)
    # security code -> latest MarketDataTick dict
    ticks: dict[str, dict[str, Any]] = field(default_factory=dict)
    # security code -> latest OrderBook dict
    order_books: dict[str, dict[str, Any]] = field(default_factory=dict)
    # table name -> latest RawTable dict
    # {"columns": [...], "rows": [[...]], "received_at_unix_ms": int, "last_seen": ts}
    raw_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
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

    def set_limits_state(self, agent_id: str, limits: dict[str, Any]) -> None:
        """Store the agent's echoed effective limits (whitelist + caps + last push ts)."""
        with self._lock:
            self._agents.setdefault(agent_id, AgentState(agent_id=agent_id)).limits_state = limits

    def limits_state(self, agent_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            st = self._pick(agent_id)
            return st.limits_state if st else None

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

    def set_raw_table(
        self,
        agent_id: str,
        name: str,
        columns: list[str],
        rows: list[list[str]],
        received_at_unix_ms: int,
    ) -> None:
        """Store the latest generic QUIK table verbatim under its sheet name."""
        if not name:
            return
        with self._lock:
            st = self._agents.setdefault(agent_id, AgentState(agent_id=agent_id))
            st.raw_tables[name] = {
                "columns": list(columns),
                "rows": [list(r) for r in rows],
                "received_at_unix_ms": received_at_unix_ms,
                "last_seen": _now_ms(),
            }

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
                    "limits_state": st.limits_state,
                    "securities_count": len(st.securities),
                    "tick_codes": sorted(st.ticks.keys()),
                    "order_book_codes": sorted(st.order_books.keys()),
                    "raw_table_names": sorted(st.raw_tables.keys()),
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

    def list_raw_tables(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Summaries of generic tables. When agent_id is None, list across agents.

        Each summary: {agent_id, name, columns_count, rows_count, received_at_unix_ms}.
        """
        with self._lock:
            if agent_id is not None:
                agents = [self._agents[agent_id]] if agent_id in self._agents else []
            else:
                agents = list(self._agents.values())
            out: list[dict[str, Any]] = []
            for st in agents:
                for name, tbl in st.raw_tables.items():
                    out.append({
                        "agent_id": st.agent_id,
                        "name": name,
                        "columns_count": len(tbl.get("columns", [])),
                        "rows_count": len(tbl.get("rows", [])),
                        "received_at_unix_ms": tbl.get("received_at_unix_ms", 0),
                    })
            return out

    def get_raw_table(self, name: str, agent_id: str | None = None) -> dict[str, Any] | None:
        """Full table: {columns, rows, received_at_unix_ms}.

        With agent_id, looks only at that agent. Without, picks the single agent
        when unambiguous, else searches all agents for the first matching name.
        """
        with self._lock:
            if agent_id is not None:
                st = self._agents.get(agent_id)
                tbl = st.raw_tables.get(name) if st else None
                return self._table_view(tbl)
            st = self._pick(agent_id)
            if st is not None and name in st.raw_tables:
                return self._table_view(st.raw_tables[name])
            for st in self._agents.values():
                if name in st.raw_tables:
                    return self._table_view(st.raw_tables[name])
            return None

    @staticmethod
    def _table_view(tbl: dict[str, Any] | None) -> dict[str, Any] | None:
        if tbl is None:
            return None
        return {
            "columns": tbl.get("columns", []),
            "rows": tbl.get("rows", []),
            "received_at_unix_ms": tbl.get("received_at_unix_ms", 0),
        }

    def _pick(self, agent_id: str | None) -> AgentState | None:
        """Pick a named agent, or the single live one when unambiguous.

        With no explicit id: the single connected agent, else the single link-GREEN
        (fresh) one. The store accumulates stale entries (a pre-Register id, dead
        probes, old sessions); preferring the lone green agent keeps the read routes
        (стакан/tick/params) working without the UI having to pass agent_id, mirroring
        the order API's _resolve_agent."""
        if agent_id is not None:
            return self._agents.get(agent_id)
        if len(self._agents) == 1:
            return next(iter(self._agents.values()))
        green = [
            st for st in self._agents.values()
            if _link_lamp(st.last_seen_ms, self.link_fresh_sec) == "green"
        ]
        if len(green) == 1:
            return green[0]
        return None
