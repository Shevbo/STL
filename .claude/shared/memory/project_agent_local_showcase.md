---
name: agent-local-showcase
description: "Local status page on the QUIK agent (127.0.0.1:8071) with recon vs QUIK tables and operator align; STL mirror; deployed 2026-07-06, awaiting VDS operator steps"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8e6fa383-4b49-4a0a-82de-e6b5cada8ab0
---

Agent local showcase SHIPPED 2026-07-06 (commits cfbbec2..39b8bb5, agent build_rev 1783327665).

- Local page http://127.0.0.1:8071 on the VDS (agent-embedded, `internal/status`): feed/RTT/exchange-lag/clock-drift, robots table, recon (positions/orders/trades/trans) vs QUIK account tables published by shectory_trade.lua.
- Align is operator-confirmed: plan frozen by id (hash of steps + absolute table stamps PosAtMs/OrdAtMs, NOT ages), 409 on stale, idempotency latch (only after >=1 OK step), close_position gated by master flag, cancel_order deliberately works disarmed (KillSwitch convention, adjudicated).
- Conventions: Step.Qty positive = SELL excess; align client_id `recon:<plan_id>:<step_index>`; pnl_rub only when PriceStep>0 AND StepCost>0; rejected trans surfaced 15 min; pure trade-mismatch does not flip recon State.
- STL mirror: `https://stl.shectory.ru/agent-status.html?src=/api/v1/quik/agent-local-status&interval=10000` (needs logged-in STL session; mutating controls hidden in mirror mode).
- Runbook: docs/runbooks/quik-robot-agent-rollout.md "Local showcase" section.

**Live-audit fix wave (2026-07-06, agent build 1783343744, commit 4d846df):** a multi-agent audit of the deployed showcase found 3 coupled data bugs. rtt_ms was epoch-sized (QUIK 32-bit Lua `%d` truncates 13-digit epoch-ms -> echoed pong t0 came back ~0); fixed by measuring RTT on the agent clock alone (pingSentMs atomic) + Lua `%.0f`. Recon was permanently STALE for a flat account (change-gate suppressed the first empty publish, AND an empty Lua table encoded as `{}` which the Go `[][]any` decoder dropped); fixed by encoding empty tables as `[]` + a first-pass/<15s keepalive gate. The keepalive would have rotated the recon plan ID every 16s (409 on every align confirm) -> split accounts.Store receipt stamp (freshness) from content stamp (plan ID). RTT verified fixed live (rtt_ms=24). See CLAUDE.md gotcha.

**Pending operator (no SSH to VDS):** copy the FIXED quik_agent/lua/shectory_trade.lua to the VDS and reload the script (this is what makes recon go GREEN and exchange_lag work — the account-publisher fixes are Lua-side). Also open the QUIK "Таблица всех сделок" window for exchange_lag. RTT already fixed by the agent build alone.

**Robot tagging + GUI control SHIPPED 2026-07-06 (agent build 1783370027, spec 623a3ef, plan f4848fa, commits 4cdbe48..154ab0a).** Recon now attributes orders/trades to a robot by a TAG the agent stamps into the QUIK order COMMENT (=> brokerref): robot ID for `rr:` orders (parsed FIRST-colon from the real 4-seg client_id `rr:<id>:<seq>:<uuid6>`, runtime.py:73), `"recon"` for align orders, empty for MANUAL (operator's own terminal trading). Untagged = manual: shown in a separate "Ручная торговля" block, NEVER reconciled, NEVER in an align plan (`manual_offset` retired; position is contextual only, reconcile signal = orders + recent trade-match). VERIFIED LIVE: with the operator manually trading RIU6 on the same account, recon reads OK and the manual position+orders sit in the manual block (was MISMATCH before). GUI control: edit params from the local page (all fields) or the STL mirror (`POST /api/v1/quik/robots/{id}/params`, params_json only); paper<->real toggle via `/api/robot/{id}/mode` exists ONLY on the agent (never STL) and REFUSES unless the robot is FLAT (position 0 + no working/in-flight order + status known) AND the typed robot-ID confirms — arming real money is local-console-only. The align Aligner is now structurally incapable of placing a close_position order (invariant enforced at the type level). Both master flags are already ON, so paper=false IS the arming action.

**Pending operator before go-live (HITL):** (1) reload the NEW quik_agent/lua/shectory_trade.lua on the VDS — it publishes `brokerref` on acc_ord/acc_trd so robot orders get attributed (old Lua works fine meanwhile: no brokerref => everything MANUAL => recon OK); needed before the first REAL robot order. (2) Go-live per runbook: stop the STL/Finam live-fvg-RIU6 (single-path), deploy the robot paper=false via STL, arm via the local page's guarded toggle, first-real-order smoke (confirm brokerref carries the robot ID; if truncated => compact-tag contingency).

Known backlog: clock_drift_ms=0 unset-default sentinel (minor); robot /logs links 404 (LogPaths empty); exchange lag relies on VDS tz=MSK; two STL set-params endpoints w/ divergent shapes (deprecate one someday). Related: [[project_robot_on_quik_agent]].
