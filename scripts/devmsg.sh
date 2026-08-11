#!/usr/bin/env bash
# Обёртка над scripts/devmsg.py: подтягивает окружение STL и его venv, чтобы
# вызов из чужого окна был одной строкой без знания путей.
#   ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh inbox real-trade'
set -euo pipefail
cd "$HOME/apps/shectory-trader"
set -a; . "$HOME/.shectory_trade.env"; set +a
# Путь к venv КЭШИРУЕМ: `poetry env info --path` стоит ~900 мс, а почту теперь
# дёргает хук на каждое сообщение оператора — почти секунда на ровном месте.
# Кэш самолечится: пропал интерпретатор — спрашиваем poetry заново.
CACHE="$HOME/.stl-devmsg-venv"
PY=""
[ -s "$CACHE" ] && PY="$(cat "$CACHE")"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  PY="$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python"
  printf '%s' "$PY" > "$CACHE"
fi
PYTHONPATH="$HOME/apps/shectory-trader" exec "$PY" scripts/devmsg.py "$@"
