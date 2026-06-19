"""LLM decision agents for team-46 — strictly via the Klod/Lineman gateway.

Boris rule: ALL LLM access goes through Lineman/Klod, never a raw provider key.
The gateway is an HTTP endpoint on the WireGuard net (no auth header, agent name
in the body): POST {LINEMAN_BASE_URL}/api/klod/ask
  -> {"text": "...", "model_used": "...", ...}

Agents reconstruct the go-bot ML-service multi-agent contract (the original
prompts are not public): EvaluateProposal (PM gate), CriticVerify, AnalyzeEvent,
EvaluateExit, ClassifyNews. Every agent DEGRADES gracefully on any failure /
timeout / unconfigured gateway — exactly like llm/gate.go falls back to a
full-size approval when the LLM is unavailable.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

_DEFAULT_BASE = "http://10.66.0.1:9090"      # Lineman/Klod on smain via WireGuard
_DEFAULT_AGENT = "klod-stl"
_FAST = "deepseek-fast"      # deepseek-chat — PM / exit / news
_REASON = "deepseek-reason"  # deepseek-reasoner — critic


class KlodClient:
    """Thin async client for the Klod gateway. `available` is False when the bot
    is configured to skip the LLM (then all agents run degraded)."""

    def __init__(self, base_url: str | None = None, agent: str | None = None,
                 timeout: float = 20.0, enabled: bool = True) -> None:
        self.base_url = (base_url or os.environ.get("LINEMAN_BASE_URL", _DEFAULT_BASE)).rstrip("/")
        self.agent = agent or os.environ.get("TRADER_AGENT_NAME", _DEFAULT_AGENT)
        self.timeout = timeout
        self._enabled = enabled

    @property
    def available(self) -> bool:
        return self._enabled and bool(self.base_url)

    async def ask(self, prompt: str, model_hint: str = _FAST, max_tokens: int = 1000) -> str:
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(
                f"{self.base_url}/api/klod/ask",
                json={"agent": self.agent, "prompt": prompt,
                      "model_hint": model_hint, "max_tokens": max_tokens},
            )
            r.raise_for_status()
            return r.json().get("text", "")


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from an LLM reply (tolerates code fences/prose)."""
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════════════
#  1. EvaluateProposal — portfolio-manager gate
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ProposalVerdict:
    verdict: str        # "APPROVE" | "REJECT" | "DOWNSIZE"
    size_factor: float  # 1.0 approve, <1 downsize, 0 reject
    confidence: float
    reasoning: str
    degraded: bool = False


_DEGRADED_APPROVE = ProposalVerdict("APPROVE", 1.0, 0.0, "degraded: LLM unavailable", True)


async def evaluate_proposal(client: KlodClient, proposal: dict) -> ProposalVerdict:
    """PM risk gate. Returns APPROVE/REJECT/DOWNSIZE + size_factor. On any failure
    returns a degraded full-size approval (strategy keeps working)."""
    if not client.available:
        return _DEGRADED_APPROVE
    prompt = (
        "You are the portfolio-manager risk gate of a MOEX intraday futures bot. "
        "Evaluate the proposed trade and decide APPROVE (full size), DOWNSIZE "
        "(reduce, give size_factor in (0,1)), or REJECT (size_factor 0). Weigh "
        "order-flow (ofi), regime (hmm_state/hmm_prob), volatility (garch_vol), "
        "momentum, levels and current exposure (positions_json, daily_pnl_pct).\n"
        f"PROPOSAL:\n{json.dumps(proposal, ensure_ascii=False, default=str)}\n"
        'Respond with ONLY JSON: {"verdict":"APPROVE|DOWNSIZE|REJECT",'
        '"size_factor":1.0,"confidence":0.0,"reasoning":"..."}'
    )
    try:
        obj = _parse_json(await client.ask(prompt, _FAST, 400))
        verdict = str(obj.get("verdict", "APPROVE")).upper()
        if verdict not in ("APPROVE", "DOWNSIZE", "REJECT"):
            verdict = "APPROVE"
        sf = float(obj.get("size_factor", 1.0 if verdict == "APPROVE" else 0.0))
        sf = 0.0 if verdict == "REJECT" else max(0.0, min(1.0, sf))
        if verdict == "APPROVE":
            sf = 1.0
        return ProposalVerdict(verdict, sf, float(obj.get("confidence", 0.0)),
                               str(obj.get("reasoning", "")))
    except Exception:
        return _DEGRADED_APPROVE


# ════════════════════════════════════════════════════════════════════════════
#  2. CriticVerify — second-agent reviewer (reasoning model)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class CriticVerdict:
    approved: bool
    verdict: str   # "approve" | "reject" | "reduce_size"
    comment: str
    degraded: bool = False


async def critic_verify(client: KlodClient, original_action: str, ticker: str,
                        reasoning: str, confidence: float, market_context: str) -> CriticVerdict:
    if not client.available:
        return CriticVerdict(True, "approve", "degraded: LLM unavailable", True)
    prompt = (
        "You are a skeptical risk critic reviewing another agent's trade decision. "
        "Approve only if the reasoning is sound given the market context; otherwise "
        "reject or reduce_size.\n"
        f"TICKER: {ticker}\nORIGINAL_ACTION: {original_action}\n"
        f"REASONING: {reasoning}\nCONFIDENCE: {confidence}\nMARKET: {market_context}\n"
        'Respond with ONLY JSON: {"approved":true,"verdict":"approve|reject|reduce_size","comment":"..."}'
    )
    try:
        obj = _parse_json(await client.ask(prompt, _REASON, 400))
        verdict = str(obj.get("verdict", "approve")).lower()
        if verdict not in ("approve", "reject", "reduce_size"):
            verdict = "approve"
        approved = bool(obj.get("approved", verdict == "approve"))
        return CriticVerdict(approved, verdict, str(obj.get("comment", "")))
    except Exception:
        return CriticVerdict(True, "approve", "degraded: LLM unavailable", True)


