# Verify baseline

Regression anchor: what "green" means in this repo. Re-run after changes; a failure you
did NOT introduce is inherited-red (not a stop), but never leave the base redder.

Recorded 2026-07-02 (commit 0004fbc):

| Command | Result |
|---|---|
| `python -m ruff check trader/ tests/` | 0 errors — "All checks passed!" |
| `FINAM_SECRET_TOKEN=dummy python -m pytest -m "not integration" -q` | 330 passed, 9 deselected, 1 warning (~41s) |
| `cd frontend && node ./node_modules/vitest/vitest.mjs run` | 24 passed (5 files) |
| `cd quik_agent && go test ./...` | green on the hoster (no local Go toolchain); `trade` pkg is the hot path |

Notes:
- Integration tests (`-m integration`, 9) need `FINAM_SECRET_TOKEN` (a real token) — not
  part of the default green baseline.
- Go tests must run on the hoster (`ssh hoster`, userland toolchain on PATH) — no Go locally.
- Prod runtime is protobuf 5.29.6: regen Python stubs only with `grpcio-tools<1.71`.
