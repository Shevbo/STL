#requires -Version 5.1
<#
.SYNOPSIS
  Загрузка минутных котировок фьючерсов ММВБ (ISS) и дополнение локальных .txt архивов (формат как docs/MVID.txt).

.DESCRIPTION
  Источник данных: публичный ISS MOEX (см. docs/iss_simple_client.py, iss_simple_main.py и PDF в docs/).
  Для каждого кода из CLIST (= SECTYPE на FORTS) перебираются серии вида <SECTYPE><буква месяца><цифра года%10>
  (например MXH6, MXM6).   Для каждой серии строится «окно жизни»: если ISS отдаёт LASTTRADEDATE/LASTDELDATE — начало берётся как более раннее из
  (начало квартала экспирации, месяц экспирации минус 10 месяцев), конец = последний торг; иначе эвристика без ISS.
  Год кандидата должен совпадать с годом LASTDELDATE при наличии метаданных (защита суффикса/десятилетия).
  Год десятилетия подбирается пробой по ISS: для каждого совпадения окна с запрошенным периодом проверяется
  наличие минутных свечей; затем по календарю вычисляются непрерывные сегменты, где данная серия — фронт, и ISS вызывается
  только по этим сегментам (без скачивания «перехлёста», когда серия не фронт).
  Для каждой календарной даты выбирается «фронт» — серия с минимальной датой экспирации (LastDel) среди ещё торгуемых
  в эту дату (TradeFrom..LastTrade); в файл попадают только свечи той серии, которая совпала с фронтом (без дублей задних месяцев).
  Если ISS не отдаёт строку инструмента (делист), LastTrade/LastDel для выбора фронта берутся по фактической последней дате
  минутных свечей в пересечении с запросом, а не по концу календарного месяца экспирации.
  Итог дописывается в QUOTATIONS_FOLDER\<код>.txt в формате загрузки как в эталоне docs/MVID.txt:
  строка заголовка …,<VOL>,<SECNAME> и далее TICKER,PER,YYYYMMDD,HHMMSS,OHLC,VOL,код серии ISS (SECID), например MXM6.

  Настройки: файл SU_settings (KEY=VALUE) в той же папке, что и этот скрипт; при отсутствии — значения по умолчанию из спринта.

  Важно: файл скрипта должен быть сохранён как UTF-8 с BOM. Иначе Windows PowerShell 5.1 читает его в системной ANSI-кодировке и кириллица ломает разбор (ParserError).

  В консоли отключён индикатор прогресса веб-запросов ($ProgressPreference), вместо него выводятся обычные строки Write-Host по крупным шагам.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\Source_update.ps1
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# Иначе Invoke-WebRequest кратко показывает жёлтую строку прогресса — визуально «мигает» и нечитаемо.
$script:_suSavedProgressPreference = $ProgressPreference
$ProgressPreference = "SilentlyContinue"

#region Defaults (Спринт 4)
$script:QUOTATIONS_FOLDER = ".\Котировки"
$script:ENGINE = "futures"
$script:MARKET = "forts"
$script:CLIST = "MX, MM, RI, GZ, SR, LK, TT, NV, GK, YD, TB, PX"
$script:DURATION = "1MIN"
$script:PERIOD_DAYS = 365
$script:DEBUG_MODE = 1
# Буквы месяцев поставки по классификации срочного рынка (F=янв … Z=дек).
$script:FutMonthLetters = @('F', 'G', 'H', 'J', 'K', 'M', 'N', 'Q', 'U', 'V', 'X', 'Z')
#endregion

function Get-ScriptRoot {
  if ($PSScriptRoot) { return $PSScriptRoot }
  return (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

function Get-MoscowTimeZone {
  foreach ($id in @("Russian Standard Time", "Europe/Moscow")) {
    try { return [System.TimeZoneInfo]::FindSystemTimeZoneById($id) } catch { }
  }
  return [System.TimeZoneInfo]::CreateCustomTimeZone(
    "MSK", [TimeSpan]::FromHours(3), "MSK", "MSK"
  )
}

function Get-MoscowCalendarDate {
  param([datetime]$UtcNow = [datetime]::UtcNow)
  $tz = Get-MoscowTimeZone
  $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($UtcNow, $tz)
  return $local.Date
}

function Read-SUSettingsFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $name = $line.Substring(0, $eq).Trim()
    $value = $line.Substring($eq + 1).Trim()
    if (-not $name) { return }
    Set-Variable -Name $name -Value $value -Scope Script
  }
}

