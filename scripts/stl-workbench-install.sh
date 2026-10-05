#!/usr/bin/env bash
# Развёртывание воркера рабочего места под пользователем stl-wb. Запускать из-под shectory (нужен sudo).
# Значения секретов не печатаются. Сервис НЕ запускается: в конце печатается команда старта.
# Предусловия: пользователь stl-wb (linger), deploy key в GitHub, /usr/local/lib/stl-wb/unshare.
set -euo pipefail

WB=stl-wb
WBH=$(getent passwd "$WB" | cut -d: -f6)
[ -n "$WBH" ] || { echo "нет пользователя $WB" >&2; exit 1; }
SRC="$HOME/.config/stl-workbench/worker.env"
DST="$WBH/.config/stl-workbench/worker.env"
UID_WB=$(id -u "$WB")
as_wb() { sudo -u "$WB" -H env XDG_RUNTIME_DIR="/run/user/$UID_WB" "$@"; }

[ -x /usr/local/lib/stl-wb/unshare ] || { echo "нет /usr/local/lib/stl-wb/unshare" >&2; exit 1; }

# 1. клон (ssh через ключ stl-wb) и venv
as_wb bash -c 'test -d ~/stl-workbench/.git || git clone git@github.com:Shevbo/STL.git ~/stl-workbench'
as_wb bash -c 'cd ~/stl-workbench && git pull -q --ff-only'
as_wb bash -c 'cd ~/stl-workbench && { test -d .venv || python3 -m venv .venv; } && .venv/bin/pip -q install \
  httpx pydantic-settings structlog "protobuf>=5.26,<6" "ruff>=0.4,<0.5" "pytest>=8.2,<9" \
  "pytest-asyncio>=0.23,<0.24" pytest-timeout pytest-mock hypothesis respx croniter cuid2 fastapi asyncpg \
  "grpcio>=1.63,<2" googleapis-common-protos'

# 2. worker.env: переносим от shectory (600, без печати); если у stl-wb уже есть свой, не затираем
sudo install -d -m 700 -o "$WB" -g "$WB" "$WBH/.config/stl-workbench"
if sudo test -f "$DST"; then
  echo "worker.env у $WB уже есть: не перезаписываю"
elif [ -f "$SRC" ]; then
  sudo install -m 600 -o "$WB" -g "$WB" "$SRC" "$DST"
  echo "worker.env перенесён (значения не показываются)"
else
  echo "нет $SRC и нет $DST: создайте worker.env у $WB вручную" >&2
fi

# 3. юнит у stl-wb
as_wb bash -c 'mkdir -p ~/.config/systemd/user && cp ~/stl-workbench/scripts/stl-workbench.service ~/.config/systemd/user/ && systemctl --user daemon-reload'

# 4. убрать юнит и env у shectory (проверка перед удалением: перенос состоялся)
if sudo test -s "$DST"; then
  systemctl --user disable --now stl-workbench 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/stl-workbench.service" "$SRC"
  systemctl --user daemon-reload || true
  echo "юнит и worker.env у $(id -un) удалены (клон ~/stl-workbench и ~/stl-workbench-wt оставлены: удалить вручную)"
fi

echo "Готово, сервис не запущен. Старт: sudo -u $WB -H env XDG_RUNTIME_DIR=/run/user/$UID_WB systemctl --user enable --now stl-workbench"
