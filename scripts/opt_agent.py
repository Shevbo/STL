#!/usr/bin/env python3
"""
Optimization AGENT — runs on a powerful host (e.g. the Windows i9 / 128GB box) and
offloads parameter sweeps from the small shared VDS.

Pull model (no inbound ports needed on this host):
  loop:
    POST /api/v1/agent/claim   -> get next queued remote run (or 204 = idle)
    fetch bars from MOEX ISS (free), expand the param grid
    run all combos across a ProcessPoolExecutor (all CPU cores)
    POST /api/v1/agent/result  -> write results, mark run done

Auth: header X-Agent-Token must match the server's OPT_AGENT_TOKEN.

Run:
  set STL_API=https://stl.shectory.ru
  set OPT_AGENT_TOKEN=<same secret as server>
  poetry run python scripts/opt_agent.py            # default: cores-2 workers
  poetry run python scripts/opt_agent.py --workers 18 --poll 5
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import itertools
import json
import os
import re
import socket
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Force UTF-8 console so status lines (→ × …) print on Windows cp1251 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _log(msg: str) -> None:
    """Print with a local date/time prefix (the i9 runs on MSK), so every job line is
    timestamped in the console/log."""
    from datetime import datetime
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _user_env(name: str) -> str:
    """Read a persistent user env var straight from HKCU\\Environment. A Scheduled Task
    / headless launch may not inherit env vars added mid-session, so winreg is the
    reliable way to get OPT_AGENT_TOKEN/STL_API on Windows."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except Exception:
        return ""


# Keep the sweep on SPARE CPU so it never lags the user's foreground work. The main
# process runs below-normal; each compute worker runs at IDLE priority (Windows: only
# when cores are otherwise free; POSIX: nice 19). User keeps all cores, zero felt lag.
# Windows priority classes / POSIX nice per level. "idle" = only spare cores (default,
# zero felt lag); "below" = below-normal; "normal" = compete evenly with other work.
_PRIO_WIN = {"idle": 0x00000040, "below": 0x00004000, "normal": 0x00000020}
_PRIO_NICE = {"idle": 19, "below": 10, "normal": 0}
_PRIO_LEVELS = ("idle", "below", "normal")


def _set_priority(prio: str = "below") -> None:
    try:
        if sys.platform == "win32":
            import ctypes
            cls = _PRIO_WIN.get(prio, _PRIO_WIN["below"])
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), cls)
        else:
            os.nice(_PRIO_NICE.get(prio, 10))
    except Exception:
        pass


def _worker_init() -> None:
    # Pool workers are pure CPU crunch. Priority is chosen by the operator (env set
    # before the pool is built); default idle = use only spare cores.
    _set_priority(os.environ.get("OPT_AGENT_WORKER_PRIO", "idle"))


