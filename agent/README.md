# Shectory LAB — Optimization Agent (Windows)

Offloads backtest **parameter sweeps** from the small VDS onto this powerful PC
(i9 / 128 GB). The VDS only queues jobs; this agent pulls them over HTTPS, runs
all combinations across every CPU core, and posts results back. No inbound ports
are opened on the PC (pull model).

## Requirements
- Python 3.12 on PATH (or pass `-Python C:\path\to\python.exe`).
- This repository present on the PC (the agent imports `trader.lab`).
- `OPT_AGENT_TOKEN` — the shared secret (same value as on the VDS). Get it from the
  keymaster; never paste it into chat/commits.

## Install (one command, from the repo root)
```powershell
powershell -ExecutionPolicy Bypass -File agent\install.ps1 -Token "<OPT_AGENT_TOKEN value>"
```
If the token is already in your user environment you can omit `-Token`.
Optional: `-Api https://stl.shectory.ru` (default), `-Workers 16` (default = cores−2).

The installer:
1. creates `agent\.venv` and installs `httpx` + `pydantic`;
2. saves `OPT_AGENT_TOKEN` + `STL_API` to your user environment;
3. registers a Scheduled Task **ShectoryOptAgent** — starts at logon, runs hidden,
   auto-restarts if it ever stops;
4. starts it immediately.

## Verify it's running
```powershell
Get-ScheduledTask -TaskName ShectoryOptAgent      # State = Running
Get-Content "$env:TEMP\shectory_opt_agent.log" -Tail 20
```
You should see `idle… waiting for jobs`. Queue a sweep in the web UI
(**Backtest Lab → Перебор параметров → Мощный хост → Запустить**) and the log will
show it claim + compute the job.

## Control
```powershell
Stop-ScheduledTask  -TaskName ShectoryOptAgent     # pause
Start-ScheduledTask -TaskName ShectoryOptAgent     # resume
Unregister-ScheduledTask -TaskName ShectoryOptAgent -Confirm:$false   # uninstall task
```

## How it works
- The agent never exits on its own: network/DNS/5xx errors are caught and it keeps
  polling; a fatal loop error restarts with backoff. The Scheduled Task is a second
  safety net (restarts the process if the whole thing dies).
- Equity curves are downsampled to ~1500 points before upload (metrics stay exact).
- Manual run (foreground, for debugging), from the repo root:
  ```powershell
  $env:OPT_AGENT_TOKEN=[Environment]::GetEnvironmentVariable("OPT_AGENT_TOKEN","User")
  $env:STL_API="https://stl.shectory.ru"; $env:PYTHONUTF8="1"
  agent\.venv\Scripts\python.exe scripts\opt_agent.py --workers 16
  ```
