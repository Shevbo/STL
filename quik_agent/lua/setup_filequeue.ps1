# Shectory QUIK file-queue setup (no LuaSocket). Run via setup_filequeue.bat.
# ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 in the ANSI codepage.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)   # UTF-8, no BOM

$bridge = 'C:\quik-bridge'
$cfg    = 'C:\distr\dist\agent_config.json'
$lua    = Join-Path $PSScriptRoot 'shectory_trade.lua'

Write-Host '=== Shectory QUIK file-queue setup ==='

if (-not (Test-Path $bridge)) {
    New-Item -ItemType Directory -Path $bridge | Out-Null
    Write-Host ('Created ' + $bridge)
} else {
    Write-Host ($bridge + ' already exists')
}

# 1) Agent config: add trade_queue_dir, keep existing fields, write without BOM.
if (Test-Path $cfg) {
    $c = Get-Content -Raw -Encoding UTF8 $cfg | ConvertFrom-Json
    $c | Add-Member -NotePropertyName trade_queue_dir -NotePropertyValue $bridge -Force
    [IO.File]::WriteAllText($cfg, ($c | ConvertTo-Json -Depth 20), $utf8)
    Write-Host ('Patched agent config: trade_queue_dir=' + $bridge)
} else {
    Write-Host ('agent_config.json not found at ' + $cfg + ' - add trade_queue_dir manually')
}

# 2) Lua: enable file-queue + QUEUE_DIR, and optionally the trade ACCOUNT.
if (Test-Path $lua) {
    $t = Get-Content -Raw -Encoding UTF8 $lua
    $t = $t -replace 'USE_FILE_QUEUE\s*=\s*false', 'USE_FILE_QUEUE = true'
    $t = $t -replace 'QUEUE_DIR\s*=\s*""', 'QUEUE_DIR      = "C:\\quik-bridge"'
    $acct = Read-Host 'FORTS trade account ACCOUNT (e.g. SPBFUT00XXX), Enter to skip'
    if ($acct) {
        $t = $t -replace 'ACCOUNT\s*=\s*""', ('ACCOUNT       = "' + $acct + '"')
        Write-Host ('ACCOUNT set to ' + $acct)
    }
    [IO.File]::WriteAllText($lua, $t, $utf8)
    Write-Host ('Patched Lua: file-queue on, QUEUE_DIR=' + $bridge)
} else {
    Write-Host 'shectory_trade.lua not next to this script - set USE_FILE_QUEUE=true and QUEUE_DIR=C:\quik-bridge manually'
}

Write-Host ''
Write-Host 'NEXT:'
Write-Host '  1) Restart the agent: Ctrl+C in its window, then run quik-agent_amd64.exe'
Write-Host '  2) Restart the shectory_trade Lua script in QUIK (stop then start)'
Write-Host ''
Write-Host 'Expected: agent console shows file-queue mode; QUIK shows transport=file-queue'
