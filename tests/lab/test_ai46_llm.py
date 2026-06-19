"""Tests for the team-46 LLM agents (mocked gateway, no network)."""
from trader.lab.ai46 import llm as L


class FakeClient:
    """Stand-in for KlodClient. `reply` is returned by ask(); available toggles."""
    def __init__(self, reply: str = "", available: bool = True):
        self.reply = reply
        self.available = available

    async def ask(self, prompt, model_hint=L._FAST, max_tokens=1000):
        return self.reply


class RaisingClient:
    available = True

    async def ask(self, *a, **k):
        raise RuntimeError("gateway down")


# ── _parse_json tolerance ─────────────────────────────────────────────────────

def test_parse_json_tolerates_fences_and_prose():
    assert L._parse_json('here you go ```json\n{"a":1}\n``` done') == {"a": 1}
    assert L._parse_json("no json here") == {}


# ── EvaluateProposal / gate ───────────────────────────────────────────────────

async def test_proposal_degraded_when_unavailable():
    v = await L.evaluate_proposal(FakeClient(available=False), {"ticker": "RIU6"})
    assert v.verdict == "APPROVE" and v.size_factor == 1.0 and v.degraded


async def test_proposal_approve_parsed():
    c = FakeClient('{"verdict":"APPROVE","size_factor":0.5,"confidence":0.8}')
    v = await L.evaluate_proposal(c, {"ticker": "RIU6"})
    assert v.verdict == "APPROVE" and v.size_factor == 1.0  # APPROVE forces full size
    assert not v.degraded


async def test_proposal_downsize_applies_factor():
    c = FakeClient('{"verdict":"DOWNSIZE","size_factor":0.4,"confidence":0.6}')
    v = await L.evaluate_proposal(c, {})
    assert v.verdict == "DOWNSIZE" and abs(v.size_factor - 0.4) < 1e-9


async def test_proposal_degraded_on_error():
    v = await L.evaluate_proposal(RaisingClient(), {})
    assert v.degraded and v.verdict == "APPROVE" and v.size_factor == 1.0


async def test_maybe_gate_reject_blocks():
    c = FakeClient('{"verdict":"REJECT","size_factor":0}')
    g = await L.maybe_gate(c, {"proposed_size_pct": 10})
    assert g.proceed is False and g.final_size_pct == 0.0


async def test_maybe_gate_downsize_scales():
    c = FakeClient('{"verdict":"DOWNSIZE","size_factor":0.3}')
    g = await L.maybe_gate(c, {"proposed_size_pct": 10})
    assert g.proceed is True and abs(g.final_size_pct - 3.0) < 1e-9


async def test_maybe_gate_degraded_full_size():
    g = await L.maybe_gate(FakeClient(available=False), {"proposed_size_pct": 7})
    assert g.proceed is True and abs(g.final_size_pct - 7.0) < 1e-9


# ── Critic / Exit / News ──────────────────────────────────────────────────────

async def test_critic_parsed_and_degraded():
    c = FakeClient('{"approved":false,"verdict":"reject","comment":"overextended"}')
    v = await L.critic_verify(c, "BUY", "RIU6", "ofi spike", 0.7, "trend_up")
    assert v.approved is False and v.verdict == "reject"
    d = await L.critic_verify(FakeClient(available=False), "BUY", "RIU6", "", 0.0, "")
    assert d.approved is True and d.degraded


async def test_exit_parsed_and_degraded():
    v = await L.evaluate_exit(FakeClient('{"action":"CLOSE","confidence":0.9}'), {"pnl_pct": -2})
    assert v.action == "CLOSE"
    d = await L.evaluate_exit(RaisingClient(), {})
    assert d.action == "HOLD" and d.degraded


async def test_news_parsed_and_degraded():
    c = FakeClient('{"severity":8,"category":"geopolitical","direction":"bearish","confidence":0.7}')
    n = await L.classify_news(c, "headline")
    assert n.severity == 8 and n.category == "geopolitical" and n.direction == "bearish"
    d = await L.classify_news(FakeClient(available=False), "x")
    assert d.severity == 0 and d.degraded
