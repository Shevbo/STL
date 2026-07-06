# Rollout: live-fvg-RIU6 -> QUIK agent

Spec: `docs/superpowers/specs/2026-07-03-quik-side-robot-agent-design.md`.
Every step below that touches real trading is OPERATOR-GATED (HITL). The agent's
local persisted state is the runtime source of truth; STL commands are optional.

## Build + publish (one-time per release)

1. On WINDOWS (dev box): `bash deploy/build_runner.sh` -> `dist/runner/robot-runner.exe`.
2. Stage it: `scp dist/runner/robot-runner.exe hoster:~/quik_build/quik_agent/dist/`.
3. On the hoster: `bash ~/apps/shectory-trader/deploy/publish_quik_agent.sh [AGENT_ID]`
   — the release zip now carries BOTH exes; agent self-update delivers them together.

## Pre-flight (paper smoke on the QUIK VDS)

1. Agent self-updates (or operator drops both exes next to each other) + restart.
2. Verify console shows, in order:
   - `runner-bridge: runner bridge listening on 127.0.0.1:50071`
   - `robots:  runner=...robot-runner.exe  bridge=127.0.0.1:50071  persisted=0`
   - `runner-sup: runner started pid=...`
   - after ~15s: `ready: quik=ok dde=ok runner=ok robots=0 trading_enabled=false`
3. From STL, deploy PAPER first:
   `POST /api/v1/quik/robots/live-fvg-RIU6/deploy-agent`
   body: `{"strategy_id":"fvg","symbol":"RIU6","schedule":"09:00-23:55",
           "max_position":1,"paper":true,
           "params":{"symbol":"RIU6","qty":1,"avg_max":1,"tp_atr":60,
                     "min_frac":12,"avg_atr_n":5,"avg_step_atr":24}}`
4. Watch `GET /api/v1/quik/agent/{id}/robots`: heartbeat fresh, `last_bar_unix`
   advancing during the session, paper fills appearing on FVG signals.
5. Parity check: compare the paper fills against the STL backtest over the same bars.

## Cutover (operator-gated, real money)

1. STOP the STL-side robot — never both paths live at once:
   `POST /api/v1/robots/live-fvg-RIU6/undeploy` AND set `state_json.live_real=false` (DB).
2. Re-deploy to the agent with `"paper": false, "max_position": 1`.
3. OPERATOR arms the agent master flag (`agent_config.json quik_trading_enabled=true`)
   + QUIK terminal logged in + Lua bridge running. THIS IS THE HUMAN DECISION POINT.
   NOTE: execution account switches to the QUIK terminal's account (was Finam).
4. Verify the first real order lifecycle in the QUIK terminal + the STL mirror.

## Rollback / emergency

- KillSwitch (this agent only): the existing kill route — blocks new orders AND
  cancels working orders; the runner halts all robots. Positions stay open —
  close manually in QUIK if needed. `start-agent` on a robot clears the halt.
- `POST /api/v1/quik/robots/live-fvg-RIU6/undeploy-agent` removes the robot
  (persisted spec deleted; runner stops it).
- Reverse cutover: re-arm the STL-side robot (reverse of step 1).

## Reboot drill (zero-touch)

Reboot the QUIK VDS. Expected with NO manual steps: QUIK autostarts, the agent
(service/logon task) starts, the runner is supervised up, robots resume from
`robots\robots.json` + `robots\runner_state.json` (position/avg/realized/state
restored), the status mirror goes fresh in STL. The ONLY manual act that ever
remains: the master trading flag.

Note on recon after a restart: order OWNERSHIP (which robot/human placed a
working order) lives only in the agent's in-memory `trade.Manager` state, not
on disk. If the agent restarts while robot orders are still resting in QUIK,
those orders come back as `ORPHAN` in the recon block until they fill or
cancel — this is EXPECTED, not a bug, and the align plan will simply offer to
cancel them (or the robot's own logic re-places as needed on its next
signal).

## Local showcase (agent-hosted status + recon page)

