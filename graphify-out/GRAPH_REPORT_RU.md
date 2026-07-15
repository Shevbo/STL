# Граф знаний: Shectory Trade & Lab

Дата сборки: 2026-06-15
Источник: весь репозиторий (`.`)

## Сводка по корпусу

- 286 файлов, ~394 880 слов
- 4670 узлов, 7395 рёбер, 304 сообщества
- Извлечение: 98% EXTRACTED, 2% INFERRED, 0% AMBIGUOUS
- Стоимость токенов: 297 866 input, 0 output

Большая часть графа это сгенерированный код. Это protobuf и gRPC обвязка Finam Trade API и Google API. Прикладной код выделяется в чёткие кластеры. Это библиотека индикаторов, ядро FastAPI, модели данных и планировщик, поток рыночных данных, проектная документация.

## God Nodes (ядро архитектуры)

Узлы с наибольшим числом связей. Это ваши центральные абстракции.

| # | Узел | Рёбер | Роль |
|---|------|-------|------|
| 1 | `Service` | 57 | Корень конфигурации Google API (сгенерировано) |
| 2 | `WsHub` | 53 | Шина реального времени для фронтенда |
| 3 | `Decimal` | 44 | Денежный тип во всех слоях |
| 4 | `Bar` | 38 | Свеча OHLCV, общая для рынка и бэктеста |
| 5 | `MarketDataFeed` | 38 | Конфляция котировок |
| 6 | `JSONSchema` | 38 | OpenAPI схема (сгенерировано) |
| 7 | `Settings` | 35 | Конфиг из env через pydantic-settings |
| 8 | `AsyncAuthClient` | 32 | JWT токен Finam |
| 9 | `ClientLibrarySettings` | 31 | Настройки GAPIC (сгенерировано) |
| 10 | `Order` | 31 | Заявка, общая для API и рантайма |

## Главный разбор: как `WsHub` связывает auth, рыночные данные и live-рантайм

`WsHub` это второй по связности узел графа. Он сидит на стыке трёх подсистем. Всё собирается в `lifespan()` в [app.py](trader/api/app.py).

### 1. Слой авторизации

`AsyncAuthClient` выдаёт JWT Finam. В lifespan создаётся один экземпляр `auth`.
Его метод `auth.get_token` передаётся как callback во все потоки и в сам хаб.

