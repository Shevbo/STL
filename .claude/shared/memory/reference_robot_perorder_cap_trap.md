---
name: reference_robot_perorder_cap_trap
description: "robot stuck unable to close when max_position > per-order cap; effective cap = min(both sides), raise order + fix"
metadata: 
  node_type: memory
  type: reference
  originSessionId: b67e33cf-a0ef-48ee-80ae-f9c7bc8b6851
  modified: 2026-07-21T07:23:37.318Z
---

2026-07-21: REAL agent robot lxk22 (MACD trend A · RIU6) stuck long +14 unable to exit ~13h.

**Root cause:** trend/averaging strategies close the whole book with ONE order of `abs(position)` (`trader/lab/strategies/library.py` place_order). `QUIK_MAX_CONTRACTS_PER_ORDER=10` < 14 rejected every close (`ReasonQtyPerOrder`, `quik_agent/internal/trade/limits.go`). Robot could OPEN via 1-lot averaging but never CLOSE once position > cap. Reject was silent (runner marked order `rejected`, no reason surfaced) — hid it for hours.

**Effective per-order cap = min(agent_config.json backstop on VDS, STL env push).** A running agent ignores a WIDER push live (adopts only tighter); it re-reads a wider cap ONLY at start. To raise: bump STL env (`~/.shectory_trade.env`) AND agent_config.json (VDS, operator-only), restart STL FIRST then the agent. Read effective via `GET /api/v1/quik/orders/config` (`agent_limits`).

**Fix (written, NOT yet committed/deployed):** `trade.CheckArmable` refuses arming real when `max_position > per-order/working cap`; runner surfaces reject reason to the detailed log (`robot_runner/runtime.py` on_order_event). Tests green (Go on hoster, py local). Deploy = agent+runner publish.

**Resolution used:** operator raised cap to 18 both sides + reconciled robot to flat (belief 0). Monitor script: hoster `~/monitor_lxk.py`.

Related: [[reference_agent_robot_pnl]], [[reference_runner_fill_crash]], [[project_live_fvg_robot]].
