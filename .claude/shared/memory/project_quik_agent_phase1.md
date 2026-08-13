---
name: project-quik-agent-phase1
description: "sprint02 Phase 1 QUIK agent — live link STL<->Windows agent, build/deploy/run facts"
metadata: 
  node_type: memory
  type: project
  originSessionId: a9dc550d-ba89-4feb-9d61-475846178d17
---

sprint02 Phase 1 (read-only QUIK link) is LIVE as of 2026-06-29. Local Go agent reads QUIK via DDE,
dials out to STL over one bidi gRPC stream. NO order transactions in Phase 1 (orders = Phase 2, needs
trans2quik.dll/Lua; DDE can't send transactions). Spec: docs/sprint02.md; acceptance: docs/sprint02-acceptance.md;
wire contract: proto/shectory/quik/v1/quik_agent.proto + quik_agent/CONTRACT.md. Reference impl ported from
PiranhaAI (smain ~/workspaces/projects/PiranhaAI/local_agent_go/internal/quikdde).

**Components:** `quik_agent/` (Go, Windows agent) + `trader/quik/` (STL gRPC server, store, alerts) +
`trader/api/quik_routes.py` (/api/v1/quik/*) + frontend ExchangeInterface.svelte ("Интерфейс биржи" selector).
Committed to main 2026-06-29 (202e97d, fix 5e1aba4).

**STL side (hoster 83.69.248.175):** gRPC server binds QUIK_AGENT_GRPC_LISTEN=0.0.0.0:50061, gated by
QUIK_AGENT_ENABLED=true. Bearer token in env QUIK_AGENT_TOKEN (both in ~/.shectory_trade.env). Server only
starts in lifespan when enabled; logs quik.server.started / quik.session.connected / auth_rejected.

**Agent side (QUIK VDS 83.69.248.180, reaches hoster via TAP tunnel, RTT ~0ms):** single exe in
C:\distr\dist\ (quik-agent_amd64.exe). Reads agent_config.json NEXT TO the exe (stl_grpc_url=83.69.248.175:50061,
stl_insecure=true, token_env=STL_QUIK_AGENT_TOKEN, quik_data_root). Token via env STL_QUIK_AGENT_TOKEN (must
EQUAL hoster QUIK_AGENT_TOKEN). DDE server name the agent registers = **SHECTORY_QUIK**, topic **data** — QUIK
must be set to export tables there. Run interactively or `--service install/start` (survives reboot).

**Build (no Go/protoc locally):** hoster has userland toolchain at ~/go-sdk/go/bin, ~/go/bin (protoc-gen-go,
-go-grpc), ~/protoc/bin. Cross-compile Windows exe with CGO_ENABLED=0 (quikdde is pure x/sys/windows syscalls,
no cgo). Sources synced to hoster ~/quik_build via tar-over-ssh (local has no rsync). Single dev runner:
dev.ps1 / Makefile (gen/build/test/lint). Python gRPC stubs MUST target protobuf 5.29 (prod runtime 5.29.6);
grpcio-tools 1.81 emits gencode 6.x which fails — regen with grpcio-tools<1.71.

**OPEN / caveats:** link is plaintext + Bearer, 50061 on 0.0.0.0 (hoster ufw inactive); harden before real
data — restrict 50061 to VDS IP at cloud firewall, or WireGuard/TLS. Token generated on-host (NOT keymaster:
the inbox auto-responder ask-claude.sh errored on the mint task). server.py has dead `_drain_commands` (harmless).
See [[reference-commission-model]], [[reference-forts-contract-roll]], [[reference-federation-access]].

**PROTOBUF GOTCHA (bit prod TWICE):** prod protobuf runtime is 5.29.6. Any sub-agent that regenerates
`trader/quik/pb/*_pb2.py` with grpcio-tools 1.81 emits gencode 6.x -> STL CRASH-LOOPS on import on prod
(local runtime is 6.x so it passes locally and pytest is green). ALWAYS regen with `grpcio-tools<1.71`
(emits "Protobuf Python Version: 5.29.0") and verify that header before committing/deploying. Hotfix:
on hoster `python -m venv /tmp/g; /tmp/g/bin/pip install "grpcio-tools<1.71" "protobuf==5.29.6"; ... protoc
... --python_out=trader/quik/pb ...; restart`.

**BROKER SDK (commit d0a1faf, deployed 2026-06-30):** `trader/broker/` — robots trade through ONE strict
contract `BrokerInterface` (base.py); concrete adapter chosen from `settings.exchange_interface` (no hardcode).
CORE caps (9): instruments, order_book, place_order, cancel_order, replace_order (NATIVE atomic move),
orders, positions, account, connection. Second-wave (6): order_book_stream, quote, maker_execution, order_stream,
news, messages. `registry.get_broker(settings, require_trade_ready=True)` HARD-GATES live trading on
`is_trade_ready()` (all CORE). Adapters: FinamBroker (not trade-ready: lacks cancel/orders/replace), QuikBroker
(not trade-ready: lacks positions/account; agent does not report them yet). Wired on app.state.broker
(require_trade_ready=False, best-effort). **Native MOVE_ORDERS** added: proto ReplaceOrder, Go agent Move + Lua
ACTION=MOVE_ORDERS, STL /api/v1/quik/orders/replace; the 1b maker loop now re-quotes via ONE atomic MOVE
(no cancel+place window) — structurally immune to the runaway class. Agent rev with MOVE: 1782768975.
Next: QUIK positions/account reporting (to make QuikBroker trade-ready); migrate real robots onto BrokerInterface.

**Self-update (works):** STL serves releases at `https://stl.shectory.ru/api/v1/quik/agent_release[?arch=]`
(agent Bearer auth) + trigger `POST /api/v1/quik/agent/{id}/self-update` (operator). Publish with
`bash ~/publish_quik_agent.sh [agent_id]` on the hoster (builds epoch build_rev, zips, publishes; agent_id arg
also fires the trigger). Agent self-updates on start + daily 03:00 + on command — NO manual exe copy. VDS env
`SHECTORY_AGENT_RELEASE_URL`. NOTE: build_rev must be a NUMERIC epoch (ldflags agentBuildRevStr); a git-sha
parses to 0. Command-trigger restart needed the os.Exit-on-staged fix (commit e820ac1) — agents built before it
stage but don't restart on the COMMAND path (on-start/03:00 still restart fine).

**Phase 2 (orders) — DEPLOYED DORMANT 2026-06-29 (commit 233f3ab):** human-initiated QUIK orders via a QLua
`sendTransaction` bridge (chose QLua+socket over trans2quik to keep pure-Go cross-compile + self-update). Master
flag `quik_trading_enabled` OFF by default (STL + agent both reject orders when off — verified live). Hard limits
both sides: max_contracts_per_order=2, max_working_contracts=2, price_collar_frac=0.002, whitelist=[RIU6],
daily_order_cap=50, kill-switch. Slice 1: 1a manual limit place/cancel/status, 1b maker loop (join touch, never
cross, re-quote >=1 step & <=200ms, collar stop). Components: `quik_agent/internal/trade/` (Go: bridge/limits/
manager/execution), `quik_agent/lua/shectory_trade.lua` (Lua relay), `trader/quik/{limits,orders}.py` +
`trader/api/quik_orders.py` + `frontend/.../Orders.svelte` (UI «Заявки» + confirm + kill-switch). Contract:
proto PlaceOrder/CancelOrder/KillSwitch/StartExecution/StopExecution + OrderUpdate/TransReply/ExecutionUpdate;
Lua<->agent TCP JSON on 127.0.0.1:50063 (see quik_agent/PHASE2.md). Acceptance: docs/sprint02-phase2-acceptance.md.
TO GO LIVE: agent on the Phase 2 build (restart/03:00), install+start the Lua script in QUIK (set ACCOUNT/
CLIENT_CODE), set quik_trading_enabled=true on BOTH sides, then walk 1a on 1 contract. Real money = operator only.

**1a VERIFIED LIVE 2026-06-29:** place far limit -> active, cancel -> cancelled (full round-trip STL<->file-queue
<->Lua<->QUIK<->exchange). QUIK had NO LuaSocket -> added a file-queue transport (cmd.jsonl/evt.jsonl, config
trade_queue_dir == Lua CONFIG.QUEUE_DIR); setup batch quik_agent/lua/setup_filequeue.bat + enable_trading.bat.
Account 763J576. A live manual BUY above the ask = TAKER fill (1a lets you cross; maker discipline is 1b only).

**PHANTOM-PENDING BLOCKS PLACEMENTS (fixed 2026-07-01, agent rev 1782933832, commit dd2730a):** an order QUIK
never acknowledged (no order_num, left by a gRPC link drop) stayed PENDING in the agent forever (its deferred
cancel waits on an order_num that never arrives) and permanently occupied the max_working_contracts budget, so new
orders were falsely rejected "would exceed max_working_contracts" while STL had already reconciled its own side to
expired. TWO reconcilers now: STL-side OrderStore.reconcile_pending (20s, commit 7657752) + agent-side
Manager.reconcileStalePending (20s): a PENDING order with no order_num older than staleAckTimeoutMs is marked
terminal (REJECTED, ReasonStalePending) and freed from the working count; PlaceOrder runs it FIRST so the blocked
placement proceeds. workingOrder gained sentMs. Test TestReconcileStalePending. NOTE the dual-flag: STL
trading_enabled AND the agent's own agent_config.json quik_trading_enabled must BOTH be true (I do not push the
master flag); "Торговля QUIK отключена" from a rejected order = the AGENT's local flag is off. A backend STL
restart drops the gRPC link (agent redials 50061) — do NOT restart STL while the operator is live-QA-testing
trading; each drop leaves untracked-order noise + can phantom an in-flight placement.

**QUIK OnTransReply STATUS + CP1251 GOTCHAS (fixed 2026-06-30, agent rev 1782831376, commit 9d3a86f):**
(a) QUIK OnTransReply `status` is NOT success=0 — it is a PROGRESS code. Non-rejection statuses are 0 (sent to
server), 1 (received by QUIK server) and **3 (EXECUTED / order registered — "успешно зарегистрирована")**;
everything else (2 transmit error, 4 not executed, 5/6 failed checks…) plus the Lua relay's own negative codes are
real rejections. The agent had `rejected := ResultCode != 0`, so a successfully-registered order (status 3) showed
"Заявка ОТКЛОНЕНА" in the UI while QUIK actually placed it. Fixed: trade.isTransReject() treats only {0,1,3} as
non-reject. (b) QUIK hands QLua its result_msg / reject text in **Windows-1251**; the Lua relays raw bytes, and Go
`json.Unmarshal` then replaces each non-UTF-8 byte with U+FFFD (the ◇◇◇ mojibake). Fixed: trade.toUTF8() converts
a non-UTF-8 line from Windows-1251 BEFORE json decode, in BOTH the TCP reader (bridge.go) and the file-queue reader
(bridge_filequeue.go); ASCII/valid-UTF-8 lines pass through. Uses golang.org/x/text/encoding/charmap (already in
go.mod). (c) UI charts now render the time axis + crosshair in MSK (UTC+3) via lightweight-charts
tickMarkFormatter/localization.timeFormatter (frontend/src/lib/chart-time.ts; data stays UTC, no shift) —
ChartFrame/MiniChart/OrderViz. Also: chart/bars REST returns EMPTY for too-wide windows (M15>~30d, D>~365d);
the route now shrinks the window and retries (base, /2, /4, /8). New "Графики поз./заявок" frame = a MiniChart per
instrument in a position or with working orders. Main chart blank-fix: ChartFrame draws REST bars DIRECTLY in
loadRestHistory (the reactive ohlc->effect chain did not repaint in prod); change*/effect race removed (one loader).

**1b RUNAWAY + FIX (commit d41f25d):** first live 1b SELL spun out placing ~14 orders (broker [GW][332] margin
limit + kill-switch stopped it; none filled, no short). Root cause: maker loop placed each tick without awaiting
the prior cancel, and onOrderEvent reset the quote on ANY terminal order (incl. lagging cancels of OLD children).
Fixed: onOrderEvent matches the CURRENT child only; cancel-before-replace barrier (pendingCancel); placeNew
refuses while a child is live/pending + minRequote rate-limit; hard placement backstop (maxChildPlacements=50);
deferred-cancel fires once order_num arrives. Tests added. DO NOT re-test 1b on live without: target=1, tight
collar, watching, kill-switch ready. The STL kill-switch endpoint works and is the emergency stop; also Ctrl+C
the agent. Resolve-agent prefers the single GREEN agent (stale store entries otherwise broke order routing).

**WHITELIST DIVERGENCE GOTCHA (bit 2026-06-30):** STL whitelist (env quik_instrument_whitelist) and the AGENT
whitelist (agent_config.json instrument_whitelist on the VDS) are SEPARATE and can silently diverge. If STL has a
code but the agent does not, STL forwards the order and the AGENT rejects it `instrument not whitelisted` BEFORE
QUIK -> "no trace in QUIK", looked like "nothing happened". Agent whitelist is config-only + needs an agent
RESTART (no gRPC push command yet; VDS has no SSH, only RDP — run quik_agent/lua/set_whitelist.bat there, it sets
RIU6,GZU6,SiU6,SRU6, then restart the exe). Durable follow-up: push limits/whitelist STL->agent over the link on
connect. FIX shipped (commit 4da6cd8): Orders.svelte now shows the reject `text` column + a "Заявка ОТКЛОНЕНА:
<reason>" message (reject reason was stored in rec.text but never rendered); App.svelte adds a "Проверка сессии…"
splash + auth/me retries so a normal F5 with a valid 30-day cookie no longer flashes the login screen; store.py
_pick resolves the single link-GREEN agent when agent_id omitted (fixes OrderViz стакан 404 + doubled requests).

**LIMITS/WHITELIST PUSH — LIVE 2026-06-30 (commit 66e3f7d, agent rev 1782823561):** STL is now the SOURCE OF
TRUTH for the hard limits/whitelist; the manual VDS set_whitelist.bat step is OBSOLETE. proto adds SetLimits
(STL->agent, OrchestratorMessage.set_limits=9) + LimitsState (agent->STL, AgentMessage.limits_state=14). On
Register STL pushes its limits (server.py set_limits_provider built from OrderLimits.from_settings via
orders.build_set_limits); the agent's Guard.ApplyPushed ADOPTS the whitelist (replace; empty ignored = fail-safe)
and treats numeric caps as a CEILING it may only tighten (agent_config stays a hard backstop); the master flag is
NOT pushed (trading stays DUAL: STL flag AND agent local flag). Agent echoes LimitsState (startup + after each
push); STL store keeps it (status + /orders/config agent_limits); Orders.svelte shows a green/red whitelist-sync
line. To change whitelist now: edit STL env quik_instrument_whitelist + restart STL (agent re-adopts on reconnect)
-- NO RDP/exe edit. Activation was fully remote: publish_quik_agent.sh (no agent_id) then POST
/api/v1/quik/agent/9618/self-update -> agent restarted into the new build, STL pushed, whitelist converged to
[RIU6,GZU6,SiU6,SRU6] incl GZU6. Doc: docs/quik-trading-startup.md (full startup sequence + automation table +
preflight). Caveat: agent restart on self-update worked (rev 1782823561 has the e820ac1 os.Exit fix); old builds
only stage. Note Python pb2 regen MUST use grpcio-tools<1.71 (5.29) -- did via /tmp/pbgen venv on hoster.

**QLUA MD FEED — DDE DEPRECATED (2026-07-05, commit 9f59124, agent rev 1783246093):** the DDE
export died 3x in one day (needs manual "Начать вывод", dies on agent restarts and on the big
params table; the watchdog can't make QUIK resume). Market data now flows from
shectory_trade.lua itself: getParamEx ticks (500ms) + getQuoteLevel2 books (1s) for
CONFIG.MD_CODES over the SAME file-queue transport as orders (auto-reconnects, zero manual
steps). Go: bridge md/book events -> Provider lua overlay (SetLuaTick/SetLuaBook; readers
merge freshest-wins; LastMutationMs includes lua so the watchdog stops DDE-restart loops).
VERIFIED LIVE: tick ts advances exactly +1000ms/s, freshness <1s end-to-end. DDE is now an
optional fallback only. Agent config poll_interval_sec=1 (was 5) — full chain QUIK->showcase
~1-2s. VDS clock was ~2min behind (skewed all freshness badges + bar buckets) — fixed with
w32tm /resync; if freshness looks wrong again, CHECK VDS CLOCK FIRST. When updating
shectory_trade.lua on the VDS (C:\distr\dist\lua\), the operator MUST re-fill CONFIG:
USE_FILE_QUEUE=true, QUEUE_DIR="C:\quik-bridge", ACCOUNT — repo defaults have file-queue
OFF, which silently kills BOTH md and the real-order path (script logs "Idling" to the QUIK
message window).

**ALL-TRADES TAPE + FULL DDE RETIREMENT (2026-07-05, commits 0db5cb3/faaf0f8, agent rev
1783250653):** shectory_trade.lua now also publishes the anonymized tape (OnAllTrade
buffered, 300ms batches) and instrument params (SEC_PRICE_STEP/STEPPRICE/BUYDEPO, 60s).
Runner bars build from REAL trades with TRUE volume (BarBuilder.on_trade; snapshot ticks
muted for 30s after tape activity, auto-fallback). RunnerBridge gained StreamTape.
VERIFIED LIVE with DDE fully OFF: /params returns RIU6 step=10/stepcost=15.445 (lua-only
source), tick stamps advance +1s. DDE is retired; the QUIK "Таблица всех сделок" window
must stay OPEN (orders the tape stream for OnAllTrade). Operator config now lives in a
SIDECAR next to the script: shectory_trade_config.lua (return {ACCOUNT=..., USE_FILE_QUEUE
=true, QUEUE_DIR="C:\quik-bridge"}) — script updates never require re-editing CONFIG
again (example: quik_agent/lua/shectory_trade_config.example.lua).
