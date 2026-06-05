# install_autostart.ps1 — run the Shectory agent AUTONOMOUSLY and HIDDEN on Windows.
#
# A Scheduled Task launches agent\launch_hidden.vbs (a static, self-locating VBS) which
# starts the supervised wrapper run_agent.cmd with NO window — there is no cmd console
# to accidentally close. The wrapper handles self-update restarts (exit 42) internally.
# Falls back to a Startup-folder copy of the VBS if the task can't be registered.
#
# Run ONCE from the repo root (admin recommended for the task):
#   powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1
param(
  [string]$TaskName = "ShectoryOptAgent"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$vbs  = Join-Path $repo "agent\launch_hidden.vbs"
if (-not (Test-Path $vbs)) { throw "launch_hidden.vbs not found at $vbs (re-pull it from github)" }
$startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "ShectoryOptAgent.vbs"
Write-Host "repo: $repo"
Write-Host "hidden launcher: $vbs"

# stop any current instance / task / startup launcher first (avoid double-launch)
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
try { Remove-Item $startupVbs -Force -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*\.venv\*" } | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name wscript -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}

$installed = $false
try {
  $action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"{0}"' -f $vbs) -WorkingDirectory $repo
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 9999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $set -Principal $principal -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "OK: Scheduled Task '$TaskName' registered + started - HIDDEN (no cmd window)."
  $installed = $true
} catch {
  Write-Warning ("Scheduled Task registration failed (" + $_.Exception.Message + "). Falling back to Startup folder.")
}

if (-not $installed) {
  Copy-Item -Path $vbs -Destination $startupVbs -Force
  Write-Host "OK: Startup launcher copied to $startupVbs (runs hidden at every logon)."
  Start-Process -FilePath "wscript.exe" -ArgumentList ('"{0}"' -f $vbs) -WorkingDirectory $repo | Out-Null
  Write-Host "Started now (hidden)."
}

Write-Host ""
Write-Host "No window to keep open. Watch status on the site (Botstore -> agent panel),"
Write-Host ("or the log:  Get-Content `"" + $repo + "\agent\agent.log`" -Wait -Tail 20")
Write-Host "Stop:        Stop-ScheduledTask -TaskName $TaskName"