function ConvertTo-IntervalMinutes {
  param([string]$Duration)
  switch ($Duration.ToUpperInvariant()) {
    "1MIN" { return 1 }
    "10MIN" { return 10 }
    "1H" { return 60 }
    "1D" { return 24 }
    default {
      throw "Неподдерживаемый DURATION='$Duration'. Задайте 1MIN, 10MIN, 1H или 1D."
    }
  }
}

function ConvertFrom-JsonCompat {
  param([string]$Text)
  if ($PSVersionTable.PSVersion.Major -ge 6) {
    return ($Text | ConvertFrom-Json -Depth 40)
  }
  Add-Type -AssemblyName System.Web.Extensions
  $ser = New-Object System.Web.Script.Serialization.JavaScriptSerializer
  $ser.MaxJsonLength = 200000000
  return $ser.DeserializeObject($Text)
}

function Invoke-IssGet {
  param([string]$Uri)
  $prev = [System.Net.ServicePointManager]::SecurityProtocol
  try {
    [System.Net.ServicePointManager]::SecurityProtocol =
      [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.ServicePointManager]::SecurityProtocol
  } catch { }
  try {
    $iwrParams = @{
      Uri             = $Uri
      UseBasicParsing = $true
      Headers         = @{
        "Accept"     = "application/json"
        "User-Agent" = "PiranhaAI-SourceUpdate/1.0"
      }
    }
    if ($PSVersionTable.PSVersion.Major -ge 7) {
      $iwrParams["ProgressAction"] = "SilentlyContinue"
    }
    $resp = Invoke-WebRequest @iwrParams
    return ([string]$resp.Content)
  } finally {
    [System.Net.ServicePointManager]::SecurityProtocol = $prev
  }
}

function Get-FuturesDeliveryMonth {
  param([string]$Letter)
  $ch = $Letter.ToString().ToUpperInvariant()
  switch ($ch) {
    'F' { return 1 }
    'G' { return 2 }
    'H' { return 3 }
    'J' { return 4 }
    'K' { return 5 }
    'M' { return 6 }
    'N' { return 7 }
    'Q' { return 8 }
    'U' { return 9 }
    'V' { return 10 }
    'X' { return 11 }
    'Z' { return 12 }
    default { return 0 }
  }
}

function Get-QuarterFirstDayOfMonth {
  param(
    [int]$Year,
    [int]$Month
  )
  if ($Month -lt 1 -or $Month -gt 12) { return $null }
  $q0 = [int][math]::Floor(($Month - 1) / 3)
  $m = 1 + 3 * $q0
  return (Get-Date -Year $Year -Month $m -Day 1 -Hour 0 -Minute 0 -Second 0 -Millisecond 0).Date
}

function Get-MonthLastDayDate {
  param(
    [int]$Year,
    [int]$Month
  )
  $ld = [DateTime]::DaysInMonth($Year, $Month)
  return (Get-Date -Year $Year -Month $Month -Day $ld -Hour 0 -Minute 0 -Second 0 -Millisecond 0).Date
}

function Max-DateOnly {
  param([datetime]$A, [datetime]$B)
  if ($A -ge $B) { return $A.Date } else { return $B.Date }
}

function Min-DateOnly {
  param([datetime]$A, [datetime]$B)
  if ($A -le $B) { return $A.Date } else { return $B.Date }
}

function Test-IssCandlesNonEmpty {
  param(
    [string]$Engine,
    [string]$Market,
    [string]$SecId,
    [int]$Interval,
    [string]$FromDate,
    [string]$TillDate
  )
  try {
    $url = "https://iss.moex.com/iss/engines/$Engine/markets/$Market/securities/$SecId/candles.json" +
    "?iss.meta=off&interval=$Interval&from=$FromDate&till=$TillDate&start=0"
    $jsonText = Invoke-IssGet -Uri $url
    $j = ConvertFrom-JsonCompat -Text $jsonText
    $data = $j.candles.data
    return ($null -ne $data -and $data.Count -gt 0)
  } catch {
    return $false
  }
}

