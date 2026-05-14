# Finam API Access Checklist

## Stage 0 — первоначальное подключение

- [ ] Счёт у ФИНАМ открыт
- [ ] Счёт имеет доступ к FORTS (срочный рынок)
- [ ] `account_id` известен (из личного кабинета)
- [ ] Secret token выпущен на https://api.finam.ru/tokens
- [ ] Сохранён в `~/.shectory_trade.env` (НЕ в репозиторий)
- [ ] Запущен: `poetry run pytest tests/auth/test_auth_integration.py -v -m integration`
- [ ] Тест PASS

## Переменные окружения

| Переменная | Описание |
|---|---|
| FINAM_SECRET_TOKEN | API secret token |
| FINAM_ACCOUNT_ID | Номер торгового счёта |
| FINAM_API_BASE_URL | Base URL (default: https://api.trade.finam.ru) |
| FINAM_TOKEN_REFRESH_BEFORE_SECS | Обновлять JWT за N секунд до истечения (default: 60) |

## Ограничения

- **Rate limit:** до 200 вызовов/мин на метод
- **Техобслуживание:** 05:00–06:15 МСК ежедневно — API недоступен
- **WebSocket:** разрывается раз в 24ч — авто-reconnect реализуется в M1

## Ротация токена

При компрометации:
1. Отозвать на https://api.finam.ru/tokens
2. Создать новый
3. Обновить в `~/.shectory_trade.env`
4. **Никогда** не коммитить реальный токен в git
