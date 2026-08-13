---
name: reference_agent_robot_pnl
description: "Agent-robot screen P&L must use the runner's realized_pnl×coef, not the chart tail-replay; a recon MISMATCH can be manual trading OR a phantom from the stale acc_trd ring, not a robot divergence"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 851d73a4-c9a9-4181-8a39-c141a72dc54e
  modified: 2026-07-21T06:59:49.454Z
---

Two traps around the STL `?agent_robot=<id>` showcase P&L, found 2026-07-13
(fixed in main commit ac0cb05):

**P&L display (chart tail-replay is wrong for long-history live robots).** The
header badge + BacktestChart «Результат» computed net by replaying the mirror's
`recent_fills` (last 200) FROM FLAT. For a robot with more than ~200 fills of
history the tail starts MID-position, so replay-from-flat mis-attributes P&L —
it showed agent-fvg-RIU6-v2 as **−36 574 ₽** while the agent's OWN authoritative
number (`realized_pnl −7464 pts × coef 1.5333 = −11 444 ₽`, shown on its
127.0.0.1:8071 page) was right. Fix: `BacktestChart` gained a `netOverride` prop;
`AgentRobotScreen` feeds the runner's `realized_pnl × ₽/point` (`pnlRub`) as the
authoritative net and drives the badge + P&L+Маржа from `pnlRub` directly. Rule:
for a LIVE agent robot, trust the runner's `realized_pnl` (accumulated live),
NEVER a client-side replay of the fills tail. `coef = step_cost/price_step` from
`/api/v1/quik/params` (RIU6 ≈ 1.53 ₽/pt; BRU6 ≈ 766.6 ₽/pt step_cost 7.67 step
0.01). `realized_pnl` has NO commission. As of 2026-07-14 (commit 3132ba6) the
runner RESETS `realized_pnl` + fills to ZERO on a paper->real ARMING flip (the
paper era is not real money) — reset fires ONLY on the live paper->real
transition (`prev.paper && !new.paper` in host.py deploy), never on a restart or
params re-deploy, and bars/position are kept. So a REAL robot's P&L is real-only
from arming. (This supersedes the old "never reset at arming" note in CLAUDE.md.)

**Recon MISMATCH ≠ robot divergence.** `agent-local-status.recon.state=MISMATCH`
with `account_net` for the symbol is the WHOLE-account net (robot + the operator's
MANUAL trading), which by design never reconciles with a robot. The authoritative
"is the robot's book wrong?" signal is the agent's align **plan**: an EMPTY
`plan.steps` means the aligner found the robot's believed position CONSISTENT with
its tagged QUIK trades — no fix needed. Do NOT compare runner belief to the whole
account net, and do NOT sum a partial `quik.orders` list to infer the robot's net
(the list is a recent snapshot; it won't equal the true acc_pos net). See
[[reference_agent_zombie_traps]] [[project_live_fvg_robot]].

**Update 2026-07-21 (agent-ob-BRU6-v1 audit) — two more traps confirmed live:**

- **Integer fill prices in the trades table were a DISPLAY bug, not data loss.**
  `AgentRobotScreen.svelte` rendered fill/plan/order prices via `Math.round(price)`,
  truncating a sub-integer instrument's cents (BRU6 real fill 86.63 shown as 87,
  85.78 as 86). Invisible on RI/Si/GZ/SR (integer step), only bit BRU6 (step 0.01).
  Underlying prices AND the points-based P&L were correct. Fixed with a `fmtPrice`
  helper (`toLocaleString('ru-RU', {maximumFractionDigits: 2})`) at the 3 price
  render sites. ponytail: 2 dp fits every FORTS instrument traded now; a 0.001-tick
  one (NG) would need step-derived precision.

- **Phantom recon MISMATCH from the stale acc_trd RING.** QUIK's `acc_trd` is a
  rolling ring that KEEPS a prior session's trades for a thinly-traded instrument
  (BRU6's two 20.07 fills were still present 21.07 morning). The forward fill
  matcher scopes FillKeys to the MSK-session floor (`status.go` buildReconInputs),
  but the REVERSE pass (`recon.evalTrades`, "a tagged QUIK trade no fill claimed")
  had NO time filter, so it flagged those lingering yesterday trades → `trades_ok
  false` → MISMATCH on a robot that simply did not trade today. Position (-3)
  matched the QUIK positions table, `plan.steps=[]`. Root-cause fix: filter
  `Acc.Trades` by `mskMidnightMs(nowMs)` in buildReconInputs, symmetric with the
  FillKey floor (+ `TestBuildReconInputs_DropsPreSessionQuikTrades`). Reinforces
  the rule above: EMPTY plan.steps = no real divergence, do not align/reset.

- **cuid-named robots false-mismatch via brokerref truncation + partial-fill qty
  (FIXED rev 1784617019).** QUIK truncates the order COMMENT/brokerref tag to 20 chars.
  A 24-char cuid ID (`l90z0afzceesll5izjjg0g8w`) gets tag `l90z0afzceesll5izjjg` on its
  QUIK orders/trades, which `!= r.Tag` (full ID) → fills never matched forward AND its
  live orders silently fell through to MANUAL (hiding would-be ROBOT_ORPHANs). Second
  cause: the runner records a multi-lot fill as one FillKey qty=N, QUIK reports N qty-1
  rows; the exact-qty-per-row gate false-flagged every partial fill. Root-cause fix in
  `recon.go`: `quikTag(id)=id[:maxBrokerrefLen(20)]` used for `robotByTag` keying + the
  forward tag compare (mirrors QUIK's truncation; no-op on ≤20-char named robots), and
  the forward matcher now SUMS QUIK rows of the same (tag, order_num) to the fill qty
  instead of requiring one exact-qty row (+ 2 recon tests). NAMED live robots (`agent-*`,
  ≤20 chars) were always safe. Note: right after an agent self-update the acc_trd table
  reloads for a few seconds — recon can read a transient trades_ok=false until it
  republishes; wait ~15s before judging a post-restart MISMATCH.