def _tee_log(path: str) -> None:
    """Mirror stdout/stderr to a rotating-ish log file (truncate if >5MB) so the
    agent is observable when launched headless by Task Scheduler (no console)."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 5_000_000:
            open(path, "w").close()
        f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)

        class _Tee:
            def __init__(self, *streams): self.streams = streams
            def write(self, s):
                for st in self.streams:
                    try:
                        st.write(s)
                        st.flush()
                    except Exception:
                        pass
            def flush(self):
                for st in self.streams:
                    try: st.flush()
                    except Exception: pass
        sys.stdout = _Tee(sys.stdout, f)
        sys.stderr = _Tee(sys.stderr, f)
    except Exception:
        pass


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

# psutil gives real CPU%/RAM for the heartbeat. It is NOT a hard dependency, so the
# agent degrades gracefully when it is absent (reports cores/workers/activity only,
# and the monitor tells the operator to `pip install psutil`).
try:
    import psutil  # noqa: E402
except Exception:  # noqa: BLE001
    psutil = None

# Bumped whenever the agent's wire behaviour changes; reported in the heartbeat so the
# monitor shows whether the i9 is running the latest opt_agent.
AGENT_VERSION = "2026-07-28-leader-strat"

# ── self-update ────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Per-file updates come from raw.githubusercontent (reachable from the i9; the codeload
# tarball host is intermittently blocked there).
RAW_BASE = os.environ.get("OPT_AGENT_RAW_BASE", "https://raw.githubusercontent.com/Shevbo/STL/main")
TOKEN_FILE = os.path.join(REPO_ROOT, ".agent_update_token")

# Local resource page served on 127.0.0.1 — self-contained (no external assets), polls
# /metrics every 1.5s. Mirrors what the STL heartbeat sends, viewable right on the i9.
_STATUS_HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>i9 · ресурсы агента STL</title>
<style>
  :root{--bg:#0a0e17;--panel:#0f1524;--bd:#1e2a44;--ink:#d7e0f0;--dim:#7386a8;--lbl:#5a6c90;
        --grn:#43c463;--amb:#f5a623;--red:#ff5c5c;--cyan:#4dd0e1;--mono:ui-monospace,Consolas,monospace}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.5 system-ui,Segoe UI,sans-serif;padding:18px}
  h1{font-size:16px;margin:0 0 2px} .sub{color:var(--dim);font-size:12px;margin-bottom:14px}
  .sub b{color:var(--cyan)} .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:1000px}
  .card{background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:12px 14px}
  .card.wide{grid-column:1/3}
  .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--lbl);margin-bottom:6px}
  .big{font-size:34px;font-weight:700;line-height:1} .big.hot{color:var(--red)} .big.ok{color:var(--grn)}
  .bar{height:8px;background:#0b1220;border-radius:4px;overflow:hidden;margin-top:8px}
  .bar > i{display:block;height:100%;background:linear-gradient(90deg,#2f8f49,var(--grn))}
  .bar > i.hot{background:linear-gradient(90deg,#b5502a,var(--red))}
  .cores{display:flex;align-items:flex-end;gap:3px;height:64px;margin-top:6px}
  .core{flex:1;min-width:6px;height:100%;background:#0b1220;border-radius:2px;display:flex;align-items:flex-end;overflow:hidden}
  .core > i{width:100%;background:var(--cyan);border-radius:2px}
  .core > i.hot{background:var(--red)}
  .kv{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;border-bottom:1px solid #131c30}
  .kv:last-child{border-bottom:none} .kv span{color:var(--dim)}
  .act{font-size:15px} .act b{color:var(--cyan)}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .dot.run{background:var(--grn);box-shadow:0 0 6px #43c463aa} .dot.idle{background:#556}
  table{width:100%;border-collapse:collapse;font-size:12px} th,td{text-align:left;padding:4px 6px}
  th{color:var(--lbl);font-weight:500;font-size:10px;text-transform:uppercase;border-bottom:1px solid var(--bd)}
  td{border-bottom:1px solid #131c30} td.num{text-align:right;font-family:var(--mono)}
  .warn{color:var(--amb);font-size:12px} #conn{color:var(--red);font-size:12px}
</style></head><body>
  <h1>🖥 i9 · ресурсы агента STL</h1>
  <div class="sub" id="hdr">подключение…</div>
  <div id="conn"></div>
  <div class="grid">
    <div class="card"><div class="lbl">Загрузка CPU</div>
      <div id="cpuBig" class="big">—</div><div class="bar"><i id="cpuBar" style="width:0"></i></div>
      <div id="cpuWarn" class="warn" style="margin-top:8px"></div>
    </div>
    <div class="card"><div class="lbl">RAM</div>
      <div id="ramBig" class="big">—</div><div class="bar"><i id="ramBar" style="width:0"></i></div>
      <div id="ramSub" class="sub" style="margin:8px 0 0"></div>
    </div>
    <div class="card wide"><div class="lbl">Ядра (<span id="coreN">—</span>) · воркеры <span id="wk" class="mono">—</span></div>
      <div class="cores" id="cores"></div>
    </div>
    <div class="card wide"><div class="lbl">Сейчас</div>
      <div class="act" id="act">—</div>
    </div>
    <div class="card wide"><div class="lbl">Последние прогоны</div>
      <table><thead><tr><th>Инстр.</th><th class="num">Комбо</th><th class="num">Сек</th><th class="num">Комбо/с</th><th class="num">OK</th></tr></thead>
      <tbody id="recent"><tr><td colspan="5" style="color:var(--dim)">—</td></tr></tbody></table>
    </div>
  </div>
<script>
function fmtDur(s){s=Math.round(s||0);var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+'ч ':'')+(h||m?m+'м ':'')+x+'с';}
function esc(v){return (''+v).replace(/[<>&]/g,function(c){return {'<':'&lt;','>':'&gt;','&':'&amp;'}[c];});}
function render(d){
  document.getElementById('conn').textContent='';
  var a=d.activity||{};
  document.getElementById('hdr').innerHTML='агент <b>'+esc(d.agent_id||'?')+'</b> · v'+esc(d.version||'?')
    +' · аптайм '+fmtDur(d.uptime_sec)+' · '+esc(d.api||'')+' · poll '+esc(d.poll)+'с';
  // CPU
  var cpu=d.cpu_pct, big=document.getElementById('cpuBig'), bar=document.getElementById('cpuBar');
  if(cpu==null){ big.textContent='—'; big.className='big'; bar.style.width='0';
    document.getElementById('cpuWarn').textContent='psutil не установлен — на i9: pip install psutil';
  } else { var hot=cpu>=85; big.textContent=cpu.toFixed(0)+'%'; big.className='big '+(hot?'hot':'ok');
    bar.style.width=Math.min(100,cpu)+'%'; bar.className=hot?'hot':''; document.getElementById('cpuWarn').textContent=''; }
  // RAM
  var rb=document.getElementById('ramBig'), rbar=document.getElementById('ramBar');
  if(d.ram_pct==null){ rb.textContent='—'; rbar.style.width='0'; document.getElementById('ramSub').textContent=''; }
  else { rb.textContent=d.ram_pct.toFixed(0)+'%'; rbar.style.width=Math.min(100,d.ram_pct)+'%';
    document.getElementById('ramSub').textContent=(d.ram_used_mb||0).toLocaleString()+' / '+(d.ram_total_mb||0).toLocaleString()+' МБ'; }
  // cores
  document.getElementById('coreN').textContent=d.cpu_count||'?';
  document.getElementById('wk').textContent=(d.workers==null?'?':d.workers);
  var wrap=document.getElementById('cores'), pc=d.per_core||[];
  if(pc.length!==wrap.children.length){ wrap.innerHTML=''; for(var i=0;i<pc.length;i++){var c=document.createElement('div');c.className='core';c.innerHTML='<i></i>';wrap.appendChild(c);} }
  for(var i=0;i<pc.length;i++){ var f=wrap.children[i].firstChild; f.style.height=Math.max(3,Math.min(100,pc[i]))+'%'; f.className=pc[i]>=90?'hot':''; wrap.children[i].title=Math.round(pc[i])+'%'; }
  // activity
  var actEl=document.getElementById('act');
  if(a.state==='job') actEl.innerHTML='<span class="dot run"></span>считает <b>'+esc(a.symbol)+'</b> · '+esc(a.combos)+' комбо <span class="sub">('+esc(a.run_id||'')+')</span>';
  else if(a.state==='task') actEl.innerHTML='<span class="dot run"></span>задача <b>'+esc(a.func||'')+'</b> · '+esc(a.units)+' ед.';
  else if(a.state==='idle') actEl.innerHTML='<span class="dot idle"></span>простаивает — ждёт джобы';
  else actEl.innerHTML='<span class="dot idle"></span>'+esc(a.state||'—');
  // recent
  var rows=(d.recent||[]).map(function(r){return '<tr><td class="mono">'+esc(r.symbol)+'</td><td class="num">'+esc(r.combos)
    +'</td><td class="num">'+esc(r.secs)+'</td><td class="num">'+esc(r.cps)+'</td><td class="num">'+esc(r.ok)+'</td></tr>';}).join('');
  document.getElementById('recent').innerHTML=rows||'<tr><td colspan="5" style="color:var(--dim)">пока нет</td></tr>';
}
async function tick(){ try{ var r=await fetch('/metrics',{cache:'no-store'}); render(await r.json()); }
  catch(e){ document.getElementById('conn').textContent='нет связи с агентом (перезапущен?)'; } }
setInterval(tick,1500); tick();
</script></body></html>"""


