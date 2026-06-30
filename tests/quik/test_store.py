"""Unit tests for the QUIK agent in-memory store (link lamp + apply logic)."""

import time

from trader.quik.store import QuikAgentStore


def test_link_lamp_green_yellow_red():
    store = QuikAgentStore(link_fresh_sec=1)
    st = store.ensure_agent("a1")
    now_ms = int(time.time() * 1000)

    st.last_seen_ms = now_ms
    assert store.status("a1")[0]["link"] == "green"

    st.last_seen_ms = now_ms - 1500  # > 1s, <= 3s
    assert store.status("a1")[0]["link"] == "yellow"

    st.last_seen_ms = now_ms - 5000  # > 3s
    assert store.status("a1")[0]["link"] == "red"


def test_securities_full_replace_vs_delta():
    store = QuikAgentStore()
    store.apply_securities("a1", [{"code": "RIU6", "name": "RTS"}], is_full=True)
    store.apply_securities("a1", [{"code": "SiU6", "name": "USD"}], is_full=False)
    codes = {s["code"] for s in store.securities("a1")}
    assert codes == {"RIU6", "SiU6"}

    store.apply_securities("a1", [{"code": "BRU6", "name": "Brent"}], is_full=True)
    codes = {s["code"] for s in store.securities("a1")}
    assert codes == {"BRU6"}


def test_pick_single_agent_when_unambiguous():
    store = QuikAgentStore()
    store.set_tick("only", {"code": "RIU6", "last": 1.0})
    # no agent_id given, single agent -> resolved
    assert store.tick("RIU6") is not None
    # second agent makes it ambiguous -> None without explicit id
    store.set_tick("second", {"code": "RIU6", "last": 2.0})
    assert store.tick("RIU6") is None
    assert store.tick("RIU6", "only")["last"] == 1.0


def test_pick_prefers_single_green_when_others_stale():
    """A stale (red) leftover agent must not block resolving the single live one.

    The store accumulates stale entries (a pre-Register id, dead probes, old
    sessions). When exactly one agent is link-green, an unspecified read resolves
    it instead of returning None — otherwise the стакан/tick routes 404 even though
    one agent is clearly live."""
    store = QuikAgentStore(link_fresh_sec=1)
    now_ms = int(time.time() * 1000)
    # stale leftover (last seen long ago -> red)
    stale = store.ensure_agent("stale")
    stale.last_seen_ms = now_ms - 60_000
    # live agent with fresh data (green)
    store.set_order_book("live", {"code": "GZU6", "bids": [], "asks": []})
    live = store.ensure_agent("live")
    live.last_seen_ms = now_ms
    assert store.order_book("GZU6") is not None
    assert store.order_book("GZU6")["code"] == "GZU6"
