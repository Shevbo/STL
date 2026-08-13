---
name: reference_agent_zombie_traps
description: "Two QUIK-agent/runner \"zombie\" traps found 2026-07-13 + their fixes (runner symbol KeyError, DDE watchdog spam)"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 851d73a4-c9a9-4181-8a39-c141a72dc54e
---

Two independent defects surfaced diagnosing the VDS stand while prepping the new
real-money trading week (2026-07-13). Both are recognizable by an exact log line.

**Trap 1 — runner `'symbol'` KeyError (robot never trades).**
Symptom: `runner: [error] host.on_bar_failed error="'symbol'" robot_id=<id>` on
EVERY bar (seen: agent-fvg-RIU6-v3, paper). Root cause: `make_on_bar`
(`trader/lab/strategies/library.py`) reads `params["symbol"]`, but the agent
runner stores the symbol as a SEPARATE authoritative spec field `spec["symbol"]`
(it drives tape/tick/order routing in `robot_runner/host.py`). If a deploy or
light `SetRobotParams` route sends `params_json` WITHOUT `symbol`, the strategy
KeyErrors every bar. v2 had symbol in params (worked); v3 didn't (wedged). Fix:
`host.py` backfills `spec["params"]["symbol"] = spec["symbol"]` at BOTH `deploy`
and `set_params`. Ships inside `robot-runner.exe`.

**Trap 2 — DDE watchdog zombie (log spam + fake reconnect count).**
Symptom: `watchdog: DDE hung (dde server not alive) - read-only restart attempt
#N` + `DDE restart issued`, every heartbeat forever; inflates the reconnect
counter on the local stand (127.0.0.1:8071) to hundreds (892 seen at ~30h
uptime, one per 2-min backoff). Root cause: `cmd/quik-agent/main.go` wired the
watchdog with raw `quikdde.Alive`; DDE is RETIRED so `Alive()` is always false ->
`stale()` returns "dde server not alive" every check; `RestartDDE` is a no-op.
The HEALTH path already dodged this with `quikdde.Alive() || !quikdde.LegacyEnabled()`
(link.go/stream.go) but the watchdog wiring did not. Fix: gate `go wd.Run(ctx)`
on `quikdde.LegacyEnabled()` — the DDE-recovery watchdog runs ONLY when
`SHECTORY_ENABLE_DDE=1`. Ships in the agent exe.

Both verified 2 ways: pytest `tests/runner/test_host.py` (host backfill +
strategy-level `KeyError('symbol')` proof) and `go test ./internal/watchdog/`
(new) + full `go build ./...` on the hoster build tree.

A third fix rode the same release: the stand's `runner.log` link (GET
/logs/runner) 404'd because the runner's stdout only went to the agent console
(`status.Deps.LogPaths` empty) — added `runner.FileTee` to tee runner+supervisor
lines to `<runnerDir>/runner.log` and wired `LogPaths["runner"]`; the `стратегия`
link (/strategy/{id}) 404'd because `strategies_doc.json` was never shipped to the
VDS (older build), now bundled next to the runner (main branch, commits 1de5aba +
07a02d2).

DEPLOY STATE 2026-07-13: APPLIED to the live agent. Published as build_rev
**1783924855** (agent exe rebuilt with all fixes; zip bundles the symbol-fixed
robot-runner.exe + strategies_doc.json, all sha256-verified local==staged==zip
entry), then the operator authorized the immediate self-update. Verified LIVE via
`/api/v1/quik/status` + `/api/v1/quik/robots-mirror`: agent 9618 link=green,
rev=1783924855, **reconnects=0** (was 926 -> watchdog zombie dead), and
agent-fvg-RIU6-v3 `params_json` now carries `symbol=RIU6` with note='' (symbol
backfill working; v3 no longer KeyErrors). The runner.log/стратегия links are
agent-local (127.0.0.1:8071) so verify them by clicking on the VDS. GOTCHA that
nearly broke the deploy: the live agent's `agent_id` is its **host_name "9618"**,
NOT "shectory-quik-agent" (that string is the `agent_version` label on the stand
badge). Trigger self-update against `9618`
(`POST /api/v1/quik/agent/9618/self-update`); the stale "quik-agent" entry in
`/api/v1/quik/status` is a dead registration (link=red). The immediate live
trigger is gated by the deploy classifier and needs an explicit operator "go".

Deploy = rebuild `robot-runner.exe` (Windows/PyInstaller) + agent exe (hoster) ->
`publish_quik_agent.sh [agent_id]` -> operator applies on the VDS (operator-gated).

FOLLOW-UP 2026-07-13 (stand links redo, main commits 4597d2f + 29f4033): the two
stand links were reworked after a first wrong attempt. «runner.log» -> «Детальный
лог робота»: the runner now appends SIGNIFICANT per-robot events
(ORDER/CANCEL/FILL/SKIP/REJECT/SIGNAL/ERROR/LIFECYCLE) to `<data>/logs/<id>.log`
via `AgentRuntime.event()`; agent serves `GET /logs/robot/{id}`. «стратегия» -> a
new Svelte deep-link `/?strategy=<id>` (reuses MustDescription + fvg/atr/ladder
schematics, read-only); the stand links to the ABSOLUTE
`https://stl.shectory.ru/?strategy=<id>`. Frontend is LIVE (verified in-browser).
Agent+runner APPLIED to agent **9618** as build_rev **1783928193** (all
sha256-verified local==staged==zip); verified live green, and post-restart the
robots-mirror shows agent-fvg-RIU6-v2 (real) and -v3 (paper) both running clean
(note='') — v3 now actually trades (pos=1), confirming the symbol fix
end-to-end. Frontend deploy = build local + scp `frontend/dist` to
`~/apps/shectory-trader/frontend/dist` (no restart). The per-robot log +
`/logs/robot/{id}` link are agent-local (127.0.0.1:8071) — verify by clicking on
the VDS; the strategy link is the live absolute STL URL.

Related: [[project_robot_on_quik_agent]] [[project_live_fvg_robot]]
[[project_quik_agent_phase1]] [[project_agent_local_showcase]]
