# Почта окна — запуск из PowerShell. Оператор работает в нём, а не в Git Bash.
#
#   .\scripts\fedwindow.ps1
#
# Делает то же, что и одноимённый .sh: держит ssh-туннель до Lineman (машина
# окон НЕ в WireGuard, канон федерации §1.1 предписывает jump через shevbo-pi)
# и крутит опрос ящика этого окна на переднем плане.
#
# Терминал не отпускается намеренно: невидимый фоновый цикл, который «вроде
# работает», — ровно та болезнь, от которой мы лечились весь день. Пока окно
# открыто, видно, живой цикл или упал.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$port = if ($env:STL_FED_PORT) { $env:STL_FED_PORT } else { '9090' }
$jump = if ($env:STL_FED_JUMP) { $env:STL_FED_JUMP } else { 'shevbo-pi' }
$ping = "http://127.0.0.1:$port/api/agent/ping/inbox?since=0"

function Test-Tunnel {
    try { Invoke-WebRequest -Uri $ping -TimeoutSec 4 -UseBasicParsing | Out-Null; $true }
    catch { $false }
}

if (-not (Test-Tunnel)) {
    Write-Host "поднимаю туннель через $jump…"
    # Своё окно процессу не даём: туннель должен жить молча рядом.
    Start-Process ssh -WindowStyle Hidden -ArgumentList @(
        '-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30',
        '-N','-L',"${port}:10.66.0.1:9090",$jump)
    foreach ($i in 1..12) { Start-Sleep -Seconds 1; if (Test-Tunnel) { break } }
}
if (-not (Test-Tunnel)) {
    Write-Host "туннель не поднялся: проверь 'ssh $jump true'" -ForegroundColor Red
    exit 1
}

Set-Location $repo
# Кто мы: переменная либо личность папки (CLAUDE.local.md). Не опознались —
# останавливаемся, а не гадаем: чужой ящик хуже пустого.
$win = & python -c "import sys; sys.path.insert(0,'scripts'); import fedwindow; print(fedwindow.window_id())"
if (-not $win) {
    Write-Host "не опознал окно: нужен STL_WINDOW или CLAUDE.local.md" -ForegroundColor Red
    exit 2
}

Write-Host "окно $win · туннель через $jump · опрос пошёл (Ctrl+C чтобы остановить)"
$env:LINEMAN_URL = "http://127.0.0.1:$port"
& python scripts/fedwindow.py loop $win
