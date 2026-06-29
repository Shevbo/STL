# Sprint02 Phase 1 — план тестирования и пошаговая приёмка

Документ для приёмки QUIK-агента (Windows, Go) и его связки с STL.
Источник требований: docs/sprint02.md. Контракт: quik_agent/CONTRACT.md.
Провод: proto/shectory/quik/v1/quik_agent.proto.

## 1. Объём Phase 1

Phase 1 — только чтение и валидация данных. Транзакций нет.

В объёме:
- Чтение справочников FORTS через DDE (SecuritiesSnapshot).
- Рыночные данные: last, bid, ask, ОИ (MarketDataTick).
- Стакан, топ-N уровней (OrderBook).
- Параметры цены: Шаг цены, Ст. шага цены, коэффициент комиссии (ParamsSnapshot).
- Диагностика каналов и алерты (Diagnostics, Alert).
- Двунаправленный поток Session, Bearer-авторизация.
- Команды только чтения и контроля: SUBSCRIBE, UNSUBSCRIBE, REQUEST_DIAGNOSTICS, REQUEST_SECURITIES, SELF_UPDATE, RESTART.
- Самообновление exe по команде оркестратора.
- Иммунитет к ребуту: автоподъём как служба Windows.
- Алерты в Telegram. Одна CRITICAL-проверка интеграции SMS-шлюза.

Вне объёма (отложено в Phase 2):
- Выставление заявок.
- Перемещение заявок.
- Снятие заявок.
- Сделка по рынку.
- Контроль исполнения заявок.
- Полный боевой поток SMS через Garden-manager.
- trans2quik и Lua-скрипты в QUIK.
- Любые записывающие транзакции в терминал.

Граница Guard 3: в коде Phase 1 нет путей размещения, перемещения, снятия заявок и рыночной сделки.

## 2. План тестирования по слоям

### 2.1 Unit

- Коэффициент комиссии. coef = step_cost / price_step. Проверка на ParamRow.
  Деление на ноль и пустой price_step дают ошибку, не NaN.
- Парсинг DDE-таблицы. Сырая DDE-таблица разбирается в строки Security и ParamRow.
  Локаль чисел (запятая как разделитель) приводится к double корректно.
- Валидация сообщений. Схема AgentMessage заполнена обязательными полями.
  Проверка устаревания (staleness) по received_at_unix_ms и last_tick_age_ms.
  Монотонность seq в пределах сессии.

### 2.2 Integration

- Поток Session agent <-> STL по gRPC (bidi).
- Кадры agent -> STL: Register, Heartbeat, SecuritiesSnapshot, MarketDataTick,
  OrderBook, ParamsSnapshot, Diagnostics, Alert.
- Кадры STL -> agent: Ack (эхо seq), Command.
- Команды: COMMAND_TYPE_SUBSCRIBE, UNSUBSCRIBE, REQUEST_DIAGNOSTICS,
  REQUEST_SECURITIES, SELF_UPDATE, RESTART.
- Bearer-авторизация. Валидный токен в metadata authorization принимается.
  Невалидный или отсутствующий токен отклоняется, поток не открывается.
- Register — первый кадр после дозвона. До Register других кадров STL не ждёт.

### 2.3 System / manual

- Прогон против живого QUIK на Windows-боксе.
- Сверка значений агента с терминалом QUIK по RIU6.
- Сценарии устойчивости: обрыв DDE, ребут ОС, самообновление.
- Финальная пошаговая приёмка из раздела 3.

## 3. Пошаговая приёмка (чек-лист)

Прогоняется вживую с агентом на Windows-боксе и работающим QUIK.
Инструмент сверки: RIU6. Эталон комиссии: taker.

### Шаг 1. Подключение

- Действие: запустить агента, открыть STL, меню "Интерфейс биржи".
- Ожидание: зелёная лампа связи. STL получил Register и Heartbeat.
- Проверка: статус линка UP в UI. В логе STL виден Register с agent_version,
  host_name, build_rev. Heartbeat идёт периодически, dde_alive=true, quik_alive=true.
- [ ] PASS  [ ] FAIL

### Шаг 2. Справочники FORTS vs QUIK

- Действие: запросить REQUEST_SECURITIES. Получить SecuritiesSnapshot.
- Ожидание: список инструментов FORTS совпадает с таблицей в QUIK.
- Проверка: сверить code, class_code, price_step, step_cost по RIU6 и ещё 2-3
  инструментам. Числа равны до знака. is_full=true для полного снимка.
- [ ] PASS  [ ] FAIL

### Шаг 3. Рыночные данные last / ОИ по RIU6

- Действие: SUBSCRIBE на RIU6. Смотреть MarketDataTick.
- Ожидание: last и open_interest совпадают с терминалом QUIK в реальном времени.
- Проверка: last сверять с допуском 1 шаг цены (текущий тик мог обновиться).
  open_interest сверять точно на спокойном рынке. received_at_unix_ms свежий,
  возраст менее порога staleness.
- [ ] PASS  [ ] FAIL

### Шаг 4. Стакан топ-5 уровней RIU6 vs QUIK

- Действие: получить OrderBook по RIU6, глубина >= 5.
- Ожидание: топ-5 bids и asks совпадают с окном стакана QUIK.
- Проверка: цены и объёмы на 5 лучших уровнях с каждой стороны равны.
  bids и asks отсортированы best-first. Спред bid/ask неотрицательный.
