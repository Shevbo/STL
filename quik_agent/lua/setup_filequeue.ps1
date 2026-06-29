# Shectory QUIK — file-queue setup (no LuaSocket needed).
# Creates the bridge dir, points the agent config + the Lua script at it, and
# optionally writes your FORTS trade ACCOUNT into the Lua. Run via setup_filequeue.bat.
$ErrorActionPreference = 'Stop'

$bridge = 'C:\quik-bridge'
$cfg    = 'C:\distr\dist\agent_config.json'
$lua    = Join-Path $PSScriptRoot 'shectory_trade.lua'

Write-Host '=== Shectory QUIK file-queue setup ===' -ForegroundColor Cyan

if (-not (Test-Path $bridge)) {
    New-Item -ItemType Directory -Path $bridge | Out-Null
    Write-Host "Created $bridge"
} else {
    Write-Host "$bridge already exists"
}

# 1) Agent config: add trade_queue_dir, preserving existing fields. BOM-free (Go json
#    does not skip a UTF-8 BOM).
if (Test-Path $cfg) {
    $c = Get-Content -Raw $cfg | ConvertFrom-Json
    $c | Add-Member -NotePropertyName trade_queue_dir -NotePropertyValue $bridge -Force
    [IO.File]::WriteAllText($cfg, ($c | ConvertTo-Json -Depth 20))
    Write-Host "Patched agent config -> trade_queue_dir=$bridge" -ForegroundColor Green
} else {
    Write-Host "agent_config.json not at $cfg — add `"trade_queue_dir`": `"C:\\quik-bridge`" manually" -ForegroundColor Yellow
}

# 2) Lua: enable file-queue + set QUEUE_DIR, and optionally the trade ACCOUNT.
if (Test-Path $lua) {
    $t = Get-Content -Raw $lua
    $t = $t -replace 'USE_FILE_QUEUE\s*=\s*false', 'USE_FILE_QUEUE = true'
    $t = $t -replace 'QUEUE_DIR\s*=\s*""',         'QUEUE_DIR      = "C:\\quik-bridge"'

    $acct = Read-Host 'FORTS trade account ACCOUNT (e.g. SPBFUT00XXX) — Enter to skip'
    if ($acct) {
        $t = $t -replace 'ACCOUNT\s*=\s*""', ('ACCOUNT       = "' + $acct + '"')
        Write-Host "ACCOUNT set to $acct"
    }
    [IO.File]::WriteAllText($lua, $t)
    Write-Host "Patched Lua -> file-queue on, QUEUE_DIR=$bridge" -ForegroundColor Green
} else {
    Write-Host "shectory_trade.lua not next to this script — set USE_FILE_QUEUE=true, QUEUE_DIR=C:\quik-bridge in its CONFIG" -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'NEXT (do these two restarts):' -ForegroundColor Cyan
Write-Host '  1) Agent: Ctrl+C in its window, then run  quik-agent_amd64.exe'
Write-Host '  2) QUIK: stop then start the shectory_trade Lua script'
Write-Host ''
Write-Host 'Expected after restart:'
Write-Host '  agent console:  trade-bridge: ... file-queue mode, dir=C:\quik-bridge'
Write-Host '  QUIK messages:  transport=file-queue / file-queue ready: C:\quik-bridge\cmd.jsonl'
