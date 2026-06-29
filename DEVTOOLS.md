# Devtools — single runner

One entry point for codegen, build, test, lint. No need to remember details.

- Windows (PowerShell, primary): `./dev.ps1 <verb>`
- Linux / hoster / CI / Git-Bash: `make <verb>`

Both run the same commands with identical codegen flags.

## Verbs

| Verb | Does |
|---|---|
| `gen` | codegen proto -> Go stubs (`quik_agent/internal/pb`) + Python stubs (`trader/quik/pb`) |
| `gen-go` / `gen-py` | only one side |
| `tidy` | `go mod tidy` in `quik_agent` |
| `build` | agent exe amd64 + 386 -> `quik_agent/dist` (build rev from git) |
| `test` | `go test ./...` + `pytest -m "not integration"` |
| `test-go` / `test-py` | only one side |
| `lint` | ruff (Python) + gofmt + go vet (Go) |
| `check` | report which toolchain pieces are installed |
| `clean` | remove generated stubs + built exes |
| `all` | gen -> tidy -> build -> test -> lint |
| `help` | usage |

## Notes

- Tools are NOT auto-installed (by choice). A missing tool prints the exact install command and stops.
- `test-py` sets `FINAM_SECRET_TOKEN=dummy` if unset, so unit tests need no real credentials.
- Go verbs (`gen-go`, `tidy`, `build`, `test-go`, `go vet`) need Go + protoc on PATH. Run `./dev.ps1 check`
  to see what is missing. On this dev box only python + ruff are present; the Go side builds on the Windows
  target or CI.
- `clean` removes generated stubs (`internal/pb`, `trader/quik/pb/shectory`). Re-run `gen` to recreate them.
