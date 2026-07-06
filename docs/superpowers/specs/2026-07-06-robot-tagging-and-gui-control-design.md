# Robot Tagging (recon) + GUI Robot Control — Design

Date: 2026-07-06
Status: approved by operator (chat), pending spec review

## Purpose

Two coupled features on the QUIK agent, driven by the operator's requirement to run
agent-hosted robots on the SAME QUIK account they also trade MANUALLY, without the two
being confused, and to manage robots (params + paper/real mode) from the showcase GUI.

- **Feature A — Tag-model recon:** attribute every robot order/trade by a robot tag
  written into the QUIK order COMMENT (surfacing as `brokerref`), so reconciliation tracks
  ONLY the agent's robots and treats everything untagged as manual (shown, never
  reconciled, never aligned). This replaces the account-net + `manual_offset` model.
- **Feature B — GUI robot control:** edit robot strategy params from the showcase (local
  page AND the authed STL mirror), and toggle a robot's paper/real mode from the LOCAL
  console only, behind a precondition gate + typed confirmation.

Tag-model is the foundation: arming a robot to real money is unsafe until its orders are
attributable and separable from manual trading.

## Non-goals

- No editing of `strategy_id` or `symbol` inline (that is a different robot — undeploy +
  redeploy). Editable = strategy params (`params_json`), `schedule`, `max_position`, mode.
- The paper/real toggle is NOT added to STL in any form (topological local-only).
- No change to the dual master-flag rule (STL env + agent config); mode toggle flips a
  robot's `paper` flag, it does NOT touch either master flag.
- `manual_offset` is retired (superseded by tagging); the config key + editor are removed.

## Feature A — Tag-model recon

### A1. Order placement stamps the robot tag

`quik_agent/lua/shectory_trade.lua` `handle_place` builds the QUIK transaction table
(currently ACTION/TRANS_ID/CLASSCODE/SECCODE/OPERATION/PRICE/QUANTITY/TYPE/ACCOUNT/
CLIENT_CODE at ~line 748). Add `trans.COMMENT = tostring(cmd.comment or "")` when non-empty.
In QUIK, a NEW_ORDER transaction's COMMENT lands in the order's `brokerref` field and is
inherited by that order's trades.

`quik_agent/internal/trade/bridge.go` `placeCmd` gains `Comment string \`json:"comment"\``.
`Manager` fills it from the order owner: for a robot order (client_id prefix `rr:<robotID>:`)
the tag is the robot ID; for a recon-align order (`recon:<planID>:<n>`) the tag is
`"recon"`; otherwise the raw client_id. The tag is the robot's IDENTITY, readable by the
operator in the QUIK order comment.

COMMENT length: QUIK's field length is bounded (verify on the VDS build). Robot IDs like
`agent-fvg-RIU6-v2` (~17 chars) are expected to fit. Contingency: if the build truncates,
switch to a compact per-robot tag persisted in `robots.json` (tag→robot_id map) and match
on that; the first-real-order smoke (A5) confirms which is needed.

### A2. Lua publishers carry the tag

`publish_acc_orders` rows append `r.brokerref or ""`; `publish_acc_trades` rows append
`r.brokerref or ""`. New row shapes (a later field, backward-additive):
- `acc_ord`: `[order_num, sec, active, price, balance, qty, brokerref]`
- `acc_trd`: `[trade_num, order_num, sec, price, qty, ts_ms, brokerref]`

### A3. Go decode

`accounts.Order` gains `Tag string`; `accounts.Trade` gains `Tag string`. The
`OrderFromRow`/`TradeFromRow` converters read the new trailing element (tolerant: absent =
"" so old Lua still decodes). `accounts.Snapshot` surfaces the tag on each Order/Trade.

### A4. recon redesign (`quik_agent/internal/recon`)

Attribution is by tag, not by order_num-vs-manual_offset. `recon.Inputs` gains, per robot,
the robot's tag (its ID). `AccView.Orders`/`Trades` carry `Tag`.

- **Tag → owner:** `robot ID` → that robot; `"recon"` → the agent's own align order
  (agent-owned: shown under the robot/agent side, NEVER manual, NEVER a mismatch, never
  re-generates an align step); empty / any other value → MANUAL.