The agent embeds its own operator page (`quik_agent/internal/status/page.html`,
served at `GET /` on the agent's loopback status server) showing feed/runner
health, deployed robots (position/P&L/heartbeat/params/signal), and a recon
(sverka) block that compares robots-claimed positions against QUIK's own
account tables. STL mirrors the same JSON opaquely (does not interpret it) at
`GET /api/v1/quik/agent-local-status` for remote viewing — see below.

### VDS operator steps (one-time per release, manual — no SSH to the VDS)

1. Update `quik_agent/lua/shectory_trade.lua` on the VDS (copy the new script
   version next to QUIK) — it now also publishes account tables (positions/
   orders/trades) for recon, change-gated same as ticks/books.
2. Update the sidecar `shectory_trade_config.lua` next to the script if new
   keys were added (it survives script updates by design; diff against
   `quik_agent/lua/shectory_trade_config.example.lua`).
3. Restart the Lua script in QUIK (reload from the QUIK Lua menu, or restart
   QUIK). Confirm the "Таблица всех сделок" (all-trades) window is still open
   — the tape feed depends on it.
4. Confirm the agent picks up new `agent_config.json` keys (additive, agent
   backfills sane defaults on first read if absent):
   - `status_port` — loopback port for the local page. Default `8071`. An
     explicit `0` is a deliberate persistent "disabled" (unlike other numeric
     fields, 0 is never re-defaulted back to 8071).
   - `status_snapshot_min_sec` — floors how often the agent mirrors its local
     status JSON up to STL, even if it changes every tick. Default `5`.
5. Open `http://127.0.0.1:8071` on the VDS (agent loopback only — not exposed
   externally) and confirm the page renders: header shows version/build/
   uptime/master-flag/link-to-STL; the "Здоровье" tiles are fresh; deployed
   robots show a heartbeat under ~45s; the "Сверка (recon)" banner reads OK
   (green) once the account-table feed above is live — it reads STALE
   (amber) until Lua step 1 is applied, which is expected and not an error.

### Align procedure — what the button does

When recon finds a mismatch, the page shows a plan (`recon.plan.steps`) and an
"Выполнить план (Execute plan)" button. Clicking it POSTs `{"plan_id"}` to
`/api/align` on the AGENT (never STL — this executes locally against QUIK).
The handler recomputes recon FRESH server-side and refuses to run a plan the
operator isn't currently looking at: if the fresh plan's id doesn't match the
one submitted, it returns **409** with the fresh recon so the page can update
and the operator re-confirms — this is the guard against acting on a stale
screenshot/plan. Steps execute sequentially and stop at the first failure
(remaining steps are reported `skipped`, not attempted) — a half-applied plan
must be re-evaluated from a fresh recon picture, never pushed through blind.
Step kinds: `cancel_order` (cancel a QUIK order the agent doesn't track) and
`fix_state` (clear a robot's phantom "working order" belief and pin its
position/avg to the plan's values). `close_position` is a HARD REFUSAL —
recon no longer generates it (an "excess account position" is contextual: it
can include the operator's own manual trading, not just robot activity, so
it is now reported for context only) and the Aligner has no wired capability
to place an order for it at all, so a stray/legacy `close_position` in a plan
always fails with an error and never reaches QUIK. No AlignExec wired ->
**503** before any body parse (an agent without an executor can't act on
anything). Снятие заявок (`cancel_order`, `CancelOrphan`) работает и при
выключенном master-флаге — как и KillSwitch, оно только снижает экспозицию;
`close_position` не размещает ордера вообще — сверка позиции теперь только
информационная. A repeated confirm of a plan
that already ran is refused (409, "план уже исполнен") — the page also
disables the button while a request is in flight, so a double-click cannot
fire the same align twice.

### STL mirror (remote viewing, read-only)

Open `https://stl.shectory.ru/agent-status.html?src=/api/v1/quik/agent-local-status&interval=10000`.
This is the SAME page (`frontend/public/agent-status.html`, a literal copy of
the agent's embedded HTML) pointed at STL's mirror endpoint instead of the
agent's own `/api/status`, polling every 10s instead of the local page's 1s
default — remote viewing only generates load on STL, not the VDS. The align
button on this URL POSTs `/api/align` RELATIVE TO STL's own origin, which does
not implement that route — treat the mirror as READ-ONLY monitoring; confirm
any align from the local `http://127.0.0.1:8071` page on the VDS itself.
Until the agent has sent at least one snapshot, the mirror endpoint returns
`{"status": null, "_received_at_ms": null}` — expected on first deploy or
after an agent restart, not a bug.

The mirror endpoint is auth-gated like every `/api/v1/quik/*` route: viewing
it requires a logged-in stl.shectory.ru session in the SAME browser; without
one the page shows the "HTTP 401 — войдите в STL в этом браузере" banner
instead of rendering. If the 401 banner appears while logged in, the session
expired — re-login; the page recovers on its next poll.

### Robot attribution — robots vs manual trading (tag model)

The account you trade on can hold BOTH robot orders and your own MANUAL trades.
Recon separates them by a robot-ID TAG the agent writes into every robot order's
QUIK `COMMENT` (surfacing as the order/trade `brokerref`): a robot order carries
its robot ID, an align order carries `"recon"`, and everything else — your manual
terminal trading — carries no tag. The page splits accordingly:

- **Мои роботы** — reconciled: each robot's working orders must be present + tagged
  in QUIK, and its recent fills must match tagged QUIK trades; a mismatch here
  (ROBOT_ORPHAN / MISSING / trade mismatch) is a real signal.
- **Ручная торговля (не сверяется, справочно)** — every untagged QUIK order + the
  account net position, shown for context. These NEVER turn recon red and NEVER
  appear in an align plan. So your manual +N lots and your manual orders sit here
  quietly; the align button can never touch them.

`manual_offset` is retired — the tag replaces it. The COMMENT→brokerref round-trip
is only exercised by a REAL order (paper never reaches QUIK); verify it in the
first-real-order smoke (below).

### Robot control from the GUI (params + paper/real mode)

- **Edit params** — expand a robot row: strategy params, `schedule`, `max_position`,
  Save. From the LOCAL page (`127.0.0.1:8071`) all three apply (via
  `/api/robot/{id}/params` → runner + robots.json). From the STL mirror only the
  strategy params (`params_json`) apply remotely (relayed via
  `POST /api/v1/quik/robots/{id}/params` → `SetRobotParams`); `schedule`/`max_position`
  edits there are disabled — change those from the local page.
- **paper ⇄ real toggle — LOCAL CONSOLE ONLY.** The mode endpoint exists ONLY on the
  agent (`POST /api/robot/{id}/mode`); STL has no such route, so arming real money
  is impossible from the mirror by construction. On the local page the arming panel
  requires ALL of: the robot FLAT (position 0, no working/in-flight order — the agent
  re-checks this server-side and returns **409** with the reason if not), the
  single-path checklist ticked (you confirm the STL/Finam variant of this symbol is
  stopped — the agent can't see Finam), and the robot ID typed exactly. Only then does
  "Армировать в REAL" enable. Both master flags are already ON, so `paper=false` starts
  real orders immediately — this toggle IS the arming action. De-arming (real→paper)
  needs the same FLAT state but no typing ceremony.

### First-real-order smoke (part of go-live, HITL)

When you arm the first real robot: place/allow one real order, then confirm on the
page that (1) it appears under "Мои роботы" attributed to the robot (its ID is the
order's tag), NOT under "Ручная торговля", and (2) a concurrent manual order you
place in the terminal stays under "Ручная торговля". That proves the COMMENT→brokerref
tag round-trips on this QUIK build (if the ID is truncated, the tag length exceeded the
COMMENT field — tell me and we switch to a compact tag).

### Clock-drift caveat

The page's "Дрейф часов" (clock drift) and "Биржевой лаг" (exchange lag)
tiles are only meaningful if the VDS clock is accurate. The VDS clock has
drifted minutes before. If drift looks implausible (large, or exchange lag
looks negative/huge), resync FIRST before treating it as a feed problem:
`w32tm /resync` on the VDS (operator-manual, no SSH access from here).

## Verify loops

- Python: `python -m pytest tests/runner/ tests/quik/ -q`
- Go (hoster): `go test ./internal/robots/ ./internal/runner/ ./internal/link/ ./internal/trade/`
