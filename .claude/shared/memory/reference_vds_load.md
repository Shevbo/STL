---
name: reference-vds-load
description: Hoster VDS is small/shared and SSH dies under load; backtest sweeps must run as background load
metadata: 
  node_type: memory
  type: reference
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
  modified: 2026-07-23T18:16:40.374Z
---

## Оптимизация бэктест-движка i9 (2026-07-23, проверено в бою)
- **pivot_reversal был ~30x медленнее остальных** (300 комбо: 1300-1700с против 30-60с). ДВА узких места в `trader/lab/strategies/library.py`: (1) `_prev_day_hlc` пересканировал 2200-баровое окно на КАЖДОМ баре — кэш по (symbol, day) `_PIVOT_HLC`; (2) `atr()` гонял весь массив (pivot тянет 2200 баров ради прошлого дня) — теперь по хвосту `bars[-(atr_n*40+1):]`. ATR по Уайлдеру забывает старое экспоненциально, хвост atr_n*40 бит-в-бит == полному окну (тест `test_atr_tail_equals_full_window`). Проверено на РЕАЛЬНЫХ SVU6: комбо 52→11с; тот же 300-комбо прогон на i9: **1116с→494с**. ATR-фикс касается ВСЕХ стратегий с усреднением/TP (через AVG_PARAMS).
- **УРОК: мерить на РЕАЛЬНЫХ данных.** Синтетика с дефолтными параметрами (усреднение выкл) дала фантомный «19x» — задела только ветку _prev_day_hlc. Настоящий хотспот (ATR) виден только с усреднением на боевом объёме. `scratchpad/bench_real.py` грузит бары через iss_loader.
- **Репер выбивал длинные opt-джобы через 8 мин** (порог под <1-мин раунды) → перезапуск с нуля вечно, pivot часами жевал одну работу. Фикс: не трогать джобу, чей run_id называет свежий (<120с) i9-heartbeat (app.py `_orphan_reaper`).
- **ЛОВУШКА пересборки пула воркеров i9:** пул пересобирается (свежие процессы = новый код с диска) ТОЛЬКО когда агент ЗАМЕТИТ смену числа воркеров при опросе. 14→13→14 с короткой паузой = агент опросил после возврата к 14, увидел 14==14, НЕ пересобрал → воркеры на старом коде. Надо ПОСТАВИТЬ ОТЛИЧНОЕ значение и ДЕРЖАТЬ, пока heartbeat не подтвердит новое число. Доставка модуля стратегии на i9 = curl с jsdelivr (raw.githubusercontent зарезан) в `C:\Users\admin\Documents\@FIN\...\trader\lab\strategies\`, сверять hash. ХРУПКО (споткнулся дважды) — надо научить агента забирать модули стратегий с jsdelivr автоматически. [[honest-campaign]]

Hoster VDS (83.69.248.175) is SMALL + SHARED. Specs: ~4 cores, 5.8Gi RAM, only 2Gi swap.
Co-tenants: eschool bot, komissionka (x2), openclaw, next-server, PM2 — plus shectory-trader.

**Failure mode seen 2026-06-03:** a param sweep drove load average to ~140, swap hit 95%,
kswapd thrashed → sshd and nginx stopped answering (TCP connects, but banner-exchange/TLS
timeout; host still pings). Looks like an outage but it's CPU/mem starvation. Recovers on its
own as load drains (140→7 over ~10-15 min). To deploy during it: poll `cut -d' ' -f1 /proc/loadavg`
until <8, then pull+build.

**Mitigations shipped (commit e65dcd2 + 9bc6429):**
- `trader/lab/backtest.py::_demote_to_background()` — every backtest subprocess calls os.nice(19) +
  Linux SCHED_IDLE + psutil ionice idle, and the grid `await asyncio.sleep(0)` every 8 combos.
- `trader/api/app.py` — module-level `_BACKTEST_LOCK = asyncio.Lock()` serializes runs (one sweep at
  a time); `_MAX_COMBOS = 2000` cap (backend rejects, Optimizer.svelte warns at same limit).
- GOTCHA that crashed startup: app.py imported asyncio only inside functions; the module-level Lock
  needed `import asyncio` at top (fixed 9bc6429). Always restart service after app.py changes.

**BUILT + VERIFIED end-to-end 2026-06-03 (commits dafd065..6db23db):** param-sweep offload to the
Windows i9 box (i9-9900X, 128GB). NOT a new table — reuses backtest_runs with new columns
engine/symbol/job_body/claimed_at/agent_id. Flow: run body has engine 'local'(VDS) | 'remote'(queued).
Remote insert sets status='queued'+job_body, no VDS task spawned. Endpoints (auth via header
X-Agent-Token == env OPT_AGENT_TOKEN): POST /api/v1/agent/claim (atomic UPDATE..FOR UPDATE SKIP LOCKED,
returns script+base_params+grid+point_value; 204 if idle), POST /api/v1/agent/result (bulk insert into
backtest_results, status=done). scripts/opt_agent.py = pull agent on Windows (runs with SYSTEM python
C:\Users\Boris\AppData\Local\Programs\Python\Python312, NOT poetry — no .venv here; deps installed:
httpx, pydantic), ProcessPoolExecutor (default cores-2; tested --workers 16), bars from ISS, downsamples
equity_curve to ~1500 pts before POST. Optimizer/BacktestLab have VDS/«Мощный хост» toggle; remote cap
200000 combos, local 2000. Existing status/results polling unchanged → UI just works.
GOTCHAS fixed: (a) backtest_runs owned by postgres not project_stl_app → app's ALTER failed "must be
owner"; ran ALTERs manually as postgres. (b) nginx default client_max_body_size 1m → 413 on result POST;
raised to 128m in deploy/nginx.conf (+ downsample). (c) agent stdout cp1251 → UnicodeEncodeError on →/×/…;
force sys.stdout.reconfigure utf-8. Run agent: set OPT_AGENT_TOKEN+STL_API user-env (already done via
keymaster), then `python scripts/opt_agent.py`. Verified: 4-combo RIM6 sweep ran on host, results in DB,
run=done. agent_id format host:pid (e.g. vs-code-local:NNNN).
AGENT INSTALL + AUTOSTART DONE (commit e2eec6d). agent/ bundle: install.ps1 (creates agent/.venv,
pip httpx+pydantic, saves OPT_AGENT_TOKEN+STL_API to USER env, registers Scheduled Task
"ShectoryOptAgent" = AtLogOn + RestartCount 999/1min, runs venv python DIRECTLY), start.cmd (manual),
README, requirements.txt. Install: `powershell -ExecutionPolicy Bypass -File agent\install.ps1 [-Token <v>] [-Workers 16]`.
Agent hardened: supervisor run() never exits (claim/process errors keep polling; fatal → backoff restart);
self-tees stdout+stderr to log (--log, default %TEMP%\shectory_opt_agent.log).
GOTCHA: Task action must run python.exe DIRECTLY, NOT `cmd /c start.cmd` — cmd strips quotes and chokes on
the space in "Shectory Trade & Lab" (LastTaskResult=1, no proc). Direct exe + -WorkingDirectory repo works
(state=Running, result 267009=running). Manage: Get/Start/Stop-ScheduledTask -TaskName ShectoryOptAgent;
log Get-Content %TEMP%\shectory_opt_agent.log. Verified task-launched agent claims+computes+posts (run=done).
Workers auto = cores-2; pin with install -Workers 16 (sets OPT_AGENT_WORKERS user env).
This PC has NO poetry/.venv at repo root — only system python C:\Users\Boris\AppData\Local\Programs\Python\Python312;
the agent uses its OWN agent/.venv.
CAMPAIGN QUEUER DONE (commits 8dee785..2041950). scripts/enqueue_campaign.py (run ON VDS): enqueues
REMOTE jobs = each library strategy × top-N FORTS instruments; job_body carries script_code+base_params
+grid so no robots row per strategy needed. agent/claim prefers job_body.script_code/base_params (campaign)
else robots table (UI). agent/result mirrors campaign results into optimization_leaderboard (Botstore
hit-parade): strategy parsed via regex make_on_bar\('(\w+)'\) from script_code, campaign = first 3 dash
parts of run_id "camp-YYYYMMDD-HHMM". FK: backtest_runs.robot_id→robots, campaign rows reuse
FK_ROBOT_ID='robot-supertrend-rts-01' just to satisfy FK.
GOTCHAS fixed: optimization_leaderboard.id is bigint sequence (NOT cuid) → omit id in INSERT;
campaign-id parse was rsplit wrong → use first 3 dash parts.
i9 HOST REALITY: the powerful host is "Win10-HyperV" user admin, repo at
C:\Users\admin\Documents\@FIN\Shectory Trade & Lab, Python 3.10 (not 3.12), NO git, corporate proxy with
TLS interception. install.ps1 handles: recreate foreign/broken copied .venv; Startup-folder VBS autostart
when Scheduled Task denied (no admin); pip --trusted-host pypi.org/files.pythonhosted.org/pypi.python.org
(+ optional -Proxy) for the self-signed-cert proxy. Agent verified running on i9: "Win10-HyperV:2460
workers=16 idle". This dev box is only 8 threads (NOT the i9).
RUN A CAMPAIGN: ssh hoster, `cd ~/apps/shectory-trader && set -a && . ~/.shectory_trade.env && set +a &&
<venv>/bin/python scripts/enqueue_campaign.py --instruments 8 --per-strategy 400`. Monitor:
`sudo -u postgres psql project_stl -t -c "SELECT status,count(*) FROM backtest_runs WHERE id LIKE 'camp-%' GROUP BY status;"`
and optimization_leaderboard WHERE campaign_run='camp-...'. Note CNYRUBF/USDRUBF perpetuals → "no bars"
from ISS (those jobs fail cleanly, others fine). 12 library strategies (not 16; 4 core ones separate).
TODO next: (1) verify UI toggle path in browser (Optimizer→Мощный хост); (2) optional Numba @njit (+10-50x).
Secret name OPT_AGENT_TOKEN — see [[feedback-secrets-protocol]], [[reference-federation-access]].

**i9 SYNC SOLVED (2026-07-09): network share `\\WIN10-HYPERV\STL-HyperV` = the repo root
on the i9** (C:\Users\admin\Documents\@FIN\Shectory Trade & Lab). Copy files directly
(robocopy /E from the dev box); no more RDP hand-copies, and the flaky raw.githubusercontent
self-update is obsolete for code sync. Running sweep workers hold imported code in memory —
an overwrite applies from the NEXT claimed job. opt_agent.py default workers CAPPED at 10
(commit 22b0496; cores-2 pegged the 20-core box, operator alarmed twice) — override via
--workers/OPT_AGENT_WORKERS. Mid-run relief without killing the job: PowerShell on the i9
`Get-Process python* | % { $_.ProcessorAffinity = 0x3FF; $_.PriorityClass = 'BelowNormal' }`.
SWEEP GOTCHA (killed a 55-min 100k run at the final POST): a wipeout combo (return <= -100%)
made _annualize return a COMPLEX number -> json.dumps of the whole result batch died
("Object of type complex is not JSON serializable"); guarded in trader/lab/backtest.py
(f790572) — wipeouts annualize to None. Results of bare-cuid runs live in backtest_results
(NOT optimization_leaderboard — that mirror needs camp-/opt- run_id prefixes).

**i9 AGENT CONNECTIVITY GOTCHA (2026-06-04):** agent reached `idle` before (claim worked), then
started failing every poll with `claim error: All connection attempts failed`. Server was UP
(stl.shectory.ru → 200, /api/v1/agent/claim → 401 as expected) — so it was the i9's outbound path,
the corporate proxy now blocking direct :443. Fix: route httpx via proxy. opt_agent.py now reads
`--proxy` / `OPT_AGENT_PROXY`, and falls back to `HTTPS_PROXY`/`HTTP_PROXY` env. PREFER the env var:
httpx (trust_env default) applies it to BOTH agent claim/result AND load_bars_iss in workers. Set
`$env:HTTPS_PROXY` + `$env:HTTP_PROXY` (same proxy as pip -Proxy) before launching. The updated
opt_agent.py + trader/lab/commission.py + runtime.py + backtest.py must be COPIED to the i9 (no git
there) for the new commission model to apply in campaign computation.

**BLACKOUT 2026-06-04 (Botstore on-demand chart):** clicking a leaderboard row for an UNCACHED
symbol (GDM6 — campaign agent caches bars on the i9, NOT on the VDS, so VDS ohlcv_bars had only the
~5 symbols opened via UI) made _fetch_bars_for_backtest pull 3 months of 1-min bars from ISS with NO
timeout. Slow ISS → task hung → it holds the single in-process _BACKTEST_LOCK → every later local run
blocked, chart spun forever ("чёрный экран 10 мин"). NOT memory (swap=0, no OOM); CPU/lock stall, nginx
starved (sshd+API timed out, recovered ~6 min). Diagnosis: `cat /proc/loadavg`, `free -m`, `dmesg|grep -i oom`,
and `SELECT status,count(*) FROM backtest_runs WHERE engine='local' GROUP BY status` (saw many stale
'running'). uvicorn runs `--workers 1`, so the asyncio.Lock DOES serialize — the extra 'running' rows were
ORPHANS from the 3 deploys/restarts that day (a restart kills the task but leaves status='running').
FIX (committed, deployed): (a) `asyncio.wait_for(_fetch_bars_for_backtest, 150)` so a hung fetch fails the
run, releases the lock, surfaces an error instead of an endless spinner; (b) on lifespan startup, UPDATE
engine=local status=running -> failed (orphan reset, since a clean start has no live local tasks). First
chart-open of an uncached symbol is still SLOW (full ISS pull on the VDS) but now bounded. Cached symbols
open fast (verified BRN6 via shared browser). TODO idea: have the agent also upsert bars to VDS, or
pre-cache campaign symbols, so first chart-open isn't a cold ISS pull.

**AGENT SELF-UPDATE (2026-06-05, WORKING/verified):** the i9 agent updates itself on command — no manual
copies. Agent polls `GET /api/v1/agent/control` each cycle for `update_token` (DB table agent_control,
key='update_token'); when it differs from the applied token (file `<repo>/.agent_update_token`), the agent
fetches each file in `agent/update_manifest.txt` from raw.githubusercontent (NOT the codeload tarball —
that host is intermittently blocked from the i9 and hung the loop), writes the token, and requests a
restart. RESTART: os.execv does NOT survive in a Windows console (returns to the shell → agent stops), so
the agent is launched via the supervised wrapper `agent\run_agent.cmd` (sets OPT_AGENT_WRAPPED=1); on update
it exits code 42 and the wrapper relaunches with the fresh code, console preserved. LAUNCH the agent with
`.\agent\run_agent.cmd [--insecure ...]`, NOT direct python. TO TRIGGER ("the command"): ssh hoster,
`sudo -u postgres psql project_stl -c "INSERT INTO agent_control(key,value) VALUES('update_token',
now()::text) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"`. When trader/ gains NEW files, add them
to agent/update_manifest.txt. Gotchas burned through: codeload tarball blocked from i9 → use raw per-file;
os.execv dies on Windows console → wrapper+exit-42. VDS tz = Europe/Moscow; use MSK in logs/reports.

