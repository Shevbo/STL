---
name: feedback-llm-via-lineman
description: "STRICT - all LLM access goes through the Lineman proxy, never a raw provider key"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a3701a33-3d27-427a-aa8b-04e822e31829
---

STRICT rule from Boris (2026-06-18): **access to ANY LLM is strictly through Lineman**, never a raw provider API key embedded in a service.

**Why:** central routing/rotation/audit/cost control; the federation runs all agents' LLM calls through Lineman (keymaster `used_by` for DEEPSEEK_API_KEY = "all-agents-via-lineman", "claude-code-router"). A raw key on the hoster bypasses that and is forbidden.

**How to apply:** for the MOEX AI Trading Bot — team-46 LLM gate (and anything else needing an LLM), call the **Lineman proxy** (`LINEMAN_PROXY1_URL` / `LINEMAN_IPROYAL_URL` + `GATEWAY_TOKEN` from the keymaster), OpenAI-compatible endpoint, model `deepseek-chat`/`deepseek-reasoner`. Do NOT fetch or store `DEEPSEEK_API_KEY` directly. Get Lineman URL + GATEWAY_TOKEN via the keymaster request-value flow (klod-stl not pre-approved → Boris TG approval), write into hoster env, never print. Related: [[reference-federation-access]], [[feedback-secrets-protocol]].