class _Restart(Exception):
    """Raised after a self-update to break the loop and re-exec the process."""


def _read_token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_token(tok: str) -> None:
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(tok or "")
    except Exception:
        pass


def _patch_httpx_insecure() -> None:
    """Make every httpx.AsyncClient skip TLS verification by default. Needed behind
    a TLS-INTERCEPTING corporate proxy (e.g. local 127.0.0.1:port) that re-signs
    certs with a CA Python doesn't trust — otherwise both agent calls and the ISS
    bar fetch fail with 'self signed certificate'. Idempotent."""
    if getattr(httpx.AsyncClient, "_stl_insecure", False):
        return
    _orig = httpx.AsyncClient.__init__

    def _init(self, *a, **kw):
        kw.setdefault("verify", False)
        _orig(self, *a, **kw)

    httpx.AsyncClient.__init__ = _init
    httpx.AsyncClient._stl_insecure = True
    try:
        import warnings
        warnings.filterwarnings("ignore", message="Unverified HTTPS")
    except Exception:
        pass


# ── worker (separate process) ─────────────────────────────────────────────────
def _run_chunk(args: tuple) -> list[dict]:
    """Run a chunk of param-sets in a worker process. Self-contained: rebuilds the
    strategy module from script_code, runs each combo, returns serializable rows."""
    import asyncio as _asyncio
    import types as _types
    from trader.lab.backtest import run_single_backtest, _demote_to_background
    from trader.lab.runtime import Bar

    # Workers inherit env; honor the insecure flag for the ISS fetch they do.
    if os.environ.get("OPT_AGENT_INSECURE"):
        _patch_httpx_insecure()
    script_code, bars_data, symbol, param_sets, point_value, initial_margin = args
    _demote_to_background()  # be a polite background citizen on the shared host too
    from trader.lab.script_guard import validate_script
    validate_script(script_code)
    bars = [Bar(**b) for b in bars_data]
    mod = _types.ModuleType("robot_script")
    exec(compile(script_code, "<robot>", "exec"), mod.__dict__)

    def _downsample(curve: list, cap: int = 1500) -> list:
        # Keep metrics exact (already computed); shrink the equity curve for transport.
        # The chart resamples anyway, so ~1500 points is plenty. Always keep last point.
        n = len(curve)
        if n <= cap:
            return curve
        step = n / cap
        out = [curve[int(i * step)] for i in range(cap)]
        if out[-1] is not curve[-1]:
            out.append(curve[-1])
        return out

    async def _all():
        out = []
        for ps in param_sets:
            try:
                r = await run_single_backtest(mod, bars, symbol, ps, point_value=point_value,
                                              initial_margin=initial_margin)
                if isinstance(r.get("equity_curve"), list):
                    r["equity_curve"] = _downsample(r["equity_curve"])
                out.append({"ok": True, "params": ps, "result": r})
            except Exception as exc:  # noqa: BLE001
                out.append({"ok": False, "params": ps, "error": str(exc)})
        return out

    return _asyncio.run(_all())


