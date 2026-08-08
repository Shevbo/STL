#!/usr/bin/env bash
# Build a new QUIK agent release and publish it to STL's release dir, so connected
# agents self-update (on start / on COMMAND_TYPE_SELF_UPDATE). Run ON THE HOSTER.
#
# build_rev = unix epoch (monotonic). Builds from ~/quik_build/quik_agent (keep it
# synced with the repo). Publishes <arch>.rev + <arch>.zip into the STL release dir.
# Pass an agent_id to also trigger an immediate self-update on that live agent.
#
#   bash deploy/publish_quik_agent.sh --runner-sha <sha256> [AGENT_ID]
#
# ЗАМОК (регламент трёх окон, 08.08.2026): если в dist/ staged robot-runner.exe,
# публикация требует --runner-sha с sha256 ТВОЕЙ локальной сборки и сверяет его
# со staged-файлом. Три окна Claude пишут в ~/quik_build параллельно; 08.08 чужая
# перезаливка staged-бинарника уехала на боевой VDS. Не совпало — не стартуем.
set -euo pipefail

export PATH="$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH"
SRC="$HOME/quik_build/quik_agent"
REL="$HOME/apps/shectory-trader/agent_release"
ENVF="$HOME/.shectory_trade.env"
REV="$(date +%s)"

AGENT_ID=""
RUNNER_SHA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --runner-sha)   RUNNER_SHA="${2:-}"; shift 2 ;;
    --runner-sha=*) RUNNER_SHA="${1#*=}"; shift ;;
    *)              AGENT_ID="$1"; shift ;;
  esac
done

# ── Замок 1: staged runner обязан совпасть с тем, что публикующий собрал сам ──
RUNNER="$SRC/dist/robot-runner.exe"
if [ -f "$RUNNER" ]; then
  STAGED_SHA="$(sha256sum "$RUNNER" | cut -d' ' -f1)"
  echo "[publish] staged runner: sha256=$STAGED_SHA"
  echo "[publish] staged: $(stat -c 'mtime %y, %s bytes, owner %U' "$RUNNER")"
  if [ -z "$RUNNER_SHA" ]; then
    echo "[publish] СТОП: в dist/ лежит robot-runner.exe, а --runner-sha не передан." >&2
    echo "[publish] Подтверди, ЧЕЙ бинарник уезжает на боевой VDS:" >&2
    echo "[publish]   bash publish_quik_agent.sh --runner-sha <sha256 локальной сборки> [AGENT_ID]" >&2
    exit 2
  fi
  if [ "$STAGED_SHA" != "$RUNNER_SHA" ]; then
    echo "[publish] СТОП: staged runner НЕ ТОТ. Ожидали  $RUNNER_SHA" >&2
    echo "[publish]                            в dist/  $STAGED_SHA" >&2
    echo "[publish] Кто-то перезаписал staged-файл после твоей заливки (см. mtime выше)." >&2
    echo "[publish] Перезалей свою сборку и повтори." >&2
    exit 3
  fi
  echo "[publish] runner sha OK"
elif [ -n "$RUNNER_SHA" ]; then
  echo "[publish] СТОП: --runner-sha передан, а dist/robot-runner.exe не staged." >&2
  exit 2
fi

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
# Ship the current QLua feed/trade script too, so a self-update delivers it next
# to the agent (<exeDir>\lua\); the operator then only Stop/Starts it in QUIK
# instead of hand-copying the file (stale in-memory Lua has bitten us — brokerref).
cp -f lua/shectory_trade.lua dist/ 2>/dev/null || echo '[publish] note: lua/shectory_trade.lua missing - shipping without it'
cd dist
python3 - <<'PYZ'
import os, zipfile
def zwrite(zname, names):
    z = zipfile.ZipFile(zname, 'w', zipfile.ZIP_DEFLATED)
    for n in names:
        z.write(n)
    z.close()
extra = [n for n in ('robot-runner.exe', 'strategies_doc.json', 'shectory_trade.lua') if os.path.exists(n)]
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
