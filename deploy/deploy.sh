#!/usr/bin/env bash
# deploy.sh — полный деплой на хостер 83.69.248.175
# Использование: bash deploy/deploy.sh
# Ключ SSH должен быть доступен (ssh-agent или ~/.ssh/config)

set -euo pipefail

HOSTER="hoster"
REMOTE_DIR="/home/ubuntu/apps/shectory-trader"
SERVICE="shectory-trader"

# ── 1. Сборка фронтенда (на хостере, чтобы избежать PATH issues на Windows) ──
echo "▶ Сборка фронтенда будет выполнена на хостере..."

# ── 2. Синхронизировать код на хостер ───────────────────────────────────────
echo "▶ rsync → $HOSTER:$REMOTE_DIR"
ssh "$HOSTER" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.venv' \
  --exclude='frontend/node_modules' \
  . "$HOSTER:$REMOTE_DIR/"

# ── 3. Удалённые команды ─────────────────────────────────────────────────────
echo "▶ Установка зависимостей и перезапуск сервиса..."
ssh "$HOSTER" bash -s <<'REMOTE'
set -euo pipefail
export PATH="/home/ubuntu/.local/bin:$PATH"
cd /home/ubuntu/apps/shectory-trader

# Собрать фронтенд
echo "  Сборка фронтенда..."
(cd frontend && npm install --silent && npm run build)

# Установить Python-зависимости
poetry install --no-root --only main

# Установить / обновить nginx-конфиг
if ! diff -q deploy/nginx.conf /etc/nginx/sites-available/shectory-trader &>/dev/null; then
  sudo cp deploy/nginx.conf /etc/nginx/sites-available/shectory-trader
  sudo ln -sf /etc/nginx/sites-available/shectory-trader /etc/nginx/sites-enabled/shectory-trader
  sudo nginx -t && sudo systemctl reload nginx
  echo "  nginx перезагружен"
fi

# Установить / обновить systemd-юнит
if ! diff -q deploy/shectory-trader.service /etc/systemd/system/shectory-trader.service &>/dev/null; then
  sudo cp deploy/shectory-trader.service /etc/systemd/system/shectory-trader.service
  sudo systemctl daemon-reload
  echo "  systemd перезагружен"
fi

# Включить и перезапустить сервис
sudo systemctl enable shectory-trader
sudo systemctl restart shectory-trader
sleep 2
sudo systemctl is-active --quiet shectory-trader && echo "  ✓ сервис запущен" || (sudo journalctl -u shectory-trader -n 20 --no-pager; exit 1)
REMOTE

echo "✓ Деплой завершён → http://83.69.248.175"