function Get-FortsSecurityMeta {
  param(
    [string]$Engine,
    [string]$Market,
    [string]$SecId
  )
  try {
    $url = "https://iss.moex.com/iss/engines/$Engine/markets/$Market/securities/$SecId.json" +
    "?iss.meta=off&iss.only=securities&securities.columns=LASTTRADEDATE,LASTDELDATE,SHORTNAME,SECNAME"
    $jsonText = Invoke-IssGet -Uri $url
    $j = ConvertFrom-JsonCompat -Text $jsonText
    $data = $j.securities.data
    if (-not $data -or $data.Count -eq 0) { return $null }
    $cols = [string[]]@($j.securities.columns)
    $row = $data[0]
    $map = @{}
    for ($i = 0; $i -lt $cols.Length; $i++) {
      $map[$cols[$i]] = $row[$i]
    }
    $lts = [string]$map['LASTTRADEDATE']
    $lds = [string]$map['LASTDELDATE']
    if (-not $lts -or -not $lds) { return $null }
    $inv = [System.Globalization.CultureInfo]::InvariantCulture
    $lt = [datetime]::ParseExact($lts, "yyyy-MM-dd", $inv).Date
    $ld = [datetime]::ParseExact($lds, "yyyy-MM-dd", $inv).Date
    $shortN = $map['SHORTNAME']
    $secN = $map['SECNAME']
    return @{
      LastTrade = $lt
      LastDel   = $ld
      ShortName = $(if ($null -ne $shortN) { [string]$shortN } else { "" })
      SecName   = $(if ($null -ne $secN) { [string]$secN } else { "" })
    }
  } catch {
    return $null
  }
}

function Get-ContractWindowForYear {
  param(
    [int]$YearGuess,
    [int]$DeliveryMonth,
    [string]$SecId,
    [string]$Engine,
    [string]$Market,
    $Meta
  )
  $delFirst = (Get-Date -Year $YearGuess -Month $DeliveryMonth -Day 1 -Hour 0 -Minute 0 -Second 0 -Millisecond 0).Date
  $quarterStart = Get-QuarterFirstDayOfMonth -Year $YearGuess -Month $DeliveryMonth
  if ($null -eq $quarterStart) { return $null }
  $extBack = $delFirst.AddMonths(-10)
  $extBack = (Get-Date -Year $extBack.Year -Month $extBack.Month -Day 1 -Hour 0 -Minute 0 -Second 0 -Millisecond 0).Date
  $activeFrom = (Min-DateOnly -A $quarterStart -B $extBack)
  $activeTo = Get-MonthLastDayDate -Year $YearGuess -Month $DeliveryMonth

  if ($null -ne $Meta) {
    if ($Meta.LastDel.Year -ne $YearGuess) {
      return $null
    }
    $ld = $Meta.LastDel.Date
    $activeTo = $Meta.LastTrade.Date
    $ldFirst = (Get-Date -Year $ld.Year -Month $ld.Month -Day 1 -Hour 0 -Minute 0 -Second 0 -Millisecond 0).Date
    $extFromDel = $ldFirst.AddMonths(-10)
    $extFromDel = (Get-Date -Year $extFromDel.Year -Month $extFromDel.Month -Day 1 -Hour 0 -Minute 0 -Second 0 -Millisecond 0).Date
    $qLd = Get-QuarterFirstDayOfMonth -Year $ld.Year -Month $ld.Month
    if ($null -eq $qLd) { return $null }
    $activeFrom = Min-DateOnly -A (Min-DateOnly -A $activeFrom -B $extFromDel) -B $qLd
  }
  return @{ From = $activeFrom; To = $activeTo }
}

function Get-IssLastCandleCalendarDateInRange {
  param(
    [string]$Engine,
    [string]$Market,
    [string]$SecId,
    [int]$Interval,
    [datetime]$RngFrom,
    [datetime]$RngTill
  )
  $lo = $RngFrom.Date
  $hi = $RngTill.Date
  if ($lo -gt $hi) { return $null }
  $loS = $lo.ToString("yyyy-MM-dd")
  $hiS = $hi.ToString("yyyy-MM-dd")
  if (-not (Test-IssCandlesNonEmpty -Engine $Engine -Market $Market -SecId $SecId -Interval $Interval -FromDate $loS -TillDate $hiS)) {
    return $null
  }
  while ($lo -lt $hi) {
    $span = [int](($hi - $lo).TotalDays)
    $step = [math]::Max(1, [int][math]::Floor(($span + 1) / 2))
    $mid = $lo.AddDays($step)
    $midS = $mid.ToString("yyyy-MM-dd")
    if (Test-IssCandlesNonEmpty -Engine $Engine -Market $Market -SecId $SecId -Interval $Interval -FromDate $midS -TillDate $hiS) {
      $lo = $mid
    } else {
      $hi = $mid.AddDays(-1)
    }
  }
  return $lo
}

