#!/usr/bin/env bash
# Полные сканы витрины хитпарада: показать или отменить.
#
# ЗАЧЕМ. Запрос витрины (trader/api/app.py:2201) идёт без LIMIT и без индекса по
# optimization_leaderboard (4.8 млн строк, 2.9 ГБ). Каждое открытие страницы
# запускает новый полный скан, предыдущий не отменяется. 22.09.2026 их
# накопилось девять по 7-8 минут, LA хостера дошла до 31.
#
# Отмена лечит СИМПТОМ. Причина лечится индексом (strategy, symbol, score DESC)
# и statement_timeout — передано письмом в ui-ux, trader/api их зона.
#
#   bash showcase_scans_v1.sh            показать
#   bash showcase_scans_v1.sh --cancel   отменить всё, что висит дольше минуты
#
# Отмена = pg_cancel_backend: SIGINT, запрос падает, СОЕДИНЕНИЕ живёт, пул API
# продолжает работать. Запрос читающий, терять нечего. Параллельные воркеры
# умирают вместе с ведущим. Права суперюзера не нужны: бэкенды витрины и этот
# скрипт ходят одной ролью project_stl_app, а свои бэкенды роль гасит сама.
#
# ВАЖНО: сначала закрой страницу хитпарада. Открытая вкладка с автообновлением
# запустит новый скан сразу после отмены.
set -euo pipefail
ENV_FILE=${STL_ENV_FILE:-/home/ubuntu/.shectory_trade.env}
set -a; . "$ENV_FILE"; set +a
SIG="%DISTINCT ON (strategy, symbol)%"
WHERE="state='active' AND query LIKE '$SIG' AND now()-query_start > interval '1 min'"
if [ "${1:-}" = "--cancel" ]; then
    psql "$LAB_DB_URL" -c "SELECT pid, date_trunc('second', now()-query_start) AS ждёт,
        pg_cancel_backend(pid) AS отменён FROM pg_stat_activity a WHERE $WHERE"
else
    psql "$LAB_DB_URL" -c "SELECT pid, date_trunc('second', now()-query_start) AS ждёт,
        (SELECT count(*) FROM pg_stat_activity w WHERE w.leader_pid = a.pid) AS воркеров
        FROM pg_stat_activity a WHERE $WHERE"
    echo "нагрузка:"; uptime
fi
