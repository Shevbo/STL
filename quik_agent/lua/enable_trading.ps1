# Arm the agent for QUIK trading: set quik_trading_enabled=true in agent_config.json.
# Deliberate, human action. The agent still places NOTHING without an explicit,
# confirmed order from STL, and the hard limits (whitelist/qty/collar/daily/kill) apply.
# Run via enable_trading.bat, then restart the agent.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$cfg  = 'C:\distr\dist\agent_config.json'

if (-not (Test-Path $cfg)) {
    Write-Host ('agent_config.json not found at ' + $cfg)
    exit 1
}
$c = Get-Content -Raw -Encoding UTF8 $cfg | ConvertFrom-Json
$c | Add-Member -NotePropertyName quik_trading_enabled -NotePropertyValue $true -Force
[IO.File]::WriteAllText($cfg, ($c | ConvertTo-Json -Depth 20), $utf8)

Write-Host 'Agent TRADING ENABLED in config (quik_trading_enabled=true).'
Write-Host 'Restart the agent now: Ctrl+C in its window, then run quik-agent_amd64.exe'
Write-Host 'After restart the console should show: trade: bridge :50063 enabled=true'
