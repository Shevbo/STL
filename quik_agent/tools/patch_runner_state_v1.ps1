# patch_runner_state_v1.ps1 — backfill agent-fvg-RIU6-v2 (2026-07-13 lost fills)
#
# 2026-07-13: the runner died on every REAL fill (cp1251 log crash, fixed in
# rev 1783975589+), so 12 QUIK fills never reached the book. This script writes
# them back into runner_state.json with their exact QUIK trade prices/times and
# sets the resulting book: position SHORT 3 @ 88032.86, realized -1582.57 pts
# (computed with the FIXED partial-reduce algorithm shipped in the same release).
#
# HOW TO RUN (on the QUIK VDS, as the agent user):
#   1. STOP both processes:  taskkill /IM quik-agent-windows-amd64.exe /F
#                            taskkill /IM robot-runner.exe /F
#      (exe name may differ — check Task Manager; kill BOTH, runner too.)
#   2. powershell -ExecutionPolicy Bypass -File .\patch_runner_state_v1.ps1 `
#        -StatePath "C:\path\to\agent\robots\runner_state.json"
#   3. Start the agent again (it spawns the runner).
#
# Idempotent: refuses to run twice (checks the backfill order ids) and refuses
# to run on an unexpected book state. Makes a .bak copy before writing.

param(
    [Parameter(Mandatory = $true)][string]$StatePath
)

$RobotId       = "agent-fvg-RIU6-v2"
$WantPosition  = 1          # precondition: the frozen book we are patching
$WantAvg       = 87060.0
$WantRealized  = -7464.0    # tolerance 1.0 pt
$NewPosition   = -3
$NewAvg        = 88032.85714285714
$NewRealized   = -1582.5714285714057

$FillsJson = @'
[
 {"client_id":"backfill:1925040016067087651","order_id":"1925040016067087651","symbol":"RIU6","side":"sell","qty":1,"price":88370.0,"status":"filled","ts_ms":1783937463000},
 {"client_id":"backfill:1925040016067087654","order_id":"1925040016067087654","symbol":"RIU6","side":"sell","qty":1,"price":88370.0,"status":"filled","ts_ms":1783937463000},
 {"client_id":"backfill:1925040016067325958","order_id":"1925040016067325958","symbol":"RIU6","side":"sell","qty":1,"price":88670.0,"status":"filled","ts_ms":1783943162000},
 {"client_id":"backfill:1925040016067325971","order_id":"1925040016067325971","symbol":"RIU6","side":"sell","qty":1,"price":88670.0,"status":"filled","ts_ms":1783943162000},
 {"client_id":"backfill:1925040016067678136","order_id":"1925040016067678136","symbol":"RIU6","side":"sell","qty":1,"price":87860.0,"status":"filled","ts_ms":1783952041000},
 {"client_id":"backfill:1925040016067678137","order_id":"1925040016067678137","symbol":"RIU6","side":"sell","qty":1,"price":87860.0,"status":"filled","ts_ms":1783952041000},
 {"client_id":"backfill:1925040016067864554","order_id":"1925040016067864554","symbol":"RIU6","side":"sell","qty":1,"price":87400.0,"status":"filled","ts_ms":1783954261000},
 {"client_id":"backfill:1925040016067864564","order_id":"1925040016067864564","symbol":"RIU6","side":"sell","qty":1,"price":87400.0,"status":"filled","ts_ms":1783954261000},
 {"client_id":"backfill:1925040016067968958","order_id":"1925040016067968958","symbol":"RIU6","side":"buy","qty":1,"price":86760.0,"status":"filled","ts_ms":1783955585000},
 {"client_id":"backfill:1925040016068049528","order_id":"1925040016068049528","symbol":"RIU6","side":"buy","qty":1,"price":86870.0,"status":"filled","ts_ms":1783956963000},
 {"client_id":"backfill:1925040016068080570","order_id":"1925040016068080570","symbol":"RIU6","side":"buy","qty":1,"price":86940.0,"status":"filled","ts_ms":1783958280000},
 {"client_id":"backfill:1925040016068121396","order_id":"1925040016068121396","symbol":"RIU6","side":"buy","qty":1,"price":86990.0,"status":"filled","ts_ms":1783960567000}
]
'@

$ErrorActionPreference = "Stop"

if (-not (Test-Path $StatePath)) { Write-Error "not found: $StatePath"; exit 1 }

# Refuse to patch while the runner is alive (it would overwrite the file).
$running = Get-Process -Name "robot-runner" -ErrorAction SilentlyContinue
if ($running) { Write-Error "robot-runner.exe is RUNNING - stop agent+runner first"; exit 1 }

$raw = [System.IO.File]::ReadAllText($StatePath)
$state = $raw | ConvertFrom-Json
$robot = $state.$RobotId
if ($null -eq $robot) { Write-Error "robot '$RobotId' not in state file"; exit 1 }

# Preconditions: exactly the frozen book this patch was computed against.
if ([int]$robot.position -ne $WantPosition) {
    Write-Error ("position is {0}, expected {1} - book changed, DO NOT patch blindly" -f $robot.position, $WantPosition); exit 1 }
if ([math]::Abs([double]$robot.avg - $WantAvg) -gt 0.5) {
    Write-Error ("avg is {0}, expected {1}" -f $robot.avg, $WantAvg); exit 1 }
if ([math]::Abs([double]$robot.realized - $WantRealized) -gt 1.0) {
    Write-Error ("realized is {0}, expected ~{1}" -f $robot.realized, $WantRealized); exit 1 }

$fills = $FillsJson | ConvertFrom-Json
foreach ($f in $fills) {
    foreach ($existing in $robot.fills) {
        if ($existing.order_id -eq $f.order_id) {
            Write-Error ("order {0} already in the journal - patch already applied?" -f $f.order_id); exit 1 }
    }
}

Copy-Item $StatePath ($StatePath + ".bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

$robot.fills = @($robot.fills) + @($fills)
$robot.position = $NewPosition
$robot.avg = $NewAvg
$robot.realized = $NewRealized

# UTF-8 WITHOUT BOM: the runner reads this with a strict utf-8 codec; a
# PowerShell 5.1 Out-File BOM would make json.load fail -> state wiped to {}.
$json = $state | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($StatePath, $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ("OK: {0} fills appended; {1}: position {2} @ {3}, realized {4} pts" -f `
    $fills.Count, $RobotId, $NewPosition, $NewAvg, $NewRealized)
Write-Host "Start the agent now; check the showcase shows all 13.07 trades."
