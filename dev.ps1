<#
.SYNOPSIS
  Single dev runner for Shectory Trade & Lab (QUIK agent + STL).
  Frees you from remembering codegen / build / test / lint details.

.USAGE
  ./dev.ps1 <verb>

  Verbs:
    gen        codegen proto -> Go stubs + Python stubs
    gen-go     codegen proto -> quik_agent/internal/pb (package quikv1)
    gen-py     codegen proto -> trader/quik/pb
    build      build agent exe (amd64 + 386) into quik_agent/dist
    tidy       go mod tidy in quik_agent
    test       go test + python pytest (not integration)
    test-go    go test ./... in quik_agent
    test-py    pytest -m "not integration"
    lint       gofmt+vet + ruff
    check      report which toolchain pieces are installed (no install)
    clean      remove generated stubs + built exes
    all        gen -> tidy -> build -> test -> lint
    help       this text

  Tools are NOT auto-installed. Missing ones print an exact install hint.
#>
[CmdletBinding()]
param([Parameter(Position = 0)][string]$Verb = 'help')

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Proto = 'shectory/quik/v1/quik_agent.proto'

function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Need($name, $hint) {
  if (-not (Have $name)) {
    Write-Host "[dev] missing tool: $name" -ForegroundColor Red
    Write-Host "      install: $hint" -ForegroundColor Yellow
    exit 1
  }
}

function Run($block) { & $block; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }

function BuildRev {
  if (Have git) {
    $r = (& git -C $Root rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $r) { return $r.Trim() }
  }
  return 'dev'
}