- **Order classification:**
  - Robot-owned tag → must be a working order the robot knows about (by order_num); if the
    robot does not know it → `ROBOT_ORPHAN` (real mismatch: a tagged order the robot lost
    track of, e.g. across a restart).
  - `"recon"` tag → agent align order, in-flight; not a mismatch, not manual.
  - MANUAL (empty / unknown tag) → excluded from the robot recon, listed in a separate
    `manual` block, NEVER a mismatch, NEVER in an align plan.
  - A robot's believed working order absent from QUIK active orders → `MISSING` (mismatch).
- **Trade classification:** a QUIK trade whose tag matches a robot → that robot's fill;
  `"recon"` → agent; untagged/unknown → manual (ignored).
- **Position is CONTEXTUAL, not a hard reconcile.** QUIK's `futures_client_holding` is
  account-net (includes manual) and cannot be split per robot, and summing a robot's tagged
  fills is unreliable (the trades ring holds only the last 500). So the recon SIGNAL for a
  robot is orders + trades, NOT a position-net comparison:
  - the robot's working orders must all be present and correctly tagged in QUIK (no MISSING,
    no ROBOT_ORPHAN), AND
  - its RECENT fills bidirectionally match tagged QUIK trades (a robot fill with no matching
    tagged QUIK trade, or a tagged QUIK trade the robot never recorded, is a mismatch) —
    this catches a divergence between the robot's belief and reality within the live window,
    which is exactly what a net comparison would catch but without the ring-eviction
    fragility.
  The showcase DISPLAYS the robot's believed position and the account net side by side for
  context; neither is reconciled against the other. `manual_offset` is removed from `Inputs`
  and the code.
- **Transactions:** unchanged — the agent only ever tracks its own trans_ids, already
  robot/agent-scoped.
- **Align plan:** steps are generated ONLY over robot-attributed findings (ROBOT_ORPHAN
  cancel, MISSING fix_state, self-consistency close). A `MANUAL` order/position can NEVER
  produce a step. This is invariant #1 and gets an explicit test.

`Report` gains a `Manual` block: `{Orders []ManualOrder, AccountNet []PosLine}` for the
"Ручная торговля (не сверяется)" UI section. `Report.State` is OK when every robot is
self-consistent and has no ROBOT_ORPHAN/MISSING, regardless of manual activity.

### A5. Smoke (first real order)

The COMMENT→brokerref round-trip cannot be verified with paper (paper never reaches QUIK).
It is verified during the go-live first-real-order check: place one real robot order,
confirm the mirror's `acc_ord`/recon shows the order attributed to the robot (its ID in
the tag), and a concurrent manual order stays in the `manual` block.

## Feature B — GUI robot control

### B1. Param editing (local + STL, authed)

Editable: `params_json` fields, `schedule`, `max_position`.

- **Local:** `POST /api/robot/{id}/params` on the agent status server (loopback, no auth)
  with body `{"params_json": "...", "schedule": "...", "max_position": N}` (all optional;
  only present fields change). Validates JSON + ranges, updates `robots.json`, relays
  `SetRobotParams` to the runner, calls `Store.TouchParams(id)`.
- **STL mirror:** the authed page POSTs to STL, which relays via the existing
  `SetRobotParams` runner-control path (add `POST /api/v1/quik/robots/{id}/params` in
  `trader/api/quik_robots.py` if not already present). Same effect on the agent.
- **Source of truth:** `robots.json` on the agent is authoritative for agent-hosted robots;
  both edit paths write it; last-write-wins; the status mirror reflects the truth.

### B2. paper/real mode toggle (LOCAL ONLY, guarded)

- **Topology = enforcement:** `POST /api/robot/{id}/mode` exists ONLY on the agent's local
  status server. STL has NO such route, so it is unreachable from the mirror — local-only
  by construction, not merely hidden.
