# Deploy — Shectory Trader (хостер: 83.69.248.175)

Frontend собирается локально, бэкенд работает как systemd-сервис. Nginx проксирует API и WS к FastAPI на порту 8000.

## Полный деплой (одна команда)

```bash
bash deploy/deploy.sh
```

Скрипт делает:
1. `npm run build` — собирает фронтенд
2. `rsync` — синхронизирует код на хостер
3. `poetry install` — обновляет Python-зависимости
4. Обновляет nginx-конфиг и перезагружает nginx (если изменился)
5. Обновляет systemd-юнит и перезапускает сервис

## Первая установка на хостере (выполнить вручную один раз)

```bash
# SSH на хостер
ssh ubuntu@83.69.248.175

# Установить poetry (если нет)
curl -sSL https://install.python-poetry.org | python3 -

# Создать папку приложения
mkdir -p /home/ubuntu/apps

# Создать env-файл с секретами
cat > ~/.shectory_trade.env <<'EOF'
FINAM_SECRET_TOKEN=<ваш_токен>
FINAM_ACCOUNT_ID=<ваш_account_id>
FINAM_MVP_SYMBOL=GZM6@RTSX
EOF
chmod 600 ~/.shectory_trade.env

# Разрешить ubuntu управлять nginx и systemd без пароля
# Добавить в /etc/sudoers.d/shectory:
#   ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl, /bin/cp, /bin/ln, /usr/sbin/nginx
```

После этого запускать `bash deploy/deploy.sh` с локальной машины.

## Только фронтенд (быстрое обновление UI)

```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ ubuntu@83.69.248.175:/home/ubuntu/apps/shectory-trader/frontend/dist/
```

## Логи и статус

```bash
# Статус сервиса
ssh ubuntu@83.69.248.175 sudo systemctl status shectory-trader

# Логи в реальном времени
ssh ubuntu@83.69.248.175 sudo journalctl -u shectory-trader -f

# Перезапустить вручную
ssh ubuntu@83.69.248.175 sudo systemctl restart shectory-trader
```

## Файлы деплоя

| Файл | Назначение |
|------|-----------|
| `deploy/deploy.sh` | Полный деплой одной командой |
| `deploy/nginx.conf` | Nginx-конфиг (proxy API + WS, раздача SPA) |
| `deploy/shectory-trader.service` | systemd-юнит для FastAPI |
