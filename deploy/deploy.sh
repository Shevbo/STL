#!/usr/bin/env bash
# deploy.sh — полный деплой на хостер 83.69.248.175
# Использование: bash deploy/deploy.sh
# Требует: git push уже выполнен, SSH доступ настроен в ~/.ssh/config

set -euo pipefail

HOSTER="hoster"
REMOTE_DIR="/home/ubuntu/apps/shectory-trader"
SERVICE="shectory-trader"

# ── 1. Проверить что коммиты готовы к отправке ──────────────────────────────
echo "▶ Проверка статуса git..."
if ! git diff-index --quiet HEAD -- ':!.claude/worktrees'; then
  echo "✗ Есть неустановленные изменения (исключая .claude/worktrees). Используйте: git add && git commit"
  exit 1
fi

# ── 2. Push на удалённый репозиторий ────────────────────────────────────────
echo "▶ git push..."
git push github main

# ── 3. Удалённые команды ─────────────────────────────────────────────────────
echo "▶ Обновление кода и перезапуск сервиса на $HOSTER..."
ssh "$HOSTER" bash -s <<'REMOTE'
set -euo pipefail
export PATH="/home/ubuntu/.local/bin:$PATH"
cd /home/ubuntu/apps/shectory-trader

# Обновить код из git
echo "  git pull..."
git pull github main

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

echo "✓ Деплой завершён → https://stl.shectory.ru/"