- [ ] PASS  [ ] FAIL

### Шаг 5. Комиссия на 1 контракте vs эталон (taker)

- Действие: взять ParamRow по RIU6. Вычислить coef = step_cost / price_step.
- Ожидание: коэффициент равен эталонной taker-ставке (reference_commission_model).
- Проверка: посчитать комиссию на 1 контракт по taker-формуле. Сверить с эталоном
  до копейки. coef из ParamsSnapshot совпадает с локальным расчётом step_cost/price_step.
- [ ] PASS  [ ] FAIL

### Шаг 6. Валидация: оборвать DDE -> алерт + красная лампа

- Действие: остановить DDE-источник (закрыть экспорт или QUIK).
- Ожидание: агент шлёт Alert (severity WARN/CRITICAL, code DDE_DOWN). Лампа красная.
- Проверка: в STL пришёл Alert с code=DDE_DOWN. Diagnostics: dde=CHANNEL_STATE_DOWN.
  Лампа связи DDE красная. last_tick_age_ms растёт. Heartbeat dde_alive=false.
- [ ] PASS  [ ] FAIL

### Шаг 7. Ребут Windows -> агент сам поднялся, связь восстановлена

- Действие: перезагрузить Windows-бокс.
- Ожидание: служба агента стартует сама, дозванивается в STL, поток восстановлен.
- Проверка: после загрузки ОС без ручного запуска приходит новый Register.
  reconnects_since_start увеличился. Лампа связи снова зелёная. uptime_sec считается с нуля.
- [ ] PASS  [ ] FAIL

### Шаг 8. Самообновление по команде оркестратора

- Действие: отправить COMMAND_TYPE_SELF_UPDATE из STL.
- Ожидание: агент скачивает новую версию, перезапускается, в логе новая версия.
- Проверка: в логе агента запись о проверке и загрузке обновления. После рестарта
  Register с новым build_rev и agent_version. Старая сессия закрылась штатно, новая открылась.
- [ ] PASS  [ ] FAIL

### Шаг 9. Telegram: внештатная ситуация -> сообщение в ТГ

- Действие: спровоцировать внештатное событие (обрыв DDE или LINK_DOWN).
- Ожидание: сообщение приходит в Telegram.
- Проверка: в чат ТГ пришёл текст из Alert.message с кодом и временем. severity
  соответствует событию. Для одной CRITICAL-проверки подтвердить срабатывание
  интеграции SMS-шлюза (Phase 1 — только факт интеграции, не боевой поток).
- [ ] PASS  [ ] FAIL

## 4. Матрица валидации потоков данных

Для каждого потока: проверка схемы, порог свежести, детект разрывов, условия WARN и CRITICAL.

| Поток | Проверка схемы | Порог свежести (last_seen) | Детект разрыва | WARN | CRITICAL |
|---|---|---|---|---|---|
| Heartbeat | sent_at_unix_ms > 0; флаги заданы | нет HB > 2 интервалов | пропуск 2+ HB подряд | задержка HB | нет HB > 3 интервалов -> LINK_DOWN |
| MarketDataTick | code, received_at_unix_ms заданы | last_tick_age_ms ниже порога | возраст тика растёт без обновлений | возраст выше WARN-порога | DDE_DOWN, тиков нет дольше CRITICAL-порога |
| OrderBook | bids/asks best-first, неотриц. объёмы | received_at_unix_ms свежий | стакан не обновляется | устаревший стакан | пустой стакан при живом рынке |
| SecuritiesSnapshot | items не пуст; price_step, step_cost > 0 | по запросу REQUEST_SECURITIES | снимок старше суток | частичная дельта без полного снимка | справочник недоступен |
| ParamsSnapshot | coef == step_cost/price_step | актуален к расчёту комиссии | price_step или step_cost = 0 | расхождение coef с расчётом | деление на ноль, комиссия не считается |
| Diagnostics | enum-состояния заданы | uptime_sec растёт | DEGRADED по любому каналу | один канал DEGRADED | любой канал DOWN |

Соответствие состояний: CHANNEL_STATE_UP норма, CHANNEL_STATE_DEGRADED -> WARN,
CHANNEL_STATE_DOWN -> CRITICAL. AlertSeverity: INFO лог, WARN внимание,
CRITICAL дублируется в SMS (шлюз в Phase 2, в Phase 1 одна проверка интеграции).

## 5. Definition of Done — Phase 1

- Агент дозванивается в STL, открыт один долгоживущий поток Session.
- Bearer-токен из keymaster принимается, невалидный отклоняется. Значение токена нигде не печатается и не коммитится.
- Справочники FORTS, last/ОИ, стакан, параметры цены приходят и сверены с живым QUIK по RIU6.
- Коэффициент комиссии coef = step_cost / price_step сходится с taker-эталоном на 1 контракте.
- Обрыв DDE даёт Alert и красную лампу. Diagnostics отражает состояние каналов.
- После ребута Windows агент поднимается сам, связь восстанавливается без ручных действий.
- Самообновление по COMMAND_TYPE_SELF_UPDATE проходит, новая версия видна в логе и в Register.
- Внештатное событие доходит в Telegram. Одна CRITICAL-проверка подтверждает интеграцию SMS-шлюза.
- Все 9 шагов приёмки помечены PASS.
- В коде Phase 1 нет путей выставления, перемещения, снятия заявок и рыночной сделки (Guard 3).
