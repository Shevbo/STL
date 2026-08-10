#!/usr/bin/env bash
# Обёртка над scripts/devmsg.py: подтягивает окружение STL и его venv, чтобы
# вызов из чужого окна был одной строкой без знания путей.
#   ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh inbox real-trade'
set -euo pipefail
cd "$HOME/apps/shectory-trader"
set -a; . "$HOME/.shectory_trade.env"; set +a
PY="$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python"
PYTHONPATH="$HOME/apps/shectory-trader" exec "$PY" scripts/devmsg.py "$@"
