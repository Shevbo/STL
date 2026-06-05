#!/usr/bin/env bash
# Continuous adaptive-optimizer supervisor.
#
# Runs adaptive optimizer campaigns BACK-TO-BACK (rotating random seed each time, so
# every campaign explores fresh points in the parameter space) — keeps the botstore
# discovering good robots without manual relaunch.
#
# Safe by construction: only ever ONE campaign at a time. It waits while another
# optimize_adaptive.py is running OR any opt- job is queued/running, so it never piles
# load onto the i9/VDS. Compute is on the i9 agent (or the throttled VDS fallback).
#
# Run on the VDS:
#   nohup bash scripts/optimize_loop.sh >> /tmp/opt_loop.log 2>&1 &
# Watch:  tail -f /tmp/opt_loop.log
# Stop:   pkill -f optimize_loop.sh    (a campaign already running finishes on its own)
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$PWD"
set -a; . "$HOME/.shectory_trade.env" 2>/dev/null || true; set +a

LOG="${OPT_LOOP_LOG:-/tmp/opt_loop.log}"
INSTRUMENTS="${OPT_LOOP_INSTRUMENTS:-15}"
EXPLORE="${OPT_LOOP_EXPLORE:-300}"
ROUNDS="${OPT_LOOP_ROUNDS:-2}"
seed=$(( (RANDOM << 15) ^ RANDOM ))

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }
log "optimize_loop started (instruments=$INSTRUMENTS explore=$EXPLORE rounds=$ROUNDS)"

while true; do
  orch=$(pgrep -fc 'optimize_adaptive.py' 2>/dev/null || echo 0)
  active=$(sudo -u postgres psql project_stl -t -A -c \
    "SELECT count(*) FROM backtest_runs WHERE id LIKE 'opt-%' AND status IN ('queued','running')" \
    2>/dev/null || echo 0)
  if [ "${orch:-0}" -gt 0 ] || [ "${active:-0}" -gt 0 ]; then
    sleep 120
    continue
  fi
  seed=$((seed + 1))
  log "launch adaptive campaign seed=$seed"
  poetry run python scripts/optimize_adaptive.py \
    --instruments "$INSTRUMENTS" --explore "$EXPLORE" --rounds "$ROUNDS" --seed "$seed" >> "$LOG" 2>&1
  log "campaign finished (seed=$seed)"
  sleep 30
done
