---
name: journal-autoheal
description: agent journal-sync auto-restores robot fills from QUIK tables; partial-reduce avg bug fixed in both runtimes; showcase computes from healed fact
metadata: 
  node_type: memory
  type: project
  originSessionId: c8c08b1b-12e1-4762-8447-d7498bcee44e
---

Shipped 2026-07-14 (rev 1783978820 live, 1783979141 at 03:00): `quik_agent/internal/runner/
journalsync.go` — every 60s robot-tagged QUIK trades missing from the runner's book are
synthesized as OrderUpdates (client_id `rr:<robot>:qsync:<order>:<total>`, idempotent via
runner per-cid dedup). Guards: fresh heartbeat, 90s trade age, working orders skipped,
paper/manual/recon skipped, tail-cut skip, 30/cycle. Proven live immediately: restored the
12 lost 13.07 fills on agent restart (Lua re-publishes acc_trd with receipt=NOW, so the
session floor passed) → v2 book healed to SHORT 3 @ 88032.86, realized −1582.57 pts.
`tools/patch_runner_state_v1.ps1` (manual backfill) became unnecessary — do not run it;
its preconditions now correctly refuse.

Second bug fixed the same night: signed-space PARTIAL REDUCE reset avg to the closing
fill's price in BOTH `robot_runner/runtime.py` and `trader/lab/runtime.py`
(BacktestRuntime) — mis-realized every later close (−1583 true → −5111 via reset).
Library strategies only full-close, so backtest leaderboards unaffected; live partial
fills hit it. i9 sweep box repo copy NOT yet synced with this fix.

Known cosmetic: cross-midnight agent restart re-stamps acc_trd receipts → recon phantom
MISMATCH (journal fills dated yesterday < floor, table trades look fresh) until the
session rolls. Trades_ok=false also suppresses the chart's open-position rect (deployed
guard) — intended.

**Why:** QUIK is the source of truth; the showcase must converge to fact without operator
surgery. **How to apply:** future lost-fill incidents self-heal within ~2 min; check
`journal-sync:` lines in the agent console/runner.log. Related: [[runner-fill-crash-cp1251]],
[[agent-zombie-traps]].
