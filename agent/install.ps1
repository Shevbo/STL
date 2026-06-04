<#
  Shectory LAB — optimization agent installer (Windows).

  What it does:
    1. Creates a local venv in agent\.venv and installs requirements.
    2. Saves your OPT_AGENT_TOKEN + STL_API to user environment variables.
    3. Registers a Windows Scheduled Task "ShectoryOptAgent" that:
         - starts the agent at logon,
         - restarts it automatically if it ever stops,
         - runs hidden in the background.

  Run (from the repo root, PowerShell):
    powershell -ExecutionPolicy Bypass -File agent\install.ps1

  Pass the token explicitly if it's not already in your environment:
    powershell -ExecutionPolicy Bypass -File agent\install.ps1 -Token "<value>"
#>
param(
  [string]$Token = "",
  [string]$Api = "https://stl.shectory.ru",
  [int]$Workers = 0,          # 0 = auto (cores - 2)
  [string]$Python = ""        # path to python.exe; auto-detected if empty
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot           # repo root (parent of agent\)
$agentDir = $PSScriptRoot
$venv = Join-Path $agentDir ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"

Write-Host "== Shectory opt-agent installer ==" -ForegroundColor Cyan
Write-Host "repo: $repo"

# 1. find python
if (-not $Python) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python -or -not (Test-Path $Python)) {
  throw "python.exe not found. Install Python 3.12 and/or pass -Python <path>."
}
Write-Host "python: $Python"

# 2. venv + deps. If a venv was COPIED from another machine its pyvenv.cfg/exe
# point at a foreign python — detect that and recreate from THIS machine's python.
$venvBroken = $true
if (Test-Path $venvPy) {
  try { & $venvPy -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $venvBroken = $false } } catch {}
}
if ($venvBroken) {
  if (Test-Path $venv) { Write-Host "Recreating venv (was foreign/broken)…"; Remove-Item -Recurse -Force $venv }
  else { Write-Host "Creating venv…" }
  & $Python -m venv $venv
}
Write-Host "Installing requirements…"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r (Join-Path $agentDir "requirements.txt")

# 3. token + api into user env
if (-not $Token) { $Token = [Environment]::GetEnvironmentVariable("OPT_AGENT_TOKEN","User") }
if (-not $Token) {
  Write-Host "OPT_AGENT_TOKEN not provided and not in user env." -ForegroundColor Yellow
  Write-Host "Set it first (value from keymaster) or pass -Token. Aborting." -ForegroundColor Yellow
  exit 1
}
[Environment]::SetEnvironmentVariable("OPT_AGENT_TOKEN", $Token, "User")
[Environment]::SetEnvironmentVariable("STL_API", $Api, "User")
$Token = $null
Write-Host "Saved OPT_AGENT_TOKEN + STL_API to user environment."

# 4. scheduled task (start at logon + auto-restart)
# Run the venv python DIRECTLY (no cmd.exe wrapper — cmd mangles paths with spaces
# like "Shectory Trade & Lab"). WorkingDirectory = repo so `trader.lab` imports resolve.
$taskName = "ShectoryOptAgent"
$logFile = Join-Path $env:TEMP "shectory_opt_agent.log"
$action  = New-ScheduledTaskAction -Execute $venvPy -Argument "scripts\opt_agent.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
# Restart on failure, keep running indefinitely, allow on battery.
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
            -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

if ($Workers -gt 0) { [Environment]::SetEnvironmentVariable("OPT_AGENT_WORKERS", "$Workers", "User") }

# Autostart via Scheduled Task; if denied (policy/rights) fall back to a Startup-
# folder launcher (no admin needed). Either way the agent runs at next logon.
$autostart = "none"
try {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Shectory LAB optimization agent (param-sweep offload)" -ErrorAction Stop | Out-Null
  $autostart = "task"
  Write-Host "Registered scheduled task '$taskName' (start at logon, auto-restart)."
} catch {
  Write-Host "Scheduled Task denied ($($_.Exception.Message.Trim())). Using Startup-folder autostart instead." -ForegroundColor Yellow
  $startup = [Environment]::GetFolderPath("Startup")
  $vbs = Join-Path $startup "ShectoryOptAgent.vbs"
  # VBScript launches start.cmd hidden (no console window) at logon.
  $startCmd = Join-Path $agentDir "start.cmd"
  @"
Set s = CreateObject("WScript.Shell")
s.Run "cmd /c """"$startCmd""""", 0, False
"@ | Set-Content -Path $vbs -Encoding ASCII
  $autostart = "startup-folder"
  Write-Host "Installed Startup launcher: $vbs"
}

# 5. start it now (works regardless of autostart method)
if ($autostart -eq "task") {
  Start-ScheduledTask -TaskName $taskName
} else {
  Start-Process -FilePath $venvPy -ArgumentList "scripts\opt_agent.py" -WorkingDirectory $repo -WindowStyle Hidden | Out-Null
}
Start-Sleep -Seconds 8
$running = [bool](Get-Process python -ErrorAction SilentlyContinue)
Write-Host ("Agent running: $running  (autostart=$autostart)") -ForegroundColor Green
Write-Host ""
Write-Host "Done. Log: $env:TEMP\shectory_opt_agent.log" -ForegroundColor Cyan
if ($autostart -eq "task") {
  Write-Host "Manage: Start/Stop-ScheduledTask -TaskName $taskName"
} else {
  Write-Host "Autostart at logon via Startup folder. To stop now: Get-Process python | Stop-Process"
}
