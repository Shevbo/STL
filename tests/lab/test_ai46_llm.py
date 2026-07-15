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