function Test-IssCandlesNonEmptyMultiProbe {
  param(
    [string]$Engine,
    [string]$Market,
    [string]$SecId,
    [int]$Interval,
    [datetime]$RngFrom,
    [datetime]$RngTill
  )
  if ($RngFrom -gt $RngTill) { return $false }
  $probes = New-Object System.Collections.Generic.List[datetime]
  [void]$probes.Add($RngFrom)
  [void]$probes.Add($RngTill)
  $span = [int](($RngTill - $RngFrom).TotalDays)
  if ($span -gt 0) {
    foreach ($frac in @(0.25, 0.5, 0.75)) {
      $off = [int][math]::Floor($span * $frac)
      $d = $RngFrom.AddDays($off)
      if ($d -lt $RngFrom) { $d = $RngFrom }
      if ($d -gt $RngTill) { $d = $RngTill }
      [void]$probes.Add($d)
    }
  }
  foreach ($p in ($probes | Select-Object -Unique)) {
    $ps = $p.ToString("yyyy-MM-dd")
    if (Test-IssCandlesNonEmpty -Engine $Engine -Market $Market -SecId $SecId -Interval $Interval -FromDate $ps -TillDate $ps) {
      return $true
    }
  }
  return $false
}

function Get-QuotationsFilePath {
  param([string]$Root, [string]$Folder)
  $safe = ($Root.Trim())
  return (Join-Path $Folder ($safe + ".txt"))
}

function Get-FirstLine {
  param([string]$Path)
  $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
  $enc = [System.Text.UTF8Encoding]::new($false)
  try {
    $sr = New-Object System.IO.StreamReader($fs, $enc, $true)
    return $sr.ReadLine()
  } finally {
    $fs.Dispose()
  }
}

function Get-LastDataBegin {
  param([string]$Path, [string]$HeaderLine)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  $tail = Get-Content -LiteralPath $Path -Tail 120 -Encoding UTF8 -ErrorAction Stop
  $lastBegin = $null
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  foreach ($line in $tail) {
    if (-not $line) { continue }
    if ($line -eq $HeaderLine) { continue }
    if ($line -match '^\s*<TICKER>') { continue }
    $parts = $line -split ","
    $got = $false
    # Finam-строка: TICKER,PER,YYYYMMDD,HHMMSS,OHLC,VOL[,<SECNAME>] — имя может содержать запятые в кавычках
    if ($line -match '^([^,]+),(\d+),(\d{8}),(\d{6}),') {
      $ds = $Matches[3]
      $ts = $Matches[4].PadLeft(6, [char]'0')
      try {
        $lastBegin = [datetime]::ParseExact($ds + $ts, "yyyyMMddHHmmss", $inv)
        $got = $true
      } catch { }
    }
    if (-not $got -and $parts.Count -ge 8) {
      $b = $parts[6].Trim()
      if ($b -match '^\d{4}-\d{2}-\d{2} ') {
        try {
          $lastBegin = [datetime]::ParseExact($b, "yyyy-MM-dd HH:mm:ss", $inv)
        } catch { }
      }
    }
  }
  return $lastBegin
}

function Get-CandlesAll {
  param(
    [string]$Engine,
    [string]$Market,
    [string]$SecId,
    [int]$Interval,
    [string]$FromDate,
    [string]$TillDate
  )
  $rows = New-Object System.Collections.Generic.List[object]
  $start = 0
  $pageSize = 500
  while ($true) {
    $url = "https://iss.moex.com/iss/engines/$Engine/markets/$Market/securities/$SecId/candles.json" +
    "?iss.meta=off&interval=$Interval&from=$FromDate&till=$TillDate&start=$start"
    $jsonText = Invoke-IssGet -Uri $url
    $j = ConvertFrom-JsonCompat -Text $jsonText
    $data = $j.candles.data
    if (-not $data) { break }
    $cnt = 0
    foreach ($row in $data) {
      $cnt++
      $rows.Add($row) | Out-Null
    }
    if ($cnt -lt $pageSize) { break }
    $start += $cnt
  }
  return $rows
}

function Get-RowCell {
  param(
    $Row,
    [int]$Index
  )
  if ($null -eq $Row) { return $null }
  if ($Row -is [System.Collections.IList]) { return $Row[$Index] }
  return ($Row | Select-Object -Index $Index)
}

