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