def _chunked(seq: list, n: int) -> list[list]:
    if n <= 0:
        return [seq]
    size = max(1, (len(seq) + n - 1) // n)
    return [seq[i:i + size] for i in range(0, len(seq), size)]


# ── generic task worker (separate process) ─────────────────────────────────────
def _run_task_unit(module: str, func: str, arg):
    """Import a repo module and call func(arg) in a worker. Lets the agent run ANY
    committed task (e.g. team-46 sweep) on the i9's cores without an agent rebuild —
    the task code arrives via self-update from the repo."""
    import importlib
    _set_priority("idle")
    if os.environ.get("OPT_AGENT_INSECURE"):
        _patch_httpx_insecure()
    mod = importlib.import_module(module)
    return getattr(mod, func)(arg)


# ── agent ─────────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self, api: str, token: str, workers: int, poll: float, proxy: str = "",
                 status_port: int = 8099, priority: str = "idle"):
        self.api = api.rstrip("/")
        self.token = token
        self.workers = workers
        self.poll = poll
        self.status_port = status_port
        self._priority = priority if priority in _PRIO_LEVELS else "idle"
        # Remembers the worker count/priority we last applied so a repeated control
        # value doesn't rebuild the pool every poll.
        self._applied_ctl = (workers, self._priority)
        # Corporate networks often block direct outbound :443 ("All connection
        # attempts failed"). Route httpx through the proxy if given (or via the
        # standard HTTPS_PROXY env var, which httpx reads by default).
        self.proxy = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        self.agent_id = f"{socket.gethostname()}:{os.getpid()}"
        self.h = {"X-Agent-Token": token, "Content-Type": "application/json"}
        self.applied_token = _read_token()   # last self-update token applied
        # What the CPU is doing right now, surfaced by the heartbeat. Mutated by the
        # claim loop (job/task/idle); read by the concurrent heartbeat task.
        self._activity: dict = {"state": "starting"}
        # Latest metrics snapshot + recent jobs for the LOCAL status page (127.0.0.1).
        # The heartbeat task refills _last_metrics; the http thread only reads it (no
        # psutil.cpu_percent() from two callers, which would split the sampling window).
        self._last_metrics: dict = {}
        self._recent: collections.deque = collections.deque(maxlen=12)
        # Top combos of the last finished job (the forming hit-parade), with full params
        # so the monitor can re-run any of them into a chart. Refilled per job.
        self._leaders: list = []
        self._started = time.time()
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)  # prime: first call is meaningless
            except Exception:  # noqa: BLE001
                pass

    def _client(self) -> httpx.AsyncClient:
        kw: dict = {"timeout": 30}
        if self.proxy:
            kw["proxy"] = self.proxy
        return httpx.AsyncClient(**kw)

    async def control(self, client: httpx.AsyncClient) -> dict:
        """Poll the control channel: self-update token + operator-set worker count /
        priority. Returns the raw dict ({} on any error)."""
        try:
            r = await client.get(f"{self.api}/api/v1/agent/control", headers=self.h, timeout=15)
            if r.status_code != 200:
                return {}
            return r.json() or {}
        except Exception:
            return {}

    def _apply_control(self, ctl: dict) -> bool:
        """Apply operator-set worker count / priority from the control channel. Returns
        True when the pool must be rebuilt (count or priority actually changed)."""
        cpu = os.cpu_count() or 64
        new_w, new_p = self.workers, self._priority
        w = ctl.get("workers")
        if w is not None:
            try:
                w = int(w)
                if 1 <= w <= cpu:
                    new_w = w
            except (TypeError, ValueError):
                pass
        p = ctl.get("priority")
        if isinstance(p, str) and p in _PRIO_LEVELS:
            new_p = p
        if (new_w, new_p) == self._applied_ctl:
            return False
        self.workers, self._priority = new_w, new_p
        self._applied_ctl = (new_w, new_p)
        _log(f"control: workers={new_w} priority={new_p} → rebuild pool")
        return True

    async def _self_update(self, token: str) -> bool:
        """Pull fresh code per-file from raw.githubusercontent (reachable from the i9;
        the codeload tarball is intermittently blocked there). Files to update come from
        agent/update_manifest.txt. Best-effort: on ANY failure keep the current code and
        leave applied_token unchanged so it retries next cycle. Short timeouts so a flaky
        network never blocks the loop for long."""
        print(f"self-update → fetching files (token={token})", flush=True)
        try:
            async with self._client() as client:
                mr = await client.get(f"{RAW_BASE}/agent/update_manifest.txt",
                                      follow_redirects=True, timeout=20)
                mr.raise_for_status()
                files = [ln.strip() for ln in mr.text.splitlines()
                         if ln.strip() and not ln.startswith("#")]
                if not files:
                    raise RuntimeError("empty manifest")
                blobs = {}
                for rel in files:                       # download ALL first, then write
                    r = await client.get(f"{RAW_BASE}/{rel}", follow_redirects=True, timeout=20)
                    r.raise_for_status()
                    blobs[rel] = r.content
            for rel, content in blobs.items():
                dst = os.path.join(REPO_ROOT, *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(content)
            _write_token(token)
            self.applied_token = token
            print(f"self-update → {len(blobs)} files updated; restart", flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"self-update FAILED (keeping current code): {exc}", flush=True)
            return False

    async def claim(self, client: httpx.AsyncClient):
        r = await client.post(f"{self.api}/api/v1/agent/claim",
                              json={"agent_id": self.agent_id}, headers=self.h, timeout=30)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    async def post_result(self, client: httpx.AsyncClient, payload: dict):
        r = await client.post(f"{self.api}/api/v1/agent/result",
                              json=payload, headers=self.h, timeout=120)
        r.raise_for_status()
        return r.json()

    def _metrics(self) -> dict:
        """Snapshot of what the i9 CPU is doing, for the heartbeat. All best-effort —
        psutil may be absent; never raise from here."""
        m = {"agent_id": self.agent_id, "version": AGENT_VERSION, "workers": self.workers,
             "priority": self._priority, "cpu_count": os.cpu_count() or 0,
             "psutil": psutil is not None, "activity": self._activity,
             "leaders": self._leaders}
        if psutil is not None:
            try:
                m["cpu_pct"] = round(psutil.cpu_percent(interval=None), 1)      # since last call (~hb period)
                m["per_core"] = [round(x, 0) for x in psutil.cpu_percent(interval=None, percpu=True)]
                vm = psutil.virtual_memory()
                m["ram_pct"] = round(vm.percent, 1)
                m["ram_used_mb"] = round(vm.used / 1e6)
                m["ram_total_mb"] = round(vm.total / 1e6)
            except Exception:  # noqa: BLE001
                pass
        self._last_metrics = m          # freshest snapshot for the local status page
        return m

    async def _heartbeat_loop(self, client: httpx.AsyncClient, period: float = 4.0):
        """Independent task: report CPU/RAM/activity every `period`s even WHILE a job is
        crunching (the claim loop is blocked awaiting the pool then, so it can't). Runs
        on the SAME httpx client — concurrent POSTs are fine. Never raises."""
        while True:
            try:
                await client.post(f"{self.api}/api/v1/agent/heartbeat",
                                  json=self._metrics(), headers=self.h, timeout=10)
            except Exception:  # noqa: BLE001 — a missed heartbeat just ages out in the monitor
                pass
            await asyncio.sleep(period)

    # ── local status page (127.0.0.1) ────────────────────────────────────────────
    def _status_payload(self) -> dict:
        """Everything the local page shows: the freshest metric snapshot (refilled by
        the heartbeat task) + recent jobs + uptime. Read-only, thread-safe (reads a
        dict reference the heartbeat swaps wholesale)."""
        m = dict(self._last_metrics or {"activity": self._activity})
        m["recent"] = list(self._recent)
        m["uptime_sec"] = round(time.time() - self._started)
        m["api"] = self.api
        m["poll"] = self.poll
        return m

    def _start_status_server(self) -> None:
        """Serve a live resource page on 127.0.0.1:<status_port> (like the QUIK agent's
        :8071). Loopback only, no auth. Best-effort — a bind failure never stops the
        agent."""
        agent = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence per-request logging
                pass

            def _send(self, body: bytes, ctype: str):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def do_GET(self):
                if self.path.startswith("/metrics"):
                    self._send(json.dumps(agent._status_payload()).encode(), "application/json")
                else:
                    self._send(_STATUS_HTML.encode("utf-8"), "text/html; charset=utf-8")

        try:
            srv = ThreadingHTTPServer(("127.0.0.1", self.status_port), _H)
        except Exception as exc:  # noqa: BLE001 — port taken / restricted
            _log(f"status page disabled (bind :{self.status_port} failed: {exc})")
            return
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _log(f"status page → http://127.0.0.1:{self.status_port}/")

    # ── generic tasks ───────────────────────────────────────────────────────
    async def claim_task(self, client: httpx.AsyncClient):
        r = await client.post(f"{self.api}/api/v1/agent/task/claim",
                              json={"agent_id": self.agent_id}, headers=self.h, timeout=30)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    async def post_task_result(self, client: httpx.AsyncClient, payload: dict):
        r = await client.post(f"{self.api}/api/v1/agent/task/result",
                              json=payload, headers=self.h, timeout=180)
        r.raise_for_status()
        return r.json()

    async def process_task(self, client: httpx.AsyncClient, task: dict, pool: ProcessPoolExecutor):
        tid, module, func = task["task_id"], task["module"], task["func"]
        args = task.get("args")
        if not isinstance(args, list):
            args = [args]
        _log(f"[task {tid}] {module}.{func} × {len(args)} units on {self.workers} workers")
        self._activity = {"state": "task", "task_id": tid, "func": f"{module}.{func}",
                          "units": len(args), "since": time.time()}
        loop = asyncio.get_event_loop()
        t0 = time.time()
        futs = [loop.run_in_executor(pool, _run_task_unit, module, func, a) for a in args]
        done = await asyncio.gather(*futs, return_exceptions=True)
        results = [({"error": f"{type(r).__name__}: {r}"} if isinstance(r, Exception) else r)
                   for r in done]
        ok = sum(1 for r in results if not (isinstance(r, dict) and r.get("error")))
        _log(f"[task {tid}] done {ok}/{len(results)} in {time.time()-t0:.1f}s")
        await self.post_task_result(client, {"task_id": tid, "results": results})

    @staticmethod
    def _parse_date(s: str) -> date:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()

    def _expand(self, base_params: dict, grid: dict) -> list[dict]:
        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = list(itertools.product(*values))
        return [{**base_params, **dict(zip(keys, c))} for c in combos]

    async def process(self, client: httpx.AsyncClient, job: dict, pool: ProcessPoolExecutor):
        from trader.lab.iss_loader import load_bars_iss

        run_id = job["run_id"]
        symbol = job["symbol"]
        try:
            d_from = self._parse_date(job["date_from"])
            d_to = self._parse_date(job["date_to"])
            bars = await load_bars_iss(symbol, d_from, d_to, interval=1)
            if not bars:
                await self.post_result(client, {"run_id": run_id, "error": f"no bars for {symbol}"})
                return
            bars_data = [{"time": b.time, "open": b.open, "high": b.high,
                          "low": b.low, "close": b.close, "volume": b.volume} for b in bars]

            # Explicit combos (random explore / unioned refine grids) run as-is;
            # otherwise expand the product grid.
            ps_list = job.get("param_sets")
            if ps_list:
                base = job.get("base_params", {})
                param_sets = [{**base, **ps} for ps in ps_list]
            else:
                param_sets = self._expand(job["base_params"], job["params_grid"])
            _log(f"[{run_id}] {symbol} {len(bars)} bars × {len(param_sets)} combos "
                 f"on {self.workers} workers")
            self._activity = {"state": "job", "run_id": run_id, "symbol": symbol,
                              "combos": len(param_sets), "since": time.time()}

            chunks = _chunked(param_sets, self.workers)
            im = job.get("initial_margin", 0) or 0
            args = [(job["script_code"], bars_data, symbol, ch, job["point_value"], im) for ch in chunks]
            loop = asyncio.get_event_loop()
            t0 = time.time()
            futs = [loop.run_in_executor(pool, _run_chunk, a) for a in args]
            chunk_results = await asyncio.gather(*futs)
            results = [r for chunk in chunk_results for r in chunk]
            dt = time.time() - t0
            ok = sum(1 for r in results if r.get("ok"))
            _log(f"[{run_id}] done {ok}/{len(results)} in {dt:.1f}s "
                 f"({len(results)/dt:.0f} combos/s)")
            self._recent.appendleft({"symbol": symbol, "combos": len(param_sets),
                                     "secs": round(dt, 1), "ok": ok,
                                     "cps": round(len(results) / dt, 1) if dt else 0,
                                     "at": time.time()})
            # Forming hit-parade: top-3 combos of THIS job by net profit, with full
            # params so the monitor can re-run any into a chart (grabbed BEFORE the
            # trades/equity strip below — but we keep only metrics + params here).
            # id стратегии для клика «пересчитать» в мониторе. По имени прогона он
            # угадывается только у кампаний (opt-…-<strategy>-<symbol>); у
            # ИНТЕРАКТИВНОГО прогона имя это голый cuid без дефисов, и раньше сюда
            # уезжала пустая строка — клик по такому лидеру в Ботсторе падал с
            # «Не передан id стратегии» (найдено 28.07). Достаём из кода стратегии,
            # тем же способом, что и сервер: make_on_bar('<id>') либо имя модуля.
            strat = run_id.split("-")[-2] if run_id.count("-") >= 2 else ""
            if not strat:
                code = job.get("script_code") or ""
                m = re.search(r"make_on_bar\(\s*['\"]([^'\"]+)['\"]", code)
                if not m:
                    m = re.search(r"strategies\.([A-Za-z_][A-Za-z0-9_]*)\s+import", code)
                strat = m.group(1) if m else ""
            oks = [e for e in results if e.get("ok") and isinstance(e.get("result"), dict)]
            top = sorted(oks, key=lambda e: (e["result"].get("net_profit") or -1e18), reverse=True)[:3]
            self._leaders = [{"strategy": strat, "symbol": symbol, "run_id": run_id,
                              "net": round(e["result"].get("net_profit") or 0),
                              "rf": e["result"].get("recovery_factor"),
                              "trades": e["result"].get("total_trades"),
                              "params": e["params"]} for e in top]
            # Multi-combo sweeps only need metrics for the leaderboard — strip the
            # bulky trades + equity_curve arrays so we don't flood the small VDS
            # Postgres AND don't hit nginx body-size limits (413 Entity Too Large).
            # Keep full data for single-combo runs (chart can render trades).
            if len(param_sets) > 1:
                for e in results:
                    if e.get("ok"):
                        e["result"].pop("trades", None)
                        e["result"].pop("equity_curve", None)
            await self.post_result(client, {"run_id": run_id, "results": results})
        except Exception as exc:  # noqa: BLE001
            _log(f"[{run_id}] FAILED: {exc}")
            try:
                await self.post_result(client, {"run_id": run_id, "error": str(exc)})
            except Exception:
                pass

    async def _loop_once(self):
        """One full life of the agent: a process pool + http client + claim loop.
        Returns only on a fatal error (pool/client death); the outer run() restarts."""
        _log(f"agent {self.agent_id} → {self.api}  workers={self.workers}  poll={self.poll}s"
             + (f"  proxy={self.proxy}" if self.proxy else ""))
        _set_priority("below")           # main process: below-normal (yields to the user)
        os.environ["OPT_AGENT_WORKER_PRIO"] = self._priority   # spawned workers read this
        with ProcessPoolExecutor(max_workers=self.workers, initializer=_worker_init) as pool:
            async with self._client() as client:
                # Independent heartbeat so CPU%/activity keep flowing WHILE a job blocks
                # the claim loop below. Cancelled when this life ends.
                hb = asyncio.create_task(self._heartbeat_loop(client))
                try:
                    return await self._claim_loop(client, pool)
                finally:
                    hb.cancel()

    async def _claim_loop(self, client: httpx.AsyncClient, pool: ProcessPoolExecutor):
        idle_note = True
        while True:
            # control channel: self-update token + operator worker/priority control
            ctl = await self.control(client)
            tok = ctl.get("update_token")
            if tok and tok != self.applied_token:
                if await self._self_update(tok):
                    return "RESTART"   # exits pool/client cleanly, run() re-execs
            if self._apply_control(ctl):
                return "REBUILD"       # tear the pool down + rebuild with new workers/priority
            # Generic offloaded tasks take priority over grid campaign jobs.
            try:
                task = await self.claim_task(client)
            except Exception as exc:  # noqa: BLE001
                task = None
                _log(f"task claim error: {exc}")
            if task is not None:
                idle_note = True
                try:
                    await self.process_task(client, task, pool)
                except Exception as exc:  # noqa: BLE001 — never let one task kill the loop
                    _log(f"task error (continuing): {exc}")
                continue
            try:
                job = await self.claim(client)
            except Exception as exc:  # noqa: BLE001 — DNS/network/5xx: keep polling
                _log(f"claim error: {exc}")
                await asyncio.sleep(self.poll)
                continue
            if job is None:
                self._activity = {"state": "idle"}
                if idle_note:
                    _log("idle… waiting for jobs")
                    idle_note = False
                await asyncio.sleep(self.poll)
                continue
            idle_note = True
            try:
                await self.process(client, job, pool)
            except Exception as exc:  # noqa: BLE001 — never let one job kill the loop
                _log(f"process error (continuing): {exc}")
                await asyncio.sleep(self.poll)

    async def run(self):
        """Supervisor: the agent must NEVER exit on its own. Any fatal error in a
        loop life (pool crash, client teardown) is caught and the loop restarts
        after a short backoff. Stop only via Ctrl+C / process kill."""
        self._start_status_server()      # local resource page, survives loop restarts
        backoff = 5
        while True:
            try:
                res = await self._loop_once()
                if res == "RESTART":
                    self._reexec()   # never returns
                elif res == "REBUILD":
                    _log("rebuilding pool (worker/priority change)…")
                    continue         # new _loop_once builds a fresh pool with new settings
            except KeyboardInterrupt:
                print("stopped (KeyboardInterrupt)", flush=True)
                return
            except Exception as exc:  # noqa: BLE001
                print(f"FATAL loop error, restarting in {backoff}s: {exc}", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                backoff = 5

    @staticmethod
    def _reexec():
        """Restart into the just-updated code. Windows os.execv in a console does NOT
        survive (returns to the shell), so the SUPPORTED path is a wrapper
        (agent/run_agent.cmd) that relaunches on exit code 42. Without a wrapper, try
        in-place execv (works on POSIX); if that fails, exit 42 so a wrapper/Task can
        relaunch."""
        print("restart for self-update…", flush=True)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        if os.environ.get("OPT_AGENT_WRAPPED"):
            os._exit(42)                       # wrapper relaunches with fresh code
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            print(f"execv failed ({exc}); exiting 42 — relaunch the agent (use run_agent.cmd)", flush=True)
            os._exit(42)


def main():
    ap = argparse.ArgumentParser(description="Shectory LAB optimization agent")
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    ap.add_argument("--token", default=os.environ.get("OPT_AGENT_TOKEN", ""))
    ap.add_argument("--workers", type=int,
                    # ponytail: HARD-cap the default at 10. cores-2 pegged the
                    # 20-core i9 at 100% (operator alarmed twice); a stale
                    # OPT_AGENT_WORKERS=16 from the old install also gets capped.
                    # Only an explicit CLI --workers N exceeds 10.
                    default=min(10, int(os.environ.get("OPT_AGENT_WORKERS", "0"))
                                or max(1, (os.cpu_count() or 4) - 2)))
    ap.add_argument("--poll", type=float, default=float(os.environ.get("OPT_AGENT_POLL", "5")))
    ap.add_argument("--proxy", default=os.environ.get("OPT_AGENT_PROXY", ""),
                    help="HTTP(S) proxy URL for outbound, e.g. http://proxy.corp:8080 "
                         "(falls back to HTTPS_PROXY/HTTP_PROXY env)")
    ap.add_argument("--insecure", action="store_true",
                    default=bool(os.environ.get("OPT_AGENT_INSECURE")),
                    help="skip TLS verification (behind a TLS-intercepting proxy)")
    ap.add_argument("--log", default=os.environ.get("OPT_AGENT_LOG",
                    os.path.join(os.environ.get("TEMP", "."), "shectory_opt_agent.log")))
    ap.add_argument("--status-port", type=int,
                    default=int(os.environ.get("OPT_AGENT_STATUS_PORT", "8099")),
                    help="local resource page on 127.0.0.1:<port> (0 disables)")
    ap.add_argument("--priority", choices=("idle", "below", "normal"),
                    default=os.environ.get("OPT_AGENT_PRIORITY", "idle"),
                    help="worker CPU priority (idle=spare cores only; overridden live by the control channel)")
    args = ap.parse_args()
    if args.log:
        _tee_log(args.log)
    # Headless (Scheduled Task / Startup) may not inherit the user env — fall back to
    # reading the persisted token/API straight from the registry.
    if not args.token:
        args.token = _user_env("OPT_AGENT_TOKEN")
    if not os.environ.get("STL_API") and "--api" not in sys.argv:
        api_reg = _user_env("STL_API")
        if api_reg:
            args.api = api_reg
    if not args.token:
        print("ERROR: set OPT_AGENT_TOKEN (env) or --token", file=sys.stderr)
        sys.exit(2)
    if args.insecure:
        os.environ["OPT_AGENT_INSECURE"] = "1"   # so spawned workers inherit it
        _patch_httpx_insecure()
        print("WARNING: TLS verification DISABLED (--insecure)", flush=True)
    asyncio.run(Agent(args.api, args.token, args.workers, args.poll, args.proxy,
                      status_port=args.status_port, priority=args.priority).run())


if __name__ == "__main__":
    main()
