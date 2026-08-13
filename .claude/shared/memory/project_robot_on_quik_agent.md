---
name: project-robot-on-quik-agent
description: "planned — move LIVE robot execution to the QUIK-side agent so STL going down can't stop trading"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3c63f384-9e12-493f-b9fa-d37d88534a00
---

**Operator directive (2026-07-03), triggered by a prod outage:** port the LIVE-robot
execution function onto the agent running next to QUIK, WITHOUT a network bridge to
stl.shectory.ru. STL keeps ONLY monitoring + reporting. Then the robot keeps trading even if
STL is down. Root cause it addresses: robots run INSIDE the STL uvicorn process, so an STL
crash/thrash stops trading (SPOF) — see [[feedback-deploy-vds-safety]], [[project-live-fvg-robot]].

Design decisions still open (run brainstorming before building):
- Strategies + `LiveRuntime` are Python (`trader/lab/`); the QUIK agent is Go (`quik_agent/`).
  Options: (a) reimplement the strategy loop in Go on the agent; (b) run a small Python
  robot-runner process on the QUIK VDS reusing existing strategies. (b) is faster + reuses code.
- Robots currently trade via **Finam** (LiveRuntime -> self._tx), NOT QUIK. "Next to QUIK"
  implies orders via the local QUIK bridge = change of execution broker. The `BrokerInterface`
  + `QuikBroker` groundwork already exists (see [[project-quik-agent-phase1]]).
- Local state/log on the QUIK VDS; STL shows a read-only mirror (ping, fills, P&L).
- Market data locally from QUIK DDE (no dependency on STL/Finam feed).