- [app.py:106](trader/api/app.py#L106): `get_token=auth.get_token` уходит в `WsHub`.
- [ws_hub.py:86-87](trader/api/ws_hub.py#L86-L87): хаб зовёт `_fetch_history()` для подгрузки баров через REST, используя токен.
- [app.py:763](trader/api/app.py#L763): сам сокет `/ws` закрыт гардом `ws_auth_ok`. Без cookie соединение рвётся с кодом 4401.

Auth входит в хаб двумя путями. Это токен для исходящих запросов и проверка cookie на входящем сокете.

### 2. Слой рыночных данных

Хаб держит три источника. Это `feed` (MarketDataFeed), `bars_stream`, `book_stream`.

- [app.py:99-108](trader/api/app.py#L99-L108): все три прокинуты в конструктор `WsHub`.
- [ws_hub.py:75-93](trader/api/ws_hub.py#L75-L93): `start()` подписывает symbol на feed, бары и стакан. Запускает по фоновой задаче на каждый поток.
- Циклы вещания: `_broadcast_loop` (котировки), `_bars_broadcast_loop` (свечи), `_book_broadcast_loop` (стакан).

`MarketDataFeed` сам по себе god node (38 рёбер). Он конфлейтит котировки, отдаёт только последнюю медленному потребителю. Это сообщество "Market Data Feed" с cohesion 0.11.

### 3. Слой live-рантайма и позиций

- [app.py:101](trader/api/app.py#L101): `pos_client=pos` (PositionsClient) уходит в хаб.
- [ws_hub.py:81-82](trader/api/ws_hub.py#L81-L82): при наличии pos_client стартует `_pos_poll_loop()`.
- Цикл опрашивает позиции и счёт Finam, вещает обновления `position_update` и account-сообщения клиентам.

Торговый рантахм (`STLRuntime`, `LiveRuntime`, `RobotScheduler`) живёт рядом. Планировщик получает те же `tx` и `pos` клиенты ([app.py:88-92](trader/api/app.py#L88-L92)). То есть хаб и рантайм делят клиентов Finam, но не зовут друг друга напрямую.

### Куда всё стекается

`WsHub` это веер наружу. Каждый браузерный сокет получает свою `asyncio.Queue` (maxsize 100) в словаре `_clients` ([ws_hub.py:109-112](trader/api/ws_hub.py#L109-L112)). Все циклы вещания кладут сообщения в очереди всех клиентов.

```
AsyncAuthClient ──get_token──┐
                             │
MarketDataFeed ──quotes──┐   │
BarsStream ──candles──┐  │   │
OrderBookStream ─book─┤  │   │
                      ▼  ▼   ▼
                   ┌──────────────┐
                   │    WsHub     │  ← ws_auth_ok гард на /ws
                   └──────────────┘
                      │  (per-client Queue)
PositionsClient ─poll─┘     │
                            ▼
                  браузерные WebSocket клиенты
```

Вывод. `WsHub` не считает и не торгует. Он единая точка фан-аут. Он держит JWT для исходящих REST-запросов, гард на входящем сокете, три потока рынка и опрос позиций. Всё сводится к одному сообщению на клиента.

## Прикладные сообщества

| Сообщество | Узлов | Cohesion | Что это |
|------------|-------|----------|---------|
| Indicator Library | 54 | 0.06 | ATR, Bollinger, EMA, MACD и пр. Чистый numeric для LAB |
| FastAPI App Core | 36 | 0.08 | Лайфспан, кампании, sweep, orphan reaper |
| Data Models & Scheduler | 24 | 0.10 | Robot, BacktestRun, LiveTrade, тик робота |
| Market Data Feed | 23 | 0.11 | MarketDataFeed, QuoteState, конфляция |
| Project Docs & Planning | 45 | 0.05 | Планы, спеки, память проекта |
| Backtest Grid (C55) | 24 | 0.18 | run_backtest_grid, метрики, мульти-комбо в одном subprocess |
| ISS Loader (C43) | 18 | 0.12 | Загрузка минутных баров FORTS с MOEX ISS |
| Auth Guard (C75) | 19 | 0.23 | require_auth, ws_auth_ok, тесты гарда |

## Неожиданные связи

Граф нашёл смысловые мосты без прямых вызовов.

- `BacktestLab UI` похож на `Backtest API (/api/v1/backtest)`. UI и эндпоинт описывают один контракт.
- `STL Order Comment` похож на `STL Links API (/api/v1/stl-links)`. Комментарий к заявке несёт связь робота.
- `Agent`, `_Restart`, `date` используют `Bar`. Это offload-агент тянет модель свечи из рантайма.

## Циклы импортов

Найдены только самопетли (файл ссылается сам на себя). Перекрёстных циклов между модулями нет.

- `trader/lab/scheduler.py`
- `trader/api/app.py`
- `trader/pos/client.py`

## Гиперрёбра (групповые связи)

- Deploy Pipeline. Скрипт, nginx, systemd, хостер.
- Optimization Agent Offload. Агент тянет джобы, импортирует модули LAB.
- LAB Robot Lifecycle. stl-link, robot, backtest, scheduler.
- M1 Market Data. Трёхслойная архитектура. WsSession, MarketDataFeed, Quote, QuoteState.
- Unified Auth. Мост авторизации между приложениями.

## Файлы вывода

- `graphify-out/graph.html` интерактивный граф, открыть в браузере
- `graphify-out/graph.json` сырые данные графа
- `graphify-out/GRAPH_REPORT.md` полный аудит-отчёт (англ.)
- `graphify-out/GRAPH_REPORT_RU.md` этот отчёт