- **Body:** `{"paper": <bool>, "confirm_id": "<robotID>"}`.
- **Preconditions (agent auto-checks; ALL must hold, else 409 with the failing reason):**
  1. `confirm_id` exactly equals the robot's ID (typed confirmation).
  2. Robot is FLAT: believed position == 0, no working orders for it, no unresolved
     (pending/rejected) trans for it. Enforced in BOTH directions (paper→real and
     real→paper) so a real position is never orphaned by a demotion and a paper position
     never becomes a "real" belief on promotion.
  3. (Displayed, operator-confirmed, NOT auto — the agent cannot see Finam/STL) a checklist
     reminder that any STL-side real variant of the same symbol is stopped (single-path).
- **Mechanism:** a mode change re-deploys the robot's `RobotSpec` with `paper` flipped via
  the runner `DeployRobot` control. On a flat robot this is a clean reset into the new mode
  with no position/order carryover. Persisted to `robots.json`.
- **Reverse (real→paper):** same flat precondition; same re-deploy mechanism.

### B3. Page (`quik_agent/internal/status/page.html` + `frontend/public/agent-status.html`)

- Each robot row: an expandable params editor (fields from `params_json` + schedule +
  max_position) with a Save button → `/api/robot/{id}/params` (local) or the STL param
  endpoint (mirror). Editing available in both modes.
- Mode: a badge (paper grey / REAL red). The TOGGLE renders only when `IS_LOCAL`
  (`src===/api/status`); on the mirror the mode is read-only. Local toggle opens a guarded
  panel: auto-checked preconditions (green/red per item), the single-path checklist, and a
  text input to type the robot ID; the "Армировать в REAL" button enables only when every
  precondition is green AND the typed ID matches. Real arming is styled unmistakably (red).
- The recon section splits into "Мои роботы" (reconciled) and "Ручная торговля (не
  сверяется, справочно)".

## Safety invariants (each → a test)

1. An align plan NEVER contains a step targeting an untagged (MANUAL) order or the account
   net position.
2. `/api/robot/{id}/mode` is absent from STL (no route); only the agent local server serves
   it.
3. Mode flip is refused (409 + reason) unless the robot is FLAT (both directions) and the
   typed `confirm_id` matches.
4. A robot's real order carries its ID in the COMMENT/brokerref (verified in the A5 smoke).
5. A recon `MANUAL` classification never flips `Report.State` to MISMATCH.

## Data flow

Robot order → Manager (tags by owner) → `placeCmd.comment` → Lua `trans.COMMENT` → QUIK
`brokerref` → `acc_ord`/`acc_trd` rows → `accounts.Order/Trade.Tag` → recon attribution →
showcase "Мои роботы" vs "Ручная торговля". Param edit → local/STL → `SetRobotParams` →
runner + `robots.json`. Mode flip → local-only endpoint → flat-check → `DeployRobot`
(paper flipped) → runner + `robots.json`.

## Error handling

- Param edit: invalid JSON / out-of-range → 400 with the field. Unknown robot → 404.
- Mode flip: precondition fail → 409 with the exact failing precondition. Unknown robot →
  404. Non-local origin can't reach it (no route).
- A COMMENT that exceeds the QUIK field length is truncated by QUIK; the A5 smoke detects
  this and triggers the compact-tag contingency (A1).

## Testing

- Go units: recon tag-classification (robot vs MANUAL vs ROBOT_ORPHAN vs MISSING),
  self-consistency position check, invariant #1 (no align step over manual), invariant #5;
  mode-endpoint flat-gate (flat/non-flat both directions, confirm_id match/mismatch),
  invariant #2 (route absent on STL, present locally); param-edit validation + persistence;
  decode of the new `brokerref` trailing field (old-Lua tolerance).
- Lua: review-only + operator VDS smoke (brokerref carries COMMENT; length check).
- STL: pytest for the param relay endpoint.
- Frontend: the shared page is copied verbatim; mirror hides the mode toggle.
- Live: the A5 first-real-order smoke is part of go-live, not this build.

## Deploy

Usual chain: Go build/test on the hoster, agent rebuild + republish + self-update; the
`shectory_trade.lua` update is operator-manual on the VDS; STL param endpoint is a normal
backend deploy; the page is re-copied to `frontend/public/` and the frontend rebuilt.
