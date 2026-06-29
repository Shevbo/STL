@echo off
rem ============================================================================
rem  Codegen for the QUIK agent gRPC stubs (Windows).
rem
rem  Generates Go from ../proto/shectory/quik/v1/quik_agent.proto into
rem  internal\pb as package `quikv1`, importable as
rem  `shectory/quik_agent/internal/pb`.
rem
rem  The proto declares `option go_package = "shectory/quik/v1;quikv1"`. The
rem  -M mapping overrides that import path to shectory/quik_agent/internal/pb,
rem  and --go_opt=module=shectory/quik_agent strips the module prefix so the
rem  files land FLAT in internal\pb (quik_agent.pb.go, quik_agent_grpc.pb.go).
rem
rem  Requirements on PATH:
rem    protoc            (Protocol Buffers compiler)
rem    protoc-gen-go        (go install google.golang.org/protobuf/cmd/protoc-gen-go@latest)
rem    protoc-gen-go-grpc   (go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest)
rem
rem  Run from the quik_agent\ directory:  gen.bat
rem ============================================================================
setlocal

set PROTO_ROOT=..\proto
set PROTO_FILE=shectory/quik/v1/quik_agent.proto
set MAP=Mshectory/quik/v1/quik_agent.proto=shectory/quik_agent/internal/pb
set MODULE=module=shectory/quik_agent

if not exist internal\pb mkdir internal\pb

protoc ^
  -I "%PROTO_ROOT%" ^
  --go_out=. --go_opt=%MODULE% --go_opt=%MAP% ^
  --go-grpc_out=. --go-grpc_opt=%MODULE% --go-grpc_opt=%MAP% --go-grpc_opt=require_unimplemented_servers=false ^
  "%PROTO_ROOT%/%PROTO_FILE%"

if errorlevel 1 (
  echo.
  echo codegen FAILED. Check protoc / protoc-gen-go / protoc-gen-go-grpc are on PATH.
  exit /b 1
)

echo.
echo codegen OK -^> internal\pb\quik_agent.pb.go, internal\pb\quik_agent_grpc.pb.go
endlocal