function Escape-CsvField {
  param([string]$Text)
  if ($null -eq $Text) { return "" }
  if ($Text -match '["\r\n,]') {
    return '"' + ($Text -replace '"', '""') + '"'
  }
  return $Text
}

function Convert-CandleRowToFinamLine {
  param(
    $Row,
    [string]$Ticker,
    [int]$Per,
    [string]$DisplayName
  )
  $open = Get-RowCell -Row $Row -Index 0
  $close = Get-RowCell -Row $Row -Index 1
  $high = Get-RowCell -Row $Row -Index 2
  $low = Get-RowCell -Row $Row -Index 3
  $vol = Get-RowCell -Row $Row -Index 5
  $beginRaw = [string](Get-RowCell -Row $Row -Index 6)
  $dt = [datetime]::ParseExact($beginRaw, "yyyy-MM-dd HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture)
  $dateStr = $dt.ToString("yyyyMMdd")
  $timeStr = $dt.ToString("HHmmss")
  $tail = Escape-CsvField -Text $DisplayName
  return ("{0},{1},{2},{3},{4},{5},{6},{7},{8},{9}" -f $Ticker, $Per, $dateStr, $timeStr, $open, $high, $low, $close, $vol, $tail)
}

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

function New-SeriesPlan {
  param(
    [string]$SecId,
    [datetime]$TradeFrom,
    [datetime]$TradeTo,
    [datetime]$LastDel,
    [datetime]$LastTrade,
    [datetime]$FetchFrom,
    [datetime]$FetchTill
  )
  return [pscustomobject]@{
    SecId      = $SecId
    TradeFrom  = $TradeFrom.Date
    TradeTo    = $TradeTo.Date
    LastDel    = $LastDel.Date
    LastTrade  = $LastTrade.Date
    FetchFrom  = $FetchFrom.Date
    FetchTill  = $FetchTill.Date
  }
}

function Get-PrimarySeriesPlanForDate {
  param(
    [datetime]$CalendarDay,
    [System.Collections.Generic.List[object]]$Plans
  )
  $d = $CalendarDay.Date
  $cands = New-Object System.Collections.Generic.List[object]
  foreach ($p in $Plans) {
    if ($p.TradeFrom -le $d -and $p.LastTrade -ge $d) {
      [void]$cands.Add($p)
    }
  }
  if ($cands.Count -eq 0) { return $null }
  $sorted = @($cands | Sort-Object { $_.LastDel })
  return $sorted[0]
}

function Build-PrimaryPlanByDay {
  param(
    [datetime]$RangeStart,
    [datetime]$RangeEnd,
    [System.Collections.Generic.List[object]]$Plans
  )
  $map = @{}
  for ($t = $RangeStart.Date; $t -le $RangeEnd.Date; $t = $t.AddDays(1)) {
    $pk = Get-PrimarySeriesPlanForDate -CalendarDay $t -Plans $Plans
    $map[$t.ToString("yyyy-MM-dd")] = $pk
  }
  return $map
}

function Get-FrontFetchSegmentsForPlan {
  param(
    $Plan,
    [hashtable]$PrimaryByDay
  )
  $segments = [System.Collections.Generic.List[object]]::new()
  $d = $Plan.FetchFrom.Date
  $till = $Plan.FetchTill.Date
  $curStart = $null
  while ($d -le $till) {
    $key = $d.ToString("yyyy-MM-dd")
    $prim = $PrimaryByDay[$key]
    $match = ($null -ne $prim -and $prim.SecId -eq $Plan.SecId)
    if ($match) {
      if ($null -eq $curStart) { $curStart = $d }
    } else {
      if ($null -ne $curStart) {
        [void]$segments.Add([pscustomobject]@{ From = $curStart; Till = $d.AddDays(-1) })
        $curStart = $null
      }
    }
    $d = $d.AddDays(1)
  }
  if ($null -ne $curStart) {
    [void]$segments.Add([pscustomobject]@{ From = $curStart; Till = $till })
  }
  return $segments
}

#region Entry
$rootDir = Get-ScriptRoot
$settingsPath = Join-Path $rootDir "SU_settings"
Read-SUSettingsFile -Path $settingsPath

$QUOTATIONS_FOLDER = [Environment]::ExpandEnvironmentVariables($QUOTATIONS_FOLDER)
if (-not [System.IO.Path]::IsPathRooted($QUOTATIONS_FOLDER)) {
  $QUOTATIONS_FOLDER = Join-Path (Get-Location) $QUOTATIONS_FOLDER
}
$logsFolder = Join-Path (Get-Location) "logs"
Ensure-Directory -Path $QUOTATIONS_FOLDER
Ensure-Directory -Path $logsFolder

$interval = ConvertTo-IntervalMinutes -Duration $script:DURATION
[int]$periodDays = 0
[int]::TryParse([string]$script:PERIOD_DAYS, [ref]$periodDays) | Out-Null
if ($periodDays -le 0) { $periodDays = 365 }

[int]$dbg = 0
[int]::TryParse([string]$script:DEBUG_MODE, [ref]$dbg) | Out-Null
$debugOn = ($dbg -ne 0)

$logPath = $null
if ($debugOn) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmm"
  $logPath = Join-Path $logsFolder ("SU_{0}.log" -f $stamp)
  "=== Source_update {0} (МСК ориентир дат) ===" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss") | Add-Content -LiteralPath $logPath -Encoding UTF8
}

