#!/usr/bin/env bash
# Build a new QUIK agent release and publish it to STL's release dir, so connected
# agents self-update (on start / on COMMAND_TYPE_SELF_UPDATE). Run ON THE HOSTER.
#
# build_rev = unix epoch (monotonic). Builds from ~/quik_build/quik_agent (keep it
# synced with the repo). Publishes <arch>.rev + <arch>.zip into the STL release dir.
# Pass an agent_id to also trigger an immediate self-update on that live agent.
#
#   bash deploy/publish_quik_agent.sh [AGENT_ID]
set -euo pipefail

export PATH="$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH"
SRC="$HOME/quik_build/quik_agent"
REL="$HOME/apps/shectory-trader/agent_release"
ENVF="$HOME/.shectory_trade.env"
REV="$(date +%s)"
AGENT_ID="${1:-}"

cd "$SRC"
echo "[publish] build_rev=$REV"
mkdir -p internal/pb dist "$REL"
protoc -I ../proto \
  --go_out=. --go_opt=module=shectory/quik_agent --go_opt=Mshectory/quik/v1/quik_agent.proto=shectory/quik_agent/internal/pb \
  --go-grpc_out=. --go-grpc_opt=module=shectory/quik_agent --go-grpc_opt=Mshectory/quik/v1/quik_agent.proto=shectory/quik_agent/internal/pb --go-grpc_opt=require_unimplemented_servers=false \
  ../proto/shectory/quik/v1/quik_agent.proto
go mod tidy >/dev/null 2>&1 || true

CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -ldflags "-X main.agentBuildRevStr=$REV" -o dist/quik-agent_amd64.exe ./cmd/quik-agent
CGO_ENABLED=0 GOOS=windows GOARCH=386   go build -ldflags "-X main.agentBuildRevStr=$REV" -o dist/quik-agent.exe ./cmd/quik-agent

# Flat zips: the exe sits at the archive root under its run name. When a bundled
# robot-runner.exe (+ its strategies_doc.json, Task 10) has been staged into dist/
# (built on Windows via deploy/build_runner.sh and uploaded here), ship them in the
# SAME zip so the agent's self-update delivers the runner and its docs together
# (zero-touch satellite).
cd dist
python3 - <<'PYZ'
import os, zipfile
def zwrite(zname, names):
    z = zipfile.ZipFile(zname, 'w', zipfile.ZIP_DEFLATED)
    for n in names:
        z.write(n)
    z.close()
extra = [n for n in ('robot-runner.exe', 'strategies_doc.json') if os.path.exists(n)]
if 'robot-runner.exe' not in extra:
    print('[publish] note: dist/robot-runner.exe not staged - shipping agent only')
elif 'strategies_doc.json' not in extra:
    print('[publish] note: dist/strategies_doc.json not staged - runner will ship without strategy docs')
zwrite('amd64.zip', ['quik-agent_amd64.exe'] + extra)
zwrite('386.zip', ['quik-agent.exe'] + extra)
PYZ
cd ..

cp dist/amd64.zip "$REL/amd64.zip"; printf '%s' "$REV" > "$REL/amd64.rev"
cp dist/386.zip   "$REL/386.zip";   printf '%s' "$REV" > "$REL/386.rev"
echo "[publish] published to $REL (amd64 + 386), rev $REV"

if [ -n "$AGENT_ID" ]; then
  set -a; . "$ENVF"; set +a
  TOKEN="$(cd "$HOME/apps/shectory-trader" && "$HOME/.local/bin/poetry" run python -c "from trader.auth.portal import make_session_token; import os; print(make_session_token('ops@stl', os.environ['SHECTORY_AUTH_BRIDGE_SECRET']))" 2>/dev/null)"
  echo "[publish] triggering self-update on agent '$AGENT_ID'..."
  curl -s -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/v1/quik/agent/$AGENT_ID/self-update"; echo
fi
