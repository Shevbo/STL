# Set the QUIK agent instrument whitelist (the agent-side trading gate). Must MATCH the
# STL whitelist. Edit $WL below to your exact QUIK contract codes (case-sensitive), then
# run set_whitelist.bat and restart the agent.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$cfg  = 'C:\distr\dist\agent_config.json'

# Liquid September (U6) FORTS futures for the multi-instrument UI test. CONFIRM each code
# against your QUIK terminal and adjust (e.g. GZ could be GZU6; Si=SiU6; SR=SRU6).
$WL = @('RIU6', 'GZU6', 'SiU6', 'SRU6')

if (-not (Test-Path $cfg)) {
    Write-Host ('agent_config.json not found at ' + $cfg)
    exit 1
}
$c = Get-Content -Raw -Encoding UTF8 $cfg | ConvertFrom-Json
$c | Add-Member -NotePropertyName instrument_whitelist -NotePropertyValue $WL -Force
[IO.File]::WriteAllText($cfg, ($c | ConvertTo-Json -Depth 20), $utf8)

Write-Host ('Agent whitelist set to: ' + ($WL -join ', '))
Write-Host 'Restart the agent (Ctrl+C, then quik-agent_amd64.exe). Must match STL whitelist.'