function Write-DebugLog {
  param([string]$Message)
  if (-not $debugOn) { return }
  $Message | Add-Content -LiteralPath $logPath -Encoding UTF8
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()

$mscToday = Get-MoscowCalendarDate
$yesterday = $mscToday.AddDays(-1)

$contracts = @(
  ($script:CLIST -split ",") |
  ForEach-Object { $_.Trim() } |
  Where-Object { $_ }
)

if ($script:ENGINE.ToLowerInvariant() -ne "futures") {
  throw "В этой версии поддержан только ENGINE=futures (получено: $script:ENGINE)."
}

Write-DebugLog ("ENGINE={0} MARKET={1} DURATION={2} PERIOD_DAYS={3} QUOTATIONS_FOLDER={4}" -f `
    $script:ENGINE, $script:MARKET, $script:DURATION, $periodDays, $QUOTATIONS_FOLDER)
Write-DebugLog ("Сегодня(МСК, дата)={0} Вчера(МСК)={1}" -f $mscToday.ToString("yyyy-MM-dd"), $yesterday.ToString("yyyy-MM-dd"))

$utf8Bom = New-Object System.Text.UTF8Encoding $true
$header = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<SECNAME>"

foreach ($code in $contracts) {
  $histPath = Get-QuotationsFilePath -Root $code -Folder $QUOTATIONS_FOLDER
  $defaultStart = $mscToday.AddDays(-$periodDays)
  $reqStart = $defaultStart

  if (Test-Path -LiteralPath $histPath) {
    $hdr = Get-FirstLine -Path $histPath
    $lastBegin = Get-LastDataBegin -Path $histPath -HeaderLine $hdr
    if ($null -ne $lastBegin) {
      $reqStart = $lastBegin.Date.AddDays(1)
    }
    Write-DebugLog ("[{0}] Файл найден: {1}. Последняя begin={2} -> дата_начала={3}" -f `
        $code, $histPath, $lastBegin, $reqStart.ToString("yyyy-MM-dd"))
  } else {
    Write-DebugLog ("[{0}] Файл не найден. дата_начала по PERIOD_DAYS={1}" -f $code, $reqStart.ToString("yyyy-MM-dd"))
  }

  $reqEnd = $yesterday
  $fromStr = $reqStart.ToString("yyyy-MM-dd")
  $tillStr = $reqEnd.ToString("yyyy-MM-dd")

  if ($reqStart -gt $reqEnd) {
    Write-Host ("По корню {0} новых дней нет (дата_начала {1} позже вчера {2}). Файл {3}" -f `
        $code, $fromStr, $tillStr, (Split-Path -Leaf $histPath))
    Write-DebugLog ("[{0}] Пропуск: start>end ({1} > {2})" -f $code, $fromStr, $tillStr)
    continue
  }

  $minYear = [math]::Max(1, $reqStart.Year - 11)
  $maxYear = $reqEnd.Year + 1
  $totalLines = 0
  $seriesTouched = New-Object System.Collections.Generic.List[string]
  $seriesPlans = New-Object System.Collections.Generic.List[object]

  foreach ($L in $script:FutMonthLetters) {
    $delM = Get-FuturesDeliveryMonth -Letter $L
    if ($delM -le 0) { continue }
    for ($d = 0; $d -le 9; $d++) {
      $secId = "$code$L$d"
      $resolved = $false
      $metaOnce = Get-FortsSecurityMeta -Engine $script:ENGINE -Market $script:MARKET -SecId $secId
      $years = [System.Collections.Generic.List[int]]::new()
      for ($yy = $maxYear; $yy -ge $minYear; $yy--) {
        if (($yy % 10) -eq $d) { [void]$years.Add($yy) }
      }
      foreach ($y in $years) {
        $win = Get-ContractWindowForYear -YearGuess $y -DeliveryMonth $delM -SecId $secId `
          -Engine $script:ENGINE -Market $script:MARKET -Meta $metaOnce
        if ($null -eq $win) { continue }
        $tradeFrom = $win.From.Date
        $tradeTo = $win.To.Date
        if ($null -ne $metaOnce) {
          $ld = $metaOnce.LastDel.Date
          $ltr = $metaOnce.LastTrade.Date
        } else {
          $ld = Get-MonthLastDayDate -Year $y -Month $delM
          $ltr = $ld
          # Полное окно серии, не только пересечение с запросом — иначе при узком rng LastTrade занижается.
          $lastBar = Get-IssLastCandleCalendarDateInRange -Engine $script:ENGINE -Market $script:MARKET -SecId $secId `
            -Interval $interval -RngFrom $win.From -RngTill $win.To
          if ($null -ne $lastBar) {
            $ltr = $lastBar
            $ld = $lastBar
          }
        }
        $rngFrom = Max-DateOnly -A $reqStart -B $win.From
        $rngTill = Min-DateOnly -A $reqEnd -B $win.To
        if ($rngFrom -gt $rngTill) { continue }

        if (-not (Test-IssCandlesNonEmptyMultiProbe -Engine $script:ENGINE -Market $script:MARKET -SecId $secId `
              -Interval $interval -RngFrom $rngFrom -RngTill $rngTill)) {
          continue
        }

        $plan = New-SeriesPlan -SecId $secId `
          -TradeFrom $tradeFrom -TradeTo $tradeTo -LastDel $ld -LastTrade $ltr `
          -FetchFrom $rngFrom -FetchTill $rngTill
        [void]$seriesPlans.Add($plan)
        Write-DebugLog ("[{0}] план {1} y={2} торги {3:yyyy-MM-dd}..{4:yyyy-MM-dd} fetch {5:yyyy-MM-dd}..{6:yyyy-MM-dd} LastDel={7:yyyy-MM-dd}" -f `
            $code, $secId, $y, $tradeFrom, $ltr, $rngFrom, $rngTill, $ld)
        $resolved = $true
        break
      }
      if (-not $resolved) {
        Write-DebugLog ("[{0}] серия {1}: нет данных ISS в пересечении с [{2}..{3}]" -f $code, $secId, $fromStr, $tillStr)
      }
    }
  }

  Write-DebugLog ("[{0}] серий в реестре: {1}" -f $code, $seriesPlans.Count)

  if ($seriesPlans.Count -eq 0) {
    Write-Host ("По корню {0} за период с {1} по {2} новых свечей нет (нет серий ISS). Файл: {3}" -f `
        $code, $fromStr, $tillStr, (Split-Path -Leaf $histPath))
    continue
  }

  $primaryByDay = Build-PrimaryPlanByDay -RangeStart $reqStart -RangeEnd $reqEnd -Plans $seriesPlans

  $rollBatch = New-Object System.Collections.Generic.List[object]
  foreach ($plan in $seriesPlans) {
    $secId = $plan.SecId
    # @(...) — один сегмент иначе «разворачивается» в PSCustomObject без .Count (StrictMode).
    $segments = @(Get-FrontFetchSegmentsForPlan -Plan $plan -PrimaryByDay $primaryByDay)
    if ($segments.Count -eq 0) {
      Write-DebugLog ("[{0}] {1}: нет дней, где серия — фронт в [{2:yyyy-MM-dd}..{3:yyyy-MM-dd}]; ISS не вызывается" -f `
          $code, $secId, $plan.FetchFrom, $plan.FetchTill)
      continue
    }
    $naiveDays = [int](($plan.FetchTill - $plan.FetchFrom).TotalDays) + 1
    $frontDays = 0
    foreach ($sg in $segments) {
      $frontDays += [int](($sg.Till - $sg.From).TotalDays) + 1
    }
    Write-DebugLog ("[{0}] {1}: сегментов фронта {2}, календарных дней фронта {3} из {4} (полное пересечение с запросом)" -f `
        $code, $secId, $segments.Count, $frontDays, $naiveDays)
    Write-Host ("  ISS: свечи {0}  только фронт: {1} сегм., {2} к.дн. из {3} в пересечении с запросом" -f `
        $secId, $segments.Count, $frontDays, $naiveDays) -ForegroundColor DarkCyan

    $segIx = 0
    foreach ($seg in $segments) {
      $segIx++
      $fs = $seg.From.ToString("yyyy-MM-dd")
      $ts = $seg.Till.ToString("yyyy-MM-dd")
      Write-DebugLog ("[{0}] загрузка {1} сегмент {2}/{3} {4}..{5}" -f $code, $secId, $segIx, $segments.Count, $fs, $ts)
      try {
        $candles = Get-CandlesAll -Engine $script:ENGINE -Market $script:MARKET -SecId $secId `
          -Interval $interval -FromDate $fs -TillDate $ts
      } catch {
        Write-Warning ("Ошибка ISS для {0} ({1} {2}…{3}): {4}" -f $code, $secId, $fs, $ts, $_.Exception.Message)
        Write-DebugLog ("[{0}] Исключение {1} {2}..{3}: {4}" -f $code, $secId, $fs, $ts, $_.Exception.Message)
        continue
      }
      Write-Host ("  ISS: свечи {0}  сегм. {1}/{2}  {3} … {4}  строк: {5}" -f $secId, $segIx, $segments.Count, $fs, $ts, $candles.Count) -ForegroundColor DarkGreen

      foreach ($row in $candles) {
        $beginRaw = [string](Get-RowCell -Row $row -Index 6)
        try {
          $dt = [datetime]::ParseExact($beginRaw, "yyyy-MM-dd HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture)
        } catch {
          continue
        }
        if ($dt.Date -lt $seg.From) { continue }
        if ($dt.Date -gt $seg.Till) { continue }
        $dayKey = $dt.Date.ToString("yyyy-MM-dd")
        $prim = $primaryByDay[$dayKey]
        if ($null -eq $prim) { continue }
        if ($prim.SecId -ne $plan.SecId) { continue }
        $lineStr = Convert-CandleRowToFinamLine -Row $row -Ticker $code -Per $interval -DisplayName $plan.SecId
        [void]$rollBatch.Add([pscustomobject]@{ T = $dt; Line = $lineStr; SecId = $secId })
      }
    }
  }

  if ($rollBatch.Count -gt 0) {
    $sorted = @($rollBatch | Sort-Object { $_.T })
    $linesOut = New-Object System.Collections.Generic.List[string]
    foreach ($x in $sorted) {
      [void]$linesOut.Add($x.Line)
      if (-not $seriesTouched.Contains($x.SecId)) { [void]$seriesTouched.Add($x.SecId) }
    }
    $isNew = -not (Test-Path -LiteralPath $histPath)
    if ($isNew) {
      [System.IO.File]::WriteAllLines($histPath, @($header) + $linesOut, $utf8Bom)
    } else {
      [System.IO.File]::AppendAllLines($histPath, $linesOut, $utf8Bom)
    }
    $totalLines += $linesOut.Count
  }

  Write-DebugLog ("[{0}] всего строк добавлено: {1}; серии (данные в файл): {2}" -f $code, $totalLines, ($seriesTouched -join ", "))

  if ($totalLines -gt 0) {
    Write-Host ("По корню {0} за период с {1} по {2} добавлено строк: {3}. Файл {4}. Серии: {5}" -f `
        $code, $fromStr, $tillStr, $totalLines, (Split-Path -Leaf $histPath), ($seriesTouched -join ", "))
  } else {
    Write-Host ("По корню {0} за период с {1} по {2} новых свечей нет. Файл: {3}" -f `
        $code, $fromStr, $tillStr, (Split-Path -Leaf $histPath))
  }
}

$sw.Stop()
$h = [int]$sw.Elapsed.Hours
$m = [int]$sw.Elapsed.Minutes
$s = [int]$sw.Elapsed.Seconds
$names = ($contracts -join ", ")
Write-Host ("Данные по контрактам {0} загружены. Продолжительность выполнения задания {1} ч {2} мин {3} с" -f $names, $h, $m, $s)
Write-DebugLog ("Готово за {0} ч {1} мин {2} с" -f $h, $m, $s)
if ($null -ne $script:_suSavedProgressPreference) {
  $ProgressPreference = $script:_suSavedProgressPreference
}
#endregion
