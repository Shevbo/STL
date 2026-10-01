
## 06:41 | main
Диагностировал race condition в AsyncAuthClient.get_token(): конкурентные gather-вызовы опроса без лока → N сессий Finam, кэш хранит вытеснённую → 100% 500/503; чиню с локом.
## 07:22 | main
Залил фикс race condition AsyncAuthClient.get_token (b876f48, сравнение объекта вместо свежести), исправил регрессию force_refresh, тесты OK; не выложен — ждёт рестарта shectory-trader, SSH обломалась.