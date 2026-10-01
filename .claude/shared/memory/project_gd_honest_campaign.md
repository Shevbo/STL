---
name: gd-honest-campaign
description: GD-кампания gdhonest на честных метриках (2026-07-21); i9 task-канал починен; optimizer остановлен и ждёт перезапуска
metadata: 
  node_type: memory
  type: project
  originSessionId: 5fdc91a5-ba5c-452e-8877-49921eaea90b
  modified: 2026-07-22T23:12:59.449Z
---

2026-07-22 ~00:20 UTC доквьючен ВЕСЬ рынок: `camp-20260722-honest-*` — 399 джоб (19 символов RI,BR,Si,GZ,MX,NG,SF,PD,BT,NA,BM,MM,CC,CR,ED,Eu,SR,SS,SV × 21 стратегия без us_open_fvg, 309,643 комбо) + 19 джоб us_open_fvg random-sample 4000/символ. Итого ~406k комбо, оценка ~3-4 суток на i9 (≈4-5k комбо/час). Пользователь: «i9 не должен спать, управляй автономно». Фоновый монитор: 15-мин поллы, авто-реквью транзиентных 502/timeout (кап 3 попытки/джоба), 10ч на итерацию — по таймауту ПЕРЕВЗВОДИТЬ до полного завершения. По завершении ВСЕГО: финальный топ, удалить pause_local, запустить shectory-optimizer.

Кампания `camp-20260721-gdhonest-*` (22 джобы, символ GD, 2026-01-01..2026-07-20, --pin qty=1, avg off) заквьючена 2026-07-21 ~21:32 UTC. us_open_fvg-джоба на 131k комбо отменена и переквьючена как paramSets random-sample 4000 (`camp-20260721-gdhonest-r213328-us_open_fvg-GD`). Ранжирование: recovery_factor_mtm_oos / max_mae / windows_profitable (колонки в БД и на хостере есть).

Контекст доставки на i9:
- Task-канал был мёртв с апдейта агента 2026-07-19: `_set_priority(idle=True)` TypeError в `_run_task_unit` убивал КАЖДЫЙ generic-таск. Фикс: commit `1ff9d37` в main.
- raw.githubusercontent с сети i9 зарезан (RST), НО cdn.jsdelivr.net/gh/Shevbo/STL@<sha> с i9 РАБОТАЕТ (проверено curl с консоли i9) — рабочий путь доставки файлов.
- Оператор руками положил на i9: scripts/opt_agent.py, trader/lab/backtest.py, trader/lab/window_metrics.py (sha сверены certutil = git blob sha).
- Рестарт агента не нужен: воркеры (Windows spawn) перечитывают код с диска при пересборке пула; пересборка = смена `i9_workers` в agent_control (9→rebuild→10).
- `update_token` из agent_control УДАЛЁН (агент не долбит недоступный raw). Self-update i9 выключен, пока оператор не задаст `OPT_AGENT_RAW_BASE=https://cdn.jsdelivr.net/gh/Shevbo/STL@main` (кэш jsdelivr ~12ч) и не перезапустит агента.

СТАТУС 2026-07-23 02:00: 105 джоб посчитано, победители найдены, Si-bollinger заведён в paper. ХВОСТ: 2 fvg-джобы (Si, SV, по 4000 сетов) ЗАВИСЛИ — оптимизаторский агент i9 (Win10-HyperV, opt_agent.py) МЁРТВ ~12ч: heartbeat протух (last recv ~14:00 22.07), последний claim бэктеста 10:56 22.07, машина ПИНГУЕТСЯ (процесс агента умер/завис, не машина). VDS-фолбэк их не возьмёт (cap 150 < 4000, и pause_local=1). Джобы оставлены в queued — до перезапуска агента i9 оператором (его бокс, рестарт не мой). Оптимизатор всё ещё STOP, pause_local всё ещё 1 — вернуть после решения по i9. МОЯ ОШИБКА: несколько ходов докладывал «fvg считается», читая ЗАМОРОЖЕННЫЙ снимок activity как live — всегда проверять возраст heartbeat (now - _recv_ts), а не только поле activity.

НЕЗАКРЫТО:
- `pause_local='1'` в agent_control (VDS-fallback выключен: он перехватывал camp-джобы, реквьюнутые репером). После кампании УДАЛИТЬ ключ pause_local.
- Репер (app.py ~line 1001) реквьюит camp-джобы i9 уже через 8 минут — порог под мелкие opt-раунды; длинным camp-джобам нужен свой интервал (как 90 мин у vds-fallback). Кодовый фикс + рестарт сервиса — после кампании.
- `shectory-optimizer` на хостере ОСТАНОВЛЕН для фокусной кампании; его 193 queued opt-джобы помечены failed («cancelled: cleared for GD honest-metrics campaign»). После кампании: `sudo systemctl start shectory-optimizer`.
- queue_campaign.py печатает `run=?` — сервер отвечает `run_id`, скрипт читает `runId` (косметика).
- Висит зомби-джоба `fulu4oh79b7lrxl44mi1bzh0` running с 2026-07-13 (reaper не подобрал).
- Флаг `--campaign` добавлен в queue_campaign.py (commit `9caeb17`) — без него camp-джобы не зеркалятся в leaderboard (см. [[campaign-backfill]]).

Связано: [[optimization-campaign]], [[vds-load]].