**ADAPTIVE OPTIMIZER (2026-06-05):** `scripts/optimize_adaptive.py` (run ON VDS) — coarse-to-fine
REMOTE campaign, supersedes the old in-process `optimize_campaign.py`. Stages per (strategy x symbol):
r0 EXPLORE = N random combos over each numeric param's FULL schema range (wide net, default --explore 300);
r1..rD REFINE = top-K (default 5) by score=return×recovery_factor (filters: return>0, RF>=1.5, dd<=0.15,
trades 30..5000) → fine LOCAL grid around each winner (window halves per round: r1 span/4, r2 span/8), union+cap
(--max-combos 400), run. Orchestrator enqueues a round, polls backtest_runs until that round drains, reads
leaderboard, enqueues next. run_id = `opt-YYYYMMDD-HHMM-r{d}-{strat}-{sym}`. PROTOCOL CHANGE: job_body may
carry explicit `param_sets` (list of varying-param dicts, non-product) — opt_agent + VDS fallback run them
directly (merge with base_params) instead of expanding params_grid; agent_claim relays `param_sets`. Sweep
detection generalized: `_is_sweep_run`/`_sweep_campaign` accept camp- AND opt- prefixes (metrics-only to
leaderboard); a bare-cuid run (UI chart, engine=local) keeps FULL trades/equity. REQUIRES updated opt_agent.py
copied to the i9 (param_sets + opt- strip) — backward compatible with camp- jobs. Run e.g.:
`poetry run python scripts/optimize_adaptive.py --instruments 15 --explore 300 --rounds 2`.