Status: **CODE COMPLETE 2026-07-03** (autonomous run, commits 0c572b0..23901d1). Decisions:
embedded Python runner reusing library.py 1:1; QUIK-bridge execution; straight-to-real
1 contract; kill = halt+cancel, position stays; loopback gRPC IPC (port 50071).
Built: proto RobotSpec/control/RobotStatusReport + RunnerBridge service; Go
internal/robots (persisted store), internal/runner (bridge server + supervisor +
ticksource), link relay, manager runner-fan, main.go zero-touch wiring + "ready:" line;
Python robot_runner/ (bridge_client, bars, AgentRuntime with pre-send max_position,
RobotHost, main); PyInstaller build.spec + build_runner.sh (build on WINDOWS) +
publish_quik_agent.sh ships both exes in one zip; STL routes
/api/v1/quik/robots/{id}/*-agent + GET /api/v1/quik/agent/{id}/robots mirror.
**DEPLOYED 2026-07-04:** runner exe built (PyInstaller onefile 42MB) + published rev
1783145266; agent 9618 self-updated. Two field bugs fixed on the way: (1) self-update
apply-.bat copied only the agent exe — patched to install companion robot-runner.exe
(180cd99), needed a TWO-STEP publish (N+1 fixes the bat, N+2 delivers the runner);
(2) runner crashed with "Failed to load python312.dll / Не найден указанный модуль" —
VDS lacked Universal CRT; FIXED by operator installing vc_redist.x64.exe
(https://aka.ms/vs/17/release/vc_redist.x64.exe). Remember: any PyInstaller exe for
that VDS needs UCRT present.
Paper smoke robot `agent-fvg-RIU6-paper` deployed to agent 9618 (paper=true, FVG RIU6,
max_position=1): running, heartbeating, runner_healthy in the STL mirror
(GET /api/v1/quik/agent/9618/robots).
**DATA GAP (found 2026-07-04):** the QUIK DDE export table (`params` sheet) has NO
"Цена последней сделки" column — ticks carry bid only (ask header "Предл." wasn't
matched either; agent matcher fixed). Runner now builds bars via pick_price fallback
last->mid->bid/ask (commit c5d27da). For TRUE backtest parity ask the operator to add
the last-price column (and full "Предложение") to the exported QUIK table. Zero-touch
resume PROVEN live: agent restart -> persisted=1 -> replay deploy -> ready line all ok.
**SHOWCASE (2026-07-04, rev 1783166589):** per-robot page
`https://stl.shectory.ru/?agent_robot=<robot_id>` (AgentRobotScreen.svelte; &agent=<id>
optional, /api/v1/quik/robots-mirror picks the single green agent). RobotStatus now
carries signal_json (robot_runner/explain.py: FVG explainer mirrors sig_fvg exactly —
gap flags, body vs min_frac, waiting_for, planned/armed orders), working_orders,
bars_count and a spec echo (symbol/strategy/paper/schedule/params/max_position).
Verified live: signal_json flows in the mirror.
**PIPELINE PROVEN E2E (2026-07-04 evening):** operator added the last-price column and
restarted DDE output -> ticks carried last+ask, runner closed 2 bars, signal panel
counted "накопление баров: 2/3". BUT the DDE push died again after ~3 min (tick age
grew unbounded). Suspected: QUIK DDE output lacks "выводить при изменении данных" /
"возобновлять вывод при восстановлении связи" checkboxes — the agent watchdog restarts
its DDE server on staleness and QUIK never resumes pushing without auto-resume. Chart
"загрузка"-loop on the showcase fixed (stable-identity props, 545c56d).
NEXT SESSION (operator: "завтра торги откроются продолжим"): 1) fix DDE checkboxes ->
continuous ticks -> bar 3 closes -> full FVG diagnostics live on the showcase;
2) paper session watch + fills vs backtest parity; 3) HITL cutover of live-fvg-RIU6
(STOP the STL-side robot + live_real=false FIRST; account switches Finam->QUIK; deploy
paper=false; QUIK ГО ~22k RUB/contract; operator arms master flag). Runbook:
docs/runbooks/quik-robot-agent-rollout.md. Showcase URL:
https://stl.shectory.ru/?agent_robot=agent-fvg-RIU6-paper

**WENT REAL 2026-07-07 (operator-armed):** the live real-money robot is now
`agent-fvg-RIU6-v2` on the QUIK agent (mode=real via the guarded local toggle;
master_flag ON both sides). Single-path respected: the STL/Finam `live-fvg-RIU6` is
UNDEPLOYED (deployed=false) — so [[project-live-fvg-robot]] (Finam path) is retired in
favour of this. Robot ran ~24h autonomously, many real fills. Auth to read the mirror
off-session: mint a Bearer with `trader.auth.portal.make_session_token(email, SHECTORY_
AUTH_BRIDGE_SECRET)` in the app venv on hoster, then curl localhost:8000/api/v1/quik/
robots-mirror + /agent-local-status (agent-local-status payload IS the status: keys
agent/health/robots/recon/_received_at_ms).

**FINDING — reversal orders STACK live (not dupes; operator was right):** same-price
same-side fill PAIRS are the legit position-reversal (close abs(cur) + open base_unit,
library.py:74-76) — runner uses the SAME make_on_bar as backtest/scheduler, 1:1. BUT live
the reversal LIMIT order is placed at bars[-1].close; if the market moved it RESTS
unfilled, so cur never flips and each subsequent bar re-emits the reversal -> observed 8
resting rr: BUYs with position -1, max_position=1 (backtest fills same-bar so no stack).
Real over-exposure risk if price gaps through them. Fix (cancel this robot's working
orders before each new-bar on_bar, in host.tick_robot) was written+unit-tested then
PARKED/reverted per operator "не трогай" — redeploy only on explicit go. Deeper root: live
reversal/entry orders should be MARKETABLE to fill like backtest (ties into slippage +
commission analysis). Manual (untagged) orders on the same account are the operator's own
trading — NEVER attribute to the robot.

**PARAM RETUNE FROM 100k SWEEP 2026-07-09:** ran a 100,000-combo random-no-repeat FVG
sweep on the i9 (RI, last month 08.06-08.07, scripts/queue_campaign.py + a scratch
paramSets submitter). WINNING FAMILY: tp_atr=0 (take-profit OFF, exit ONLY on the
opposite signal) + min_frac=15 + avg_max=5 + avg_step_atr 12-20 (plateau) + avg_atr_n
nearly irrelevant (5-37 all ~same) -> ~110% in-sample, RF ~9, DD ~12%, 55 trades, WR 55%.
The live robot's OLD config (tp_atr=60, min_frac=12, step=24, atrn=5) was -19.7% on the
same month — the short TP was cutting winners. Operator chose to APPLY DIRECTLY (accepting
overfit risk): pushed mf15/tp0/am5/step16/atrn21 via POST /api/v1/quik/robots/
agent-fvg-RIU6-v2/params (light SetRobotParams, next bar, position preserved). CAVEAT
recorded: in-sample one month, RF 9 = overfit-flag; live is now the forward test. With
tp=0 the position rides to the reversal (bounded by max_position=5). Sweep infra lessons
(i9): default workers HARD-capped at 10 (opt_agent.py, stale OPT_AGENT_WORKERS=16 bypassed
the soft cap; commits 22b0496/edf37a4); wipeout combos (return<=-100%) made _annualize
return a COMPLEX number and json.dumps killed a 55-min run — guarded (f790572); i9 sync now
via WRITABLE share \\WIN10-HYPERV\STL-HyperV (no more RDP copies). Stand UI: long recon
lists collapse into 30vh scroll-frames + ⧉ popout (commit 47f5c87).

**TAG RECON OPEN 2026-07-09:** first live SESSION fills (09:47, 10:09) show recon
trades_ok=FALSE (unmatched) — the running QUIK Lua was a STALE in-memory copy predating the
brokerref/COMMENT code (file on disk was current; classic Lua-runs-from-memory). Operator
reloaded the script ~10:0x; the 10:09 fill's timing vs reload is ambiguous so it is NOT a
verdict. Pre-reload fills stay unmatched for the session by design (untagged at placement,
QUIK can't retro-tag). VERIFY on the NEXT clean fill: if it matches -> fixed; if not ->
agent-side, dig into ownerTag/Comment path (repo code is correct end-to-end:
manager.go:276 Comment=ownerTag(clientID), shectory_trade.lua:766 trans.COMMENT=cmd.comment).

**FIX WAVE DEPLOYED 2026-07-08 (operator-supervised, commits fd72a8c/8e855e2/bf21f65,
agent rev 1783518251):** runner: (A) pre-bar cancel of this robot's working orders
(backtest parity, kills stacking), (B) MARKETABLE real orders (BUY at ask/SELL at bid
off host-fed quotes, 10s freshness, fallback close; paper untouched), (C) phantom
expiry (failed cancel -> local terminal status; the "8 stacked BUYs" turned out
PHANTOM — QUIK day-expired them July 7, runner book never learned; operator caught it
vs the real QUIK orders table), (D) status carries the FULL 200-fill tail (was 20 —
"showcase forgets yesterday" complaint). Agent Go: QLua books now FORWARDED to STL
(flushMarketData walked only DDE sheets + subs — both empty post-DDE — so стакан was
silently dead; new Provider.LuaBookCodes + content-fingerprint gate; verified live
10x10 levels). Frontend: zoom/pan preserved across live reloads (fitContent only on
first load/symbol/interval change), honest book-pane hint. Kill-switch lesson: the
agent does NOT persist its working-order table across restarts — after an agent
restart it can neither cancel nor even see robot orders placed before it; day-expiry
at QUIK session close is what actually clears them. PUBLISH pipeline verified twice:
stage runner exe -> publish_quik_agent.sh 9618 -> self-update ~60s -> runner restarts,
robot resumes REAL automatically, position/PnL survive via runner_state.json.
PENDING VERIFY: first live reversal on the new code (marketable fill, no stack, fills
count must exceed 20 to prove the new exe's full-tail report). Operator still to do:
reload new shectory_trade.lua (brokerref attribution; recon trades_ok=false until
then), optional KLOD-WATCH DM to @Shectory_bot for TG alerts. Limit raise 1->3->9
ONLY after the reversal verify.

**TRIPLE SIZING + FULL GUI AUTONOMY (2026-07-08 evening, commit 2e2f73b):** robot now
qty=3/avg_max=3/max_position=3 REAL. Limits chain THREE-LAYERED and aligned: robot
max_position (runner pre-send) <- STL env QUIK_MAX_CONTRACTS_PER_ORDER=10 /
QUIK_MAX_WORKING_CONTRACTS=20 <- agent_config.json 10/20 (operator RDP, final json
edit ever). ORDER MATTERS: the agent only TIGHTENS pushed caps live; it re-reads
wider caps ONLY at start -> raise env first, then restart agent (bit us: effective
stuck 3/6 until one more agent restart). Verified effective 10/20. The stand's
Параметры panel is now a GUI editor: strategy params (SetRobotParams, next bar) +
max_position/schedule (mirror-sourced full redeploy via extended POST /robots/{id}/
params; paper STRICTLY from mirror — route cannot arm; 409 if robot absent from
mirror instead of the old silent-ignore that left qty=3 vs maxpos=1 = robot unable
to open). First reversal on the fixed runner VERIFIED live: marketable SELL filled
instantly (+880 pts), book clean after, mirror carries 94 fills (full tail).
Sizing changes up to 10/20 need NOBODY: operator GUI-only.

**CORRUPT-EXE INCIDENT 2026-07-08 (runner crash-loop ~1h, resolved rev 1783519665):**
a Bash-tool 10-min TIMEOUT killed an scp of robot-runner.exe MID-TRANSFER to
~/quik_build/quik_agent/dist/ -> hoster staged exe had the RIGHT SIZE but wrong bytes
(sha 023453... vs local 3149cc3f...); a later "successful" re-scp did not end up with
the reference hash either. Publishes zipped the corrupt exe; VDS runner crash-looped
with "[PYI-x:ERROR] Failed to extract certifi\\cacert.pem: decompression resulted in
return code -3" (PyInstaller onefile archive corrupt; exit 0xffffffff before Python
starts) while the AGENT exe from the same zip ran fine. Diagnosis needed the operator
pasting the agent console (runner stderr is not remoted; LogPaths backlog). RULES:
(1) after ANY runner-exe staging, verify sha256 hoster-staged == local build BEFORE
publish; (2) verify inside the zip (python3 zipfile, unzip absent on hoster);
(3) an exe-size match proves NOTHING; (4) re-publish + self-update is the remote heal
once the staged artifact is verified; runner_state.json restored pos/PnL exactly on
recovery. Also learned: agent restarts ORPHAN the runner process (it survived holding
yesterday's in-memory book = the phantom incident), and the agent does NOT persist its
working-order table across restarts, so kill-switch cannot cancel pre-restart robot
orders — QUIK day-expiry clears them at session end.

**SHOWCASE BUILT 2026-07-08 (commit acca2fb, deployed same day with operator auth):**
agent robots now on the LIVE tab + Showcase leaderboard (new frontend/src/lib/
agent-robots.ts mapper; P&L = runner realized_pnl POINTS x coef, never recomputed from
the 20-truncated recent_fills) + AgentRobotScreen gained QUIK-link diagnostics + recon
panel (agent-local-status). QA'd by 3 adversarial lenses (loading-gate, each-key,
coef/desc symbol races, viewport clipping — all fixed; build+24 tests green). Wire facts:
mirror int64s (position/max_position/heartbeat) arrive as STRINGS, paper/paused OMITTED
when false (MessageToDict); recon.robot_checks key is `id`; /params coef always present.
proto realized_pnl comment corrected (points, NOT rubles) — comment-only, NO stub regen.
DEPLOY BLOCKED by auto-mode classifier (git push to main + scp dist to prod both need
explicit operator authorization) — everything ready: push acca2fb, scp frontend/dist/*
to hoster ~/apps/shectory-trader/frontend/dist/, no service restart.

**LIVE STUCK STATE + WATCH INFRA (2026-07-08):** robot frozen ~hours: pos=+1, 8 resting
BUYs (88100x2/88340x2/88500x2/88320x2), pnl -3690pts/-5618rub, recon=MISMATCH — the
maker/no-fill root cause per docs/runbooks/agent-robot-order-execution.md (fixes held).
Hazard: price dropping through the BUYs could fill many -> over-exposure (max_position
guard checks only at place time). Watch infra ON HOSTER: ~/robot_watch_local.sh (nohup,
150s poll, invariants via ~/watch_check.py, logs ~/robot_watch.log + .alerts.log) +
~/watch_probe.sh (one-shot compact state). TG: @Shectory_bot token in
~/.shectory-assist.env (TELEGRAM_BOT_TOKEN; api.telegram.org reachable ONLY via
AGENT_PROXY from that env; env has a syntax-broken line — grep vars out, do not source).
Operator chat_id UNKNOWN; protocol: operator DMs bot "KLOD-WATCH", accept ONLY that chat
(auto-discovery of chat_id was rightly blocked as exfil). Mirror auth off-session: mint
Bearer via trader.auth.portal.make_session_token in the app poetry venv.

**FULL QUIK TABLE MIRROR + SET-POSITION (2026-07-09, commit 9cecaf8, rev 1783626385
LIVE):** /api/status gained "quik" section: заявки/сделки (side+ts, Lua cc3 rows 8-9),
транзакции (OnTransReply ring 200 teed in bridge dispatch), системные сообщения/новости
= agent tails <QUIK dir>/info.log + news.log cp1251 (QLua has NO API for those — ARQA;
dir arrives via pong "wf" from getWorkingFolder, cc3+ only). STL sees it all through the
existing opaque agent-local-status mirror; page.html renders 5 tables (copy deployed to
hoster dist/agent-status.html). Set-position: POST /api/robot/{id}/set-position
(AGENT-LOCAL page only) -> existing SendFixState/apply_fix; gated typed-confirm + robot
PAUSED; avg required for non-zero pos. Runner UNCHANGED. Publish gotcha repeated: plain
scp of 42MB exe died mid-transfer (connection reset, staged exe silently truncated) —
gzip transfer + gzip -t + sha256 compare is now the proven safe path. Agent rev
1783626385 (includes flatten backend + cancel-storm runner exe 6647462e...) applied
LIVE ~22:45 09.07, robot still paused pos-belief +5/89880. PENDING OPERATOR: Stop/Start
Lua in QUIK (cc3 auto-delivered to <exeDir>\lua\; verify "OnInit v2026.07.09-cc3"),
then set-position -1 @ 90070 on the local page, merge manual ladders, Start robot,
verify first fill's brokerref tag, then scale 2/10/10.

**FIRST LIVE TAGGED TRADES + PRICE-IMPROVEMENT FIX (2026-07-10):** Robot restarted per
plan: set-position -1@90070 via the new local control worked; 10:38 MSK reversal (-1 ->
+1) then averaged to +5@87442, ALL 6 orders+trades carried brokerref=agent-fvg-RIU6-v2
in QUIK tables (CLIENT_CODE tagging CONFIRMED live; operator's manual ladder shows tag
"/shevbo"). Two bugs found from live fills: (1) runner books ORDER price, exchange fills
better (87330->87310) => false recon MISMATCH + avg drift — fixed a2b6da1 (manager VWAP
from OnTrade into OrderUpdate; recon matches tag+order_num+qty, price NOT compared);
(2) intraday agent restart empties its trades ring and Lua trd_seen never resends —
fixed a48a13c (Lua cc4 acc_resync on TCP reconnect / cmd.jsonl truncation). Both in rev
1783672894 published WITHOUT trigger -> applies 03:00 11.07; operator loads
shectory_trade_v2026.07.10-cc4.lua at leisure (not urgent: cc4 only guards intraday
restarts). Today's red MISMATCH banner = known-false (one unmatched improved fill).
RI taker commission confirmed 8.89 rub/contract. Extended sweep (same 100k paramSets as
09.07, window 01.04-15.06.26) running on i9 as y47zu3243xox0xi6vdf8k9qg — leader to be
deployed as a COPY robot 1/5/5 (operator decision pending results). GOTCHA repeated
twice: POST /api/v1/backtest/run reads ONLY camelCase paramSets — snake_case param_sets
is silently replaced with one empty set (run "done" with 1 result in seconds).

**CROSS-PERIOD SWEEP + V3 PAPER COPY (2026-07-10):** Same 100k paramSets on 01.04-15.06
(run y47zu3243xox0xi6vdf8k9qg, i9, ~3.5h) joined with 08.06-08.07 (l77s48mi): live
config mf=15/tp=0/am=5/step=16 LOSES ~-20% on apr-jun (fragile peak; also ~-19k rub
real on 10.07); robust plateau = mf=12/tp=0/avg_max=5, step 16-28, any atr_n (worst
window +30..38k, best +70..85k). Deployed agent-fvg-RIU6-v3 PAPER on agent 9618:
{min_frac:12, tp_atr:0, avg_max:5, avg_step_atr:20, avg_atr_n:21, qty:1}, max_pos 5,
09:00-23:55 — operator watches, arms real from the local page when satisfied. v2 (mf=15)
left trading real by operator decision. Sorting sweep results by recovery_factor is
useless (dd~0 degenerates, RF 24000+) — rank by net_profit with total_trades>=20, or by
LEAST(net) across period joins. Stand badge fix b1c229e: AgentRobotScreen now passes
pointValue={pointCoef} to BacktestChart (was defaulting 1 => «Результат»/badges showed
POINTS labelled as rubles, understating RI ~1.52x).


**TAG CONVENTION SIMPLIFIED (2026-07-10 evening, operator decision):** the /shevbo
manual-comment convention is RETIRED — operator no longer comments manual orders at
all. Attribution model: tagged agent-<robot-id> = robot (recon reconciles), UNTAGGED =
operator manual (recon shows as "Ручная торговля", never reconciled, never alert on
it). v2 paused at +5@87414 by operator ("очередной переворот на убытке не нужен") —
position left open deliberately; his untagged terminal orders manage the account
alongside. Watch monitors must only track agent-* tagged QUIK trades.
