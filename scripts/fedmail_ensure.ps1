# Иммунитет почты к перезагрузке. Одно задание планировщика на всю машину.
#
# Поднимает то, чего не хватает, и молчит про то, что уже работает:
#   1) ssh-туннель до Lineman (машина окон НЕ в WireGuard, канон §1.1 — jump
#      через shevbo-pi);
#   2) по циклу опроса на каждое окно.
#
# Идемпотентно: запускается каждые 5 минут и при входе в систему. Умер цикл,
# оборвался туннель, перезагрузилась машина — следующий прогон поднимет заново.
# Стеречь pid-файлами не нужно и вредно: файл переживает процесс и врёт.
#
# Окно определяется ПАПКОЙ (CLAUDE.local.md), поэтому каждый цикл знает, чей
# ящик читает, и три окна на одной машине не путаются.
#
# pythonw, а НЕ python: консольный интерпретатор поднимает видимое окно на
# каждый запуск. Оператор поймал это в первый же час.

$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\Dev\Shectory Trade & Lab\AI_STL_Developers'
$port = 9090
$jump = 'shevbo-pi'
$ping = "http://127.0.0.1:$port/api/agent/ping/inbox?since=0"

function Test-Tunnel {
    try { Invoke-WebRequest -Uri $ping -TimeoutSec 4 -UseBasicParsing | Out-Null; $true }
    catch { $false }
}

# --- 1. Туннель. Судим по ФАКТУ ответа, а не по наличию процесса: живой ssh с
# мёртвым каналом даёт «почты нет» вместо ошибки, и это хуже отказа.
if (-not (Test-Tunnel)) {
    Get-Process ssh -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*-L ${port}:10.66.0.1:9090*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Process ssh -WindowStyle Hidden -ArgumentList @(
        '-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
        '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
        '-N','-L',"${port}:10.66.0.1:9090",$jump)
    foreach ($i in 1..12) { Start-Sleep -Seconds 1; if (Test-Tunnel) { break } }
}
if (-not (Test-Tunnel)) { exit 1 }   # jump лежит — молчим, следующий прогон повторит

# --- 2. Циклы окон. Один на папку, опознание по её личности.
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command python).Source }

$running = @(Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
             Select-Object -ExpandProperty CommandLine)

foreach ($dir in @('real-trade','backtests','ui-ux')) {
    $repo = Join-Path $root $dir
    if (-not (Test-Path (Join-Path $repo 'scripts\fedwindow.py'))) { continue }
    $win = & python -c "import sys; sys.path.insert(0,'$($repo -replace '\\','/')/scripts'); import fedwindow; print(fedwindow.window_id())" 2>$null
    if (-not $win) { $win = "stl-$dir" }
    $env:LINEMAN_URL = "http://127.0.0.1:$port"
    if (-not ($running -match [regex]::Escape("fedwindow.py loop $win"))) {
        Start-Process -FilePath $pyw -WindowStyle Hidden -WorkingDirectory $repo `
            -ArgumentList @('scripts\fedwindow.py','loop',$win)
    }
    # --- 3. Автоответчик. Цикл выше кладёт письмо в файл, но печатает его хук, а
    # хук ждёт, пока в окне напечатает ЧЕЛОВЕК. Замер 13.08: доставка 1.5-2 с,
    # ответ не пришёл ни от одного окна за 2.5 минуты и за ночь тоже. Отвечает
    # поэтому процесс: claude -p из папки окна, инструменты только на чтение.
    if (-not (Test-Path (Join-Path $repo 'scripts\fedbot.py'))) { continue }
    if ($running -match [regex]::Escape("fedbot.py $win")) { continue }
    Start-Process -FilePath $pyw -WindowStyle Hidden -WorkingDirectory $repo `
        -ArgumentList @('scripts\fedbot.py',$win)
}
