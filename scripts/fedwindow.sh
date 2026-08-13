#!/usr/bin/env bash
# Почта окна. Запускается в ОТДЕЛЬНОМ терминале того окна и работает там.
#
#   bash scripts/fedwindow.sh
#
# ПОЧЕМУ НЕ ФОНОМ. Первая версия уводила цикл в nohup и nohup же её и подвёл:
# фоновый процесс в Git Bash держит канал вызывающей оболочки, и запуск выглядел
# как зависание. Отвязать надёжно на Windows не вышло, а невидимый демон, который
# «вроде работает», — ровно та болезнь, от которой мы весь день лечились.
# Терминал на переднем плане честнее: видно, живой цикл или упал.
#
# ПОЧЕМУ ТУННЕЛЬ. Машина окон НЕ в WireGuard, 10.66.0.1 оттуда недоступен.
# Канон федерации §1.1 предписывает для Windows ssh-jump через shevbo-pi.
# Проверяем туннель ФАКТОМ ответа, а не наличием процесса: живой ssh с мёртвым
# каналом дал бы «почты нет» вместо ошибки, а это хуже отказа.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${STL_FED_PORT:-9090}"
JUMP="${STL_FED_JUMP:-shevbo-pi}"
PING="http://127.0.0.1:${PORT}/api/agent/ping/inbox?since=0"

alive() { curl -s -m 4 -o /dev/null "$PING"; }

if ! alive; then
  echo "поднимаю туннель через $JUMP…"
  ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -N -L "${PORT}:10.66.0.1:9090" "$JUMP" >/dev/null 2>&1 &
  for _ in $(seq 1 12); do sleep 1; alive && break; done
fi
alive || { echo "туннель не поднялся: проверь 'ssh $JUMP true'"; exit 1; }

cd "$REPO" || exit 1
WIN="$(STL_FED_ID="${STL_FED_ID:-${STL_WINDOW:-}}" python -c \
  "import sys; sys.path.insert(0,'scripts'); import fedwindow; print(fedwindow.window_id())" 2>/dev/null)"
[ -n "$WIN" ] || { echo "не опознал окно: нужен STL_WINDOW или CLAUDE.local.md"; exit 2; }

echo "окно $WIN · туннель через $JUMP · опрос пошёл (Ctrl+C чтобы остановить)"
LINEMAN_URL="http://127.0.0.1:${PORT}" exec python scripts/fedwindow.py loop "$WIN"
