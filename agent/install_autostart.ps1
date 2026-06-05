# install_autostart.ps1 — run the Shectory agent AUTONOMOUSLY on this Windows host.
#
# Registers a Scheduled Task that launches the supervised wrapper (run_agent.cmd) at
# logon, restarts it if it dies, runs headless (no terminal needed), with no time
# limit. The wrapper handles self-update restarts (exit 42) internally, so once this
# is installed the agent runs and updates itself with zero manual involvement.
# If task registration is denied (no admin), falls back to a Startup-folder launcher.
#
# Run ONCE from the repo root:
#   powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1 -AgentArgs "--insecure"
param(
  [string]$AgentArgs = "",                       # extra args passed to the agent (e.g. "--insecure")
  [string]$TaskName  = "ShectoryOptAgent"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$wrapper = Join-Path $repo "agent\run_agent.cmd"
if (-not (Test-Path $wrapper)) { throw "run_agent.cmd not found at $wrapper" }
Write-Host "repo: $repo"
Write-Host "wrapper: $wrapper  args: '$AgentArgs'"

# cmd line the task / launcher runs (headless, stays alive across self-updates)
$cmdLine = "`"$wrapper`" $AgentArgs".Trim()

# stop any current instance / task / startup launcher first (avoid double-launch)
$startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "ShectoryOptAgent.vbs"
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
try { Remove-Item $startupVbs -Force -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*\.venv\*" } | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}

$installed = $false
try {
  $action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmdLine" -WorkingDirectory $repo
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 9999 `
              -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $set `
              -Principal $principal -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "OK: Scheduled Task '$TaskName' registered + started (autonomous, auto-restart, self-updating)."
  $installed = $true
} catch {
  Write-Warning "Scheduled Task registration failed ($($_.Exception.Message)). Falling back to Startup folder."
}

if (-not $installed) {
  # Startup-folder VBS that launches the wrapper HIDDEN at logon (no admin needed).
  $vbs = $startupVbs
  $vbsBody = @"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$repo"
sh.Run "cmd /c ""$cmdLine""", 0, False
"@
  Set-Content -Path $vbs -Value $vbsBody -Encoding ASCII
  Write-Host "OK: Startup launcher written to $vbs (runs hidden at next logon)."
  # launch now too
  $sh = New-Object -ComObject WScript.Shell
  $sh.CurrentDirectory = $repo
  $sh.Run("cmd /c ""$cmdLine""", 0, $false) | Out-Null
  Write-Host "Started now (hidden)."
}

Write-Host ""
Write-Host "Watch the agent log:  Get-Content `$env:TEMP\shectory_opt_agent.log -Wait -Tail 20"
Write-Host "Stop:                 Stop-ScheduledTask -TaskName $TaskName ; or kill the python/.venv process"