function Gen-Go {
  Need protoc 'https://github.com/protocolbuffers/protobuf/releases (add protoc to PATH)'
  Need protoc-gen-go 'go install google.golang.org/protobuf/cmd/protoc-gen-go@latest'
  Need protoc-gen-go-grpc 'go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest'
  Push-Location (Join-Path $Root 'quik_agent')
  try {
    if (-not (Test-Path 'internal/pb')) { New-Item -ItemType Directory -Path 'internal/pb' | Out-Null }
    $map = "Mshectory/quik/v1/quik_agent.proto=shectory/quik_agent/internal/pb"
    Run { protoc -I ../proto `
        --go_out=. --go_opt=module=shectory/quik_agent --go_opt=$map `
        --go-grpc_out=. --go-grpc_opt=module=shectory/quik_agent --go-grpc_opt=$map --go-grpc_opt=require_unimplemented_servers=false `
        "../proto/$Proto" }
    Write-Host "[dev] go stubs -> quik_agent/internal/pb" -ForegroundColor Green
  } finally { Pop-Location }
}

function Gen-Py {
  Need python 'https://www.python.org/downloads/'
  $hasTools = $false
  & python -c "import grpc_tools" 2>$null; if ($LASTEXITCODE -eq 0) { $hasTools = $true }
  if (-not $hasTools) {
    Write-Host "[dev] grpcio-tools not installed" -ForegroundColor Red
    Write-Host "      install: python -m pip install grpcio-tools" -ForegroundColor Yellow
    exit 1
  }
  $out = Join-Path $Root 'trader/quik/pb'
  if (-not (Test-Path $out)) { New-Item -ItemType Directory -Path $out | Out-Null }
  Push-Location $Root
  try {
    Run { python -m grpc_tools.protoc -Iproto --python_out=trader/quik/pb --grpc_python_out=trader/quik/pb "proto/$Proto" }
    Write-Host "[dev] python stubs -> trader/quik/pb" -ForegroundColor Green
  } finally { Pop-Location }
}

function Tidy {
  Need go 'https://go.dev/dl/ (add go to PATH)'
  Push-Location (Join-Path $Root 'quik_agent')
  try { Run { go mod tidy } } finally { Pop-Location }
}

function Build {
  Need go 'https://go.dev/dl/ (add go to PATH)'
  $rev = BuildRev
  Push-Location (Join-Path $Root 'quik_agent')
  try {
    if (-not (Test-Path 'dist')) { New-Item -ItemType Directory -Path 'dist' | Out-Null }
    $ld = "-X main.agentBuildRevStr=$rev"
    $env:GOOS = 'windows'
    $env:GOARCH = 'amd64'; Run { go build -ldflags $ld -o dist/quik-agent_amd64.exe ./cmd/quik-agent }
    $env:GOARCH = '386';   Run { go build -ldflags $ld -o dist/quik-agent.exe ./cmd/quik-agent }
    Remove-Item Env:GOOS, Env:GOARCH -ErrorAction SilentlyContinue
    Write-Host "[dev] built quik_agent/dist (rev $rev)" -ForegroundColor Green
  } finally { Pop-Location }
}

function Test-Go {
  Need go 'https://go.dev/dl/ (add go to PATH)'
  Push-Location (Join-Path $Root 'quik_agent')
  try { Run { go test ./... } } finally { Pop-Location }
}

function Test-Py {
  Need python 'https://www.python.org/downloads/'
  if (-not $env:FINAM_SECRET_TOKEN) { $env:FINAM_SECRET_TOKEN = 'dummy' }
  Push-Location $Root
  try { Run { python -m pytest -m "not integration" -q } } finally { Pop-Location }
}

function Lint {
  Push-Location $Root
  try {
    Run { python -m ruff check trader/ tests/ }
    if (Have go) {
      Push-Location 'quik_agent'
      try {
        $bad = & gofmt -l .
        if ($bad) { Write-Host "[dev] gofmt needs: $bad" -ForegroundColor Red; exit 1 }
        Run { go vet ./... }
      } finally { Pop-Location }
    } else {
      Write-Host "[dev] go not installed -> skipped gofmt/vet (python lint done)" -ForegroundColor Yellow
    }
  } finally { Pop-Location }
}

function Check {
  $tools = @(
    @{n = 'python'; h = 'https://www.python.org/downloads/' },
    @{n = 'ruff'; h = 'python -m pip install ruff' },
    @{n = 'go'; h = 'https://go.dev/dl/' },
    @{n = 'protoc'; h = 'https://github.com/protocolbuffers/protobuf/releases' },
    @{n = 'protoc-gen-go'; h = 'go install google.golang.org/protobuf/cmd/protoc-gen-go@latest' },
    @{n = 'protoc-gen-go-grpc'; h = 'go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest' },
    @{n = 'git'; h = 'https://git-scm.com/downloads' }
  )
  foreach ($t in $tools) {
    if (Have $t.n) { Write-Host ("  OK   {0}" -f $t.n) -ForegroundColor Green }
    else { Write-Host ("  MISS {0}  -> {1}" -f $t.n, $t.h) -ForegroundColor Yellow }
  }
  $py = $false; & python -c "import grpc_tools" 2>$null; if ($LASTEXITCODE -eq 0) { $py = $true }
  if ($py) { Write-Host "  OK   grpcio-tools" -ForegroundColor Green }
  else { Write-Host "  MISS grpcio-tools  -> python -m pip install grpcio-tools" -ForegroundColor Yellow }
}

function Clean {
  Remove-Item -Recurse -Force (Join-Path $Root 'quik_agent/dist') -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force (Join-Path $Root 'quik_agent/internal/pb') -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force (Join-Path $Root 'trader/quik/pb/shectory') -ErrorAction SilentlyContinue
  Write-Host "[dev] cleaned generated stubs + dist" -ForegroundColor Green
}

switch ($Verb.ToLower()) {
  'gen' { Gen-Go; Gen-Py }
  'gen-go' { Gen-Go }
  'gen-py' { Gen-Py }
  'build' { Build }
  'tidy' { Tidy }
  'test' { Test-Go; Test-Py }
  'test-go' { Test-Go }
  'test-py' { Test-Py }
  'lint' { Lint }
  'check' { Check }
  'clean' { Clean }
  'all' { Gen-Go; Gen-Py; Tidy; Build; Test-Go; Test-Py; Lint }
  default { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host }
}