# ════════════════════════════════════════════════════════════════════════════
#  3. AnalyzeEvent — event reaction
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class EventDecision:
    action: str        # BUY|SELL|HOLD|CONFIRM_FAST_PATH|OVERRIDE|UNCERTAIN
    ticker: str
    confidence: float
    reasoning: str
    abort_contrarian: bool = False
    degraded: bool = False


async def analyze_event(client: KlodClient, event: dict) -> EventDecision:
    ticker = str(event.get("ticker", ""))
    if not client.available:
        return EventDecision("HOLD", ticker, 0.0, "degraded: LLM unavailable", False, True)
    prompt = (
        "You react to a market event for a MOEX futures bot. Decide BUY, SELL, HOLD, "
        "CONFIRM_FAST_PATH, OVERRIDE or UNCERTAIN. Set abort_contrarian=true if a "
        "contrarian entry would be dangerous here (e.g. real news-driven move).\n"
        f"EVENT:\n{json.dumps(event, ensure_ascii=False, default=str)}\n"
        'Respond with ONLY JSON: {"action":"HOLD","confidence":0.0,'
        '"reasoning":"...","abort_contrarian":false}'
    )
    try:
        obj = _parse_json(await client.ask(prompt, _FAST, 400))
        action = str(obj.get("action", "HOLD")).upper()
        return EventDecision(action, ticker, float(obj.get("confidence", 0.0)),
                             str(obj.get("reasoning", "")),
                             bool(obj.get("abort_contrarian", False)))
    except Exception:
        return EventDecision("HOLD", ticker, 0.0, "degraded: LLM unavailable", False, True)


# ════════════════════════════════════════════════════════════════════════════
#  4. EvaluateExit — exit manager
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ExitVerdict:
    action: str   # "HOLD" | "CLOSE" | "TIGHTEN_STOP"
    confidence: float
    degraded: bool = False


async def evaluate_exit(client: KlodClient, exit_req: dict) -> ExitVerdict:
    if not client.available:
        return ExitVerdict("HOLD", 0.0, True)
    prompt = (
        "You manage an open MOEX futures position. Decide HOLD, CLOSE, or "
        "TIGHTEN_STOP based on P&L, holding time, order-flow and regime.\n"
        f"POSITION:\n{json.dumps(exit_req, ensure_ascii=False, default=str)}\n"
        'Respond with ONLY JSON: {"action":"HOLD|CLOSE|TIGHTEN_STOP","confidence":0.0}'
    )
    try:
        obj = _parse_json(await client.ask(prompt, _FAST, 200))
        action = str(obj.get("action", "HOLD")).upper()
        if action not in ("HOLD", "CLOSE", "TIGHTEN_STOP"):
            action = "HOLD"
        return ExitVerdict(action, float(obj.get("confidence", 0.0)))
    except Exception:
        return ExitVerdict("HOLD", 0.0, True)


# ════════════════════════════════════════════════════════════════════════════
#  5. ClassifyNews
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class NewsClassification:
    severity: int       # 1-10
    category: str       # earnings|macro|corporate|geopolitical|other
    direction: str      # bullish|bearish|neutral
    confidence: float
    degraded: bool = False


async def classify_news(client: KlodClient, text: str, source: str = "", ticker: str = "") -> NewsClassification:
    if not client.available:
        return NewsClassification(0, "other", "neutral", 0.0, True)
    prompt = (
        "Classify this Russian-market news headline for a MOEX futures bot.\n"
        f"TICKER: {ticker or 'market-wide'}\nSOURCE: {source}\nTEXT: {text}\n"
        'Respond with ONLY JSON: {"severity":1,"category":"earnings|macro|corporate|geopolitical|other",'
        '"direction":"bullish|bearish|neutral","confidence":0.0}'
    )
    try:
        obj = _parse_json(await client.ask(prompt, _FAST, 200))
        cat = str(obj.get("category", "other")).lower()
        direction = str(obj.get("direction", "neutral")).lower()
        sev = int(obj.get("severity", 0) or 0)
        return NewsClassification(max(0, min(10, sev)), cat, direction, float(obj.get("confidence", 0.0)))
    except Exception:
        return NewsClassification(0, "other", "neutral", 0.0, True)


# ════════════════════════════════════════════════════════════════════════════
#  Gate — port of event.MaybeGate(llm/gate.go) semantics
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class GateResult:
    proceed: bool
    final_size_pct: float
    verdict: ProposalVerdict = field(default=None)  # type: ignore[assignment]


async def maybe_gate(client: KlodClient, proposal: dict) -> GateResult:
    """Consult the PM gate before opening. proposal must carry 'proposed_size_pct'.
    Rejected -> (False, 0). Approved/Downsized -> (True, size × size_factor)."""
    proposed = float(proposal.get("proposed_size_pct", 0.0))
    v = await evaluate_proposal(client, proposal)
    if v.verdict == "REJECT":
        return GateResult(False, 0.0, v)
    return GateResult(True, proposed * v.size_factor, v)
