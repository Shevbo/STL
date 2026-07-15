"""LLM decision agents for team-46 — strictly via the Klod/Lineman gateway.

Boris rule: ALL LLM access goes through Lineman/Klod, never a raw provider key.
The gateway is an HTTP endpoint on the WireGuard net (no auth header, agent name
in the body): POST {LINEMAN_BASE_URL}/api/klod/ask
  -> {"text": "...", "model_used": "...", ...}

Reconstructs the go-bot ML-service contract (the original prompts are not
public): EvaluateProposal (PM gate). It DEGRADES gracefully on any failure /
timeout / unconfigured gateway — exactly like llm/gate.go falls back to a
full-size approval when the LLM is unavailable.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

_DEFAULT_BASE = "http://10.66.0.1:9090"      # Lineman/Klod on smain via WireGuard
_DEFAULT_AGENT = "klod-stl"
_FAST = "deepseek-fast"      # deepseek-chat — PM gate


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
