#!/usr/bin/env bash
# Запуск почты окна: туннель до Lineman плюс цикл опроса. Одна команда на окно.
#
#   bash scripts/fedwindow.sh          поднять (идемпотентно)
#   bash scripts/fedwindow.sh stop     остановить
#
# ПОЧЕМУ ТУННЕЛЬ. Машина окон НЕ в WireGuard, и 10.66.0.1 оттуда недоступен —
# первый же прогон цикла упёрся в timeout. Канон федерации (§1.1) описывает для
# Windows ровно этот маршрут: ssh-jump через shevbo-pi, который сам в WG.
# Держим локальный port-forward, дальше клиент ходит на 127.0.0.1 и не знает
# ничего про WG.
#
# Pi — единая точка отказа (канон предупреждает прямым текстом). Поэтому:
# упал туннель — цикл не молотит в петле, он спит и пробует снова, а окно видит
# тишину, а не выдуманную пустоту почты.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${STL_FED_PORT:-9090}"
JUMP="${STL_FED_JUMP:-shevbo-pi}"

if [ "${1:-start}" = "stop" ]; then
  pkill -f "ssh.*-L ${PORT}:10.66.0.1:9090" 2>/dev/null
  pkill -f "fedwindow[.]py loop" 2>/dev/null
  echo "остановлено"
  exit 0
fi

# 1) Туннель. Проверяем не по процессу, а по ФАКТУ ответа: живой процесс с
#    мёртвым каналом — обычное дело, и именно он даёт «почты нет» вместо ошибки.
if ! curl -s -m 5 -o /dev/null "http://127.0.0.1:${PORT}/api/agent/ping/inbox?since=0"; then
  ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -N -L "${PORT}:10.66.0.1:9090" "$JUMP" &
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    curl -s -m 3 -o /dev/null "http://127.0.0.1:${PORT}/api/agent/ping/inbox?since=0" && break
  done
fi
if ! curl -s -m 5 -o /dev/null "http://127.0.0.1:${PORT}/api/agent/ping/inbox?since=0"; then
  echo "туннель не поднялся: проверь ssh $JUMP"; exit 1
fi

# 2) Цикл. Один на окно; повторный запуск не плодит второй.
if pgrep -f "fedwindow[.]py loop" >/dev/null 2>&1; then
  echo "цикл уже работает"
else
  ( cd "$REPO" && LINEMAN_URL="http://127.0.0.1:${PORT}" \
      nohup python scripts/fedwindow.py loop >> "$HOME/.stl-fedmail.out" 2>&1 & )
  sleep 2
fi
echo "готово: туннель через $JUMP, цикл опроса поднят"
