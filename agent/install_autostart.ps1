# install_autostart.ps1 — run the Shectory agent AUTONOMOUSLY and HIDDEN on Windows.
#
# A Scheduled Task launches a VBS that starts the supervised wrapper (run_agent.cmd)
# with NO window (window style 0) — so there is no cmd console to accidentally close.
# The VBS waits on the wrapper, so the task stays "running" and restart-on-failure
# works. The wrapper handles self-update restarts (exit 42) internally. Falls back to
# a Startup-folder VBS if task registration is denied (no admin).
#
# Run ONCE from the repo root (admin recommended for the task):
#   powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1 -AgentArgs "--insecure"
param(
  [string]$AgentArgs = "",
  [string]$TaskName  = "ShectoryOptAgent"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$wrapper = Join-Path $repo "agent\run_agent.cmd"
if (-not (Test-Path $wrapper)) { throw "run_agent.cmd not found at $wrapper" }
$vbsHidden  = Join-Path $repo "agent\launch_hidden.vbs"
$startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "ShectoryOptAgent.vbs"
Write-Host "repo: $repo"
Write-Host "wrapper: $wrapper  args: '$AgentArgs'"

# Hidden launcher: runs the wrapper with window style 0 (no console). 3rd arg True =
# WAIT, so whoever runs this VBS stays alive while the agent runs (keeps the task
# 'running' for restart-on-failure). """ is a literal quote in VBS → path with "&" ok.
$hiddenBody = @"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$repo"
sh.Run """$wrapper"" $AgentArgs", 0, True
"@
Set-Content -Path $vbsHidden -Value $hiddenBody -Encoding ASCII

# stop any current instance / task / startup launcher first (avoid double-launch)
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
try { Remove-Item $startupVbs -Force -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*\.venv\*" } | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name wscript -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}

$installed = $false
try {
  # Task runs wscript on the hidden launcher → NO console window at all.
  $action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"{0}"' -f $vbsHidden) -WorkingDirectory $repo
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 9999 `
              -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $set `
              -Principal $principal -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "OK: Scheduled Task '$TaskName' registered + started — HIDDEN (no cmd window)."
  $installed = $true
} catch {
  Write-Warning "Scheduled Task registration failed ($($_.Exception.Message)). Falling back to Startup folder."
}

if (-not $installed) {
  # Startup-folder VBS (launch-and-forget, hidden) — runs at every logon, no admin.
  $startupBody = @"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$repo"
sh.Run """$wrapper"" $AgentArgs", 0, False
"@
  Set-Content -Path $startupVbs -Value $startupBody -Encoding ASCII
  Write-Host "OK: Startup launcher written to $startupVbs (runs hidden at every logon)."
  Start-Process -FilePath "wscript.exe" -ArgumentList ('"{0}"' -f $startupVbs) -WorkingDirectory $repo | Out-Null
  Write-Host "Started now (hidden)."
}

Write-Host ""
Write-Host "No window to keep open. Watch status in the site (Botstore -> agent panel),"
Write-Host "or the log:  Get-Content `"$repo\agent\agent.log`" -Wait -Tail 20"
Write-Host "Stop:        Stop-ScheduledTask -TaskName $TaskName"