**VDS FALLBACK SWEEPER (2026-06-04):** when the i9 agent is DOWN, queued remote sweep jobs would sit
forever. `trader/api/app.py::_vds_fallback_sweeper` (started in lifespan) drains them ON the VDS but
ONLY with spare capacity so it can't repeat the overload: skips a cycle while `os.getloadavg()[0]` >
`_FB_MAX_LOAD` (2.0) = resource ceiling; one job at a time under `_BACKTEST_LOCK`; grid subprocess is
already nice19+SCHED_IDLE+ionice; combos capped at `_FB_MAX_COMBOS` (150); only claims jobs left
'queued' > `_FB_STALE_SEC` (180s) with agent_id='vds-fallback'. The real agent claims within seconds,
so the fallback stays idle whenever the i9 is up (verified: dormant while campaign drained). Writes
metrics-only to optimization_leaderboard (same shape as agent_result). Env-tunable: VDS_FALLBACK_ENABLED
(0 to disable), VDS_FALLBACK_MAX_LOAD, VDS_FALLBACK_STALE_SEC, VDS_FALLBACK_MAX_COMBOS, VDS_FALLBACK_POLL_SEC.

**BUILD GOTCHA (2026-06-05):** `npx vite build` run via the Bash tool can SILENTLY FAIL — npx hits
MODULE_NOT_FOUND on node v22, and the rtk wrapper masks it as "ok" while serving a STALE dist (the
frontend hash doesn't change and new code never ships). Symptom: deployed bundle missing just-added code.
RELIABLE local build: PowerShell tool → `node node_modules\vite\bin\vite.js build` (bypasses npx AND rtk).
Verify before deploy: bundle hash changed + `Select-String dist\assets\*.js -Pattern '<new marker>'`. Then
tar dist to the VDS. Several mid-session frontend deploys were stale because of this; the full rebuild
(bundle index-U5b7khqu) shipped all current frontend at once.

**Infra recommendation given to user:** add swap (4-8Gi) or RAM on the VDS; 2Gi swap is too little.

See [[project-optimization-campaign]], [[project-lab-mvp-state]].
