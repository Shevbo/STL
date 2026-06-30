# Запуск QUIK-торговли из STL — схема, последовательность, автоматизация

Цель: убрать хрупкость цепочки QUIK -> DDE -> Lua -> агент -> STL -> роботы. Документ
описывает что и в каком порядке поднимать, что уже автоматизировано, что ещё руками, и
как это наблюдать (preflight), чтобы не ловить тихие отказы (как reject
"instrument not whitelisted" до QUIK).

## 1. Цепочка (снизу вверх)

```
[QUIK терминал]            торговый терминал брокера (FINAM), Windows VDS 83.69.248.180
   | DDE экспорт            таблицы -> DDE сервер SHECTORY_QUIK, топик data
[DDE]                       котировки/стакан/справочники читает агент
   | QLua sendTransaction   боевые заявки (file-queue C:\quik-bridge: cmd.jsonl/evt.jsonl)
[Lua скрипт]                shectory_trade.lua, загружен в QUIK (ACCOUNT/CLIENT_CODE заданы)
   | gRPC bidi (dial-out)   агент -> STL, Bearer, через TAP
[quik-agent.exe]           Go single-exe, C:\distr\dist\, agent_config.json рядом
   | gRPC                   STL = сервер (агент за NAT)
[STL backend]              FastAPI :8000, gRPC :50061, флаги quik_agent_enabled / quik_trading_enabled
   |
[STL UI / роботы]          ручные заявки (панель «Заявки QUIK») + будущие роботы через BrokerInterface
```

Ключевой принцип: **STL — источник истины для лимитов и whitelist**. Агент держит свой
конфиг как fail-safe backstop, но whitelist и потолки лимитов получает от STL по линку
(см. раздел 4). Мастер-флаг торговли — **двойной**: торгует только если ON и в STL, и в
агенте.

## 2. Последовательность запуска (холодный старт)

Порядок важен: каждый слой зависит от нижнего.

1. **QUIK терминал** на VDS — залогинен у брокера, идёт рынок.
2. **DDE экспорт** в QUIK настроен на сервер `SHECTORY_QUIK`, топик `data` (таблицы:
   текущие торги/стакан/справочник). Разово в GUI QUIK.
3. **Lua-скрипт** `shectory_trade.lua` загружен в QUIK (Сервисы -> Lua), `ACCOUNT` и
   `CLIENT_CODE` заданы, file-queue папка совпадает с `trade_queue_dir` агента
   (`C:\quik-bridge`). Разово; авто-загрузка — если добавить в авто-скрипты QUIK.
4. **Агент** `quik-agent.exe` запущен (как Windows-сервис `--service start` -> переживает
   ребут). На старте: self-update -> dial-out к STL -> Register. `agent_config.json` рядом
   с exe (токен env, trade_queue_dir, trading_enabled, лимиты-fallback).
5. **STL backend**: `quik_agent_enabled=true` (открывает gRPC :50061),
   `quik_trading_enabled=true` (мастер-флаг STL). Перезапуск сервиса подхватывает env.
6. **Whitelist**: НИЧЕГО руками — STL пушит свой whitelist агенту на Register (раздел 4).
7. **Боевой тест 1a**: дальняя лимитка 1 контракт -> active -> cancel. Затем 1b осторожно.
8. **Роботы** (когда подключатся к `BrokerInterface`): включать последними, после зелёного
   preflight.

## 3. Что автоматизировано / что руками

| Шаг | Сейчас | Можно автоматизировать | Как |
|---|---|---|---|
| QUIK залогинен + рынок | руками (RDP) | частично | автологин QUIK по профилю; вотчер «нет тиков N минут» -> алерт (уже есть DDE/QUIK lamp) |
| DDE экспорт | разово в GUI | нет (GUI QUIK) | сохранить настройку в профиле QUIK; документировать |
| Lua загружен | разово | да | добавить `shectory_trade.lua` в авто-скрипты QUIK (стартует с терминалом) |
| Агент запущен | **авто** (Windows-сервис + self-update) | done | `quik-agent --service install`; self-update на старте/03:00/по команде |
| Обновление exe | **авто** (self-update) | done | `publish_quik_agent.sh [agent_id]` на хостере -> агент качает+рестартит |
| **Whitelist агента** | **авто** (push из STL) | done | STL шлёт `SetLimits` на Register; раньше был ручной `set_whitelist.bat`+рестарт |
| Лимиты/коллар/cap | **авто** (push, ceiling-only) | done | тот же `SetLimits`; агентский конфиг = жёсткий потолок |
| Мастер-флаг торговли | руками (намеренно) | НЕТ (безопасность) | двойной флаг STL+агент; взвод — только человек |
| STL флаги/деплой | полу-авто (`deploy.sh`) | да | git push + ssh pull/build/restart |
| Роботы | руками | да (после preflight) | гейт `is_trade_ready()` + зелёный preflight |

Намеренно НЕ автоматизируется: взвод боевой торговли (`quik_trading_enabled`) и первый
боевой ордер. Это ручные операторские действия (Guard 3).

## 4. Синхронизация лимитов STL -> агент (убирает ручной whitelist)

Проблема, которая ломала торговлю 2026-06-30: whitelist в STL (env) и в агенте
(`agent_config.json`) — РАЗНЫЕ и расходились молча. STL пропускал заявку (GZU6 в его
whitelist), агент резал её `instrument not whitelisted` ДО QUIK -> «в квике нет следов».

Решение (контракт `SetLimits` / `LimitsState`):
- На Register STL пушит агенту `SetLimits{whitelist, max_per_order, max_working, collar,
  daily_cap}`. Агент **адаптирует whitelist** (replace) и берёт потолки **только на
  ужесточение** (свой конфиг — жёсткий backstop). Мастер-флаг НЕ пушится.
- Пустой whitelist в пуше игнорируется (fail-safe: плохой пуш не отключит всё).
- Агент эхает обратно `LimitsState` (эффективные лимиты). STL/UI показывают
  синхронизацию и подсвечивают расхождение (панель «Заявки QUIK», строка sync).
- Смена whitelist теперь = поменять env STL + рестарт STL (или переподключение агента).
  Никакого RDP/`set_whitelist.bat`/рестарта агента.

Остаётся как аварийный ручной путь: `quik_agent/lua/set_whitelist.bat` на VDS (если STL
недоступен). Конфиг агента — это нижняя граница; STL может только ужесточить потолки.

## 5. Preflight / readiness (наблюдаемость цепочки)

Перед взводом торговли проверить (видно в UI и в `/api/v1/quik/status`,
`/api/v1/quik/orders/config`):

- [ ] агент `link == green` (свежий gRPC)
- [ ] `diagnostics.dde == UP` (DDE жив, тики идут)
- [ ] `diagnostics.quik == UP` (QUIK отвечает)
- [ ] идёт стакан по нужным инструментам (`order_book_codes` содержит коды)
- [ ] `agent_limits` присутствует и `whitelist синхронизирован` (строка sync зелёная)
- [ ] `trading_enabled` (STL) = true И мастер-флаг агента = true (двойной)
- [ ] kill-switch не активен
- [ ] для робота: `broker.is_trade_ready()` (QuikBroker станет ready после отчёта
      positions/account из QUIK)

Тихий отказ ловится так: если whitelist рассинхронен — строка sync КРАСНАЯ ещё ДО
заявки; если заявку всё же отклонили — причина видна в колонке «Текст» таблицы «В работе».

## 6. Типичные отказы и где смотреть

| Симптом | Причина | Где видно / фикс |
|---|---|---|
| заявка rejected, «нет следов в QUIK» | агент режет (whitelist/лимит) до QUIK | колонка «Текст» = причина; строка sync; выровнять whitelist (теперь авто) |
| заявка rejected с текстом от QUIK | QUIK отклонил (шаг цены, счёт, ГО) | колонка «Текст» = текст QUIK |
| пустой стакан/график инструмента | нет DDE по коду / не тот контракт-месяц | `diagnostics.dde`, `order_book_codes`; график — REST `/api/v1/chart/bars` |
| F5 -> экран логина | транзиентный auth/me при перезагрузке | сплэш «Проверка сессии…» + ретраи (уже есть); кука валидна 30 дней |
| агент yellow/red | линк/DDE просели | `diagnostics`, алерты в Telegram; перезапуск агента (сервис) |
| «несколько агентов» | stale-записи в сторе | STL берёт единственного green (resolve fix) |

## 7. Что ещё стоит сделать (чтобы было совсем не хрупко)

- QUIK positions/account из агента -> `QuikBroker.is_trade_ready()` = true -> роботы на QUIK.
- Единая панель preflight в UI (сейчас сигналы разбросаны по status/config/diagnostics).
- Мигрировать реальных роботов (AI46/Lab) на `BrokerInterface`.
- Авто-загрузка Lua из авто-скриптов QUIK (убрать ручной шаг 3).
