# Shectory LAB — MVP v1 Design

**Date:** 2026-05-28  
**Status:** Draft  
**Scope:** MVP v1 — минимальный рабочий LAB внутри существующего Trader

---

## 1. Контекст и цели

### Что строим

Модуль LAB расширяет существующий Shectory Trader тремя витринами:

1. **Live Robots** — витрина активных роботов: статус, P&L, трейды. Вмешательство: пауза, смена параметров
2. **Market Browser** — браузер инструментов FORTS: графики, стакан, аналитика
3. **Backtest Lab** — подбор параметров стратегии на исторических минутных данных, перебор grid search

### Что НЕ строим в v1

- Транслятор Pine Script → Python (отдельный проект)
- QUIK/Lua интеграция
- Под-минутные (тиковые) бэктесты (нет исторических тиков у Finam)
- Walk-forward analysis
- Risk manager для множества роботов (один робот на счёт)
- Отдельное Next.js приложение (расширяем текущий Svelte фронт)
- Карточка в Shectory Portal (v2)

---

## 2. Архитектура

### Подход: монолит с изоляцией (Approach A)

LAB встраивается в существующий `trader/` FastAPI процесс. Изоляция обеспечивается:
- Live роботы: `asyncio` tasks внутри event loop
- Backtest: отдельный `subprocess` (падение теста не затрагивает live)

```
trader/ (существующий FastAPI процесс)
├── api/app.py          — +LAB endpoints
├── lab/
│   ├── engine.py       — Robot Runner (asyncio tasks)
│   ├── backtest.py     — Backtest Runner (subprocess)
│   ├── runtime.py      — STL API (live + backtest реализации)
│   ├── scheduler.py    — cron scheduler для роботов
│   └── models.py       — Pydantic модели
└── db.py               — asyncpg connection pool

frontend/src/components/
├── LabPanel.svelte      — корневой компонент LAB с 3 вкладками
├── LiveRobots.svelte
├── MarketBrowser.svelte (расширение текущего ChartFrame)
└── BacktestLab.svelte
```

### База данных

**PostgreSQL на Hoster** — новая база `project_stl`.

В Python: `asyncpg` (без ORM, сырые async запросы — в духе текущего кода).

Аутентификация: **Shectory ID Bridge API** — текущий `trader/auth/portal.py` (`verify_portal_credentials`) уже реализует этот паттерн. LAB переиспользует его без изменений.

---

## 3. Схема БД (Prisma schema)

**Стандарт Shectory:** Prisma 7 + adapter-pg (как Komissionka/OurDiary).

```prisma
// prisma/schema.prisma

generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

// STL_LINK — коннектор к брокерскому счёту
model StlLink {
  id          String   @id @default(cuid())
  userEmail   String
  broker      String   @default("finam")
  exchange    String   @default("FORTS")
  accountId   String
  instruments String[]
  operations  String   @default("RW")  // "R" | "RW"
  enabled     Boolean  @default(true)
  createdAt   DateTime @default(now())

  robots      Robot[]

  @@map("stl_links")
}

// Робот — скрипт + параметры + расписание
model Robot {
  id          String    @id @default(cuid())
  userEmail   String
  stlLinkId   String
  stlLink     StlLink   @relation(fields: [stlLinkId], references: [id])
  name        String
  scriptCode  String
  paramsJson  Json      @default("{}")
  stateJson   Json      @default("{}")  // произвольный dict: set_state/get_state робота, сохраняется в БД после каждого цикла
  schedule    String    @default("*/5 * * * *")  // cron expr
  deployed    Boolean   @default(false)
  deployedAt  DateTime?
  version     Int       @default(1)
  createdAt   DateTime  @default(now())
  updatedAt   DateTime  @updatedAt

  backtestRuns BacktestRun[]
  liveTrades   LiveTrade[]
  liveMetrics  LiveMetric[]

  @@map("robots")
}

// Бэктест-прогон (одна задача = один период + один grid)
model BacktestRun {
  id         String    @id @default(cuid())
  robotId    String
  robot      Robot     @relation(fields: [robotId], references: [id])
  paramsGrid Json                              // перебираемые комбинации параметров
  dateFrom   DateTime
  dateTo     DateTime
  status     String    @default("pending")     // pending|running|done|failed
  errorMsg   String?
  createdAt  DateTime  @default(now())
  finishedAt DateTime?

  results    BacktestResult[]

  @@map("backtest_runs")
}

// Результат одной комбинации параметров
model BacktestResult {
  id          String  @id @default(cuid())
  runId       String
  run         BacktestRun @relation(fields: [runId], references: [id])
  params      Json
  trades      Json    @default("[]")
  equityCurve Json    @default("[]")
  sharpe      Float?
  maxDrawdown Float?
  winRate     Float?
  totalReturn Float?
  totalTrades Int?

  @@map("backtest_results")
}

// Трейды живых роботов
model LiveTrade {
  id        String   @id @default(cuid())
  robotId   String
  robot     Robot    @relation(fields: [robotId], references: [id])
  symbol    String
  side      String                        // "buy" | "sell"
  qty       Int
  price     Decimal  @db.Decimal(18, 6)
  orderId   String?
  status    String
  timestamp DateTime @default(now())

  @@map("live_trades")
}

// Метрики живых роботов (снимок каждый цикл)
model LiveMetric {
  id        String   @id @default(cuid())
  robotId   String
  robot     Robot    @relation(fields: [robotId], references: [id])
  equity    Decimal  @db.Decimal(18, 2)
  pnl       Decimal  @db.Decimal(18, 2)
  positions Json     @default("{}")
  timestamp DateTime @default(now())

  @@map("live_metrics")
}
```

**Workflow деплоя БД:**
```bash
npx prisma generate
npx prisma migrate deploy   # prod — всегда deploy, не dev
```

---

## 4. STL Runtime API

Единый интерфейс для скриптов роботов. Одинаковые сигнатуры — разные реализации (live vs backtest).

```python
class STLRuntime(Protocol):
    # Market data
    async def get_quote(self, symbol: str) -> Quote: ...
    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]: ...
    async def get_orderbook(self, symbol: str) -> OrderBook: ...

    # Order management
    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order: ...
    async def cancel_order(self, order_id: str) -> None: ...
    async def get_orders(self) -> list[Order]: ...

    # Position & account
    async def get_position(self, symbol: str) -> Position: ...
    async def get_account(self) -> AccountSummary: ...

    # State (персистентное хранилище между запусками)
    def get_state(self, key: str, default=None) -> Any: ...
    def set_state(self, key: str, value: Any) -> None: ...

    # Logging
    def log(self, msg: str, level: str = "info") -> None: ...
```

**LiveRuntime** — реализация для живой торговли:
- `get_quote` → существующий `QuoteStream`
- `get_bars` → существующий `BarsStream`
- `place_order` → существующий `TxClient`
- `get_state` / `set_state` → JSONB колонка `robots.state_json`

**BacktestRuntime** — реализация для симуляции:
- `get_quote` → исторические бары из БД
- `get_bars` → срез до текущего шага симуляции
- `place_order` → order matcher (market: цена следующего бара open, limit: hit-or-miss)
- `get_state` / `set_state` → in-memory dict

---

## 5. Robot Script Interface

Скрипт робота — Python модуль с одной обязательной функцией:

```python
# Вызывается на каждом баре (или по таймеру)
async def on_bar(stl: STL, params: dict) -> None:
    ...
```

Опциональные хуки:
```python
async def on_start(stl: STL, params: dict) -> None: ...   # при запуске
async def on_stop(stl: STL, params: dict) -> None: ...    # при остановке
async def on_error(stl: STL, error: Exception) -> None: ...  # при ошибке
```

### Референс-стратегии (входят в v1)

**1. EMA Crossover:**
```python
async def on_bar(stl, params):
    bars = await stl.get_bars(params["symbol"], tf=5, n=params["slow_period"] + 1)
    fast = ema(bars, params["fast_period"])
    slow = ema(bars, params["slow_period"])
    pos = await stl.get_position(params["symbol"])
    if fast[-1] > slow[-1] and pos.qty == 0:
        await stl.place_order(params["symbol"], "buy", 1, bars[-1].close)
    elif fast[-1] < slow[-1] and pos.qty > 0:
        await stl.place_order(params["symbol"], "sell", pos.qty, bars[-1].close)
```

**2. RSI Mean Reversion:**
```python
async def on_bar(stl, params):
    bars = await stl.get_bars(params["symbol"], tf=5, n=params["period"] + 1)
    rsi_val = rsi(bars, params["period"])
    pos = await stl.get_position(params["symbol"])
    if rsi_val < params["oversold"] and pos.qty == 0:
        await stl.place_order(params["symbol"], "buy", 1, bars[-1].close)
    elif rsi_val > params["overbought"] and pos.qty > 0:
        await stl.place_order(params["symbol"], "sell", pos.qty, bars[-1].close)
```

---

## 6. Backtest Engine

Запускается в отдельном `subprocess` через `multiprocessing.Process`.

**Алгоритм:**

1. Загрузить исторические бары из Finam API (минутки) для заданного периода
2. Для каждой комбинации параметров из `params_grid`:
   a. Создать `BacktestRuntime` с симулятором
   b. Прокрутить бары от `date_from` до `date_to`, вызывая `on_bar` на каждом
   c. На каждом вызове `place_order` — симулировать исполнение (market: open следующего бара)
   d. Собрать trades, equity curve
   e. Посчитать метрики: Sharpe, Max Drawdown, Win Rate, Total Return
   f. Записать в `backtest_results`
3. Обновить `backtest_runs.status = 'done'`

**Order matching (упрощённый):**
- Market order: исполняется по open следующего бара
- Limit order: исполняется если бар коснулся цены (high >= price для buy, low <= price для sell)
- Slippage: не моделируется в v1, но добавляется параметр `slippage_points`

**Предупреждение в UI:** "Результаты бэктеста могут отличаться от live из-за slippage, latency и market impact"

---

## 7. Scheduler (Live Robots)

Лёгкий планировщик на `asyncio` поверх библиотеки `croniter`.

```python
# Каждые N секунд проверяет deployed роботов в БД
# Запускает/останавливает asyncio tasks по расписанию
class RobotScheduler:
    async def start(self): ...          # poll loop
    async def run_robot(self, robot): ...  # запуск одного
    async def stop_robot(self, robot_id): ...
```

**Один активный робот за раз** (ограничение MVP v1). Защита: при попытке запустить второго — ошибка с сообщением пользователю.

**Recovery при рестарте:** робот с `deployed=true` автоматически рестартует. Открытые позиции не закрываются — робот продолжает с текущего состояния рынка.

---

## 8. REST API (новые endpoints)

```
# STL Links
GET    /api/v1/stl-links
POST   /api/v1/stl-links
PUT    /api/v1/stl-links/:id
DELETE /api/v1/stl-links/:id

# Robots
GET    /api/v1/robots
POST   /api/v1/robots
GET    /api/v1/robots/:id
PUT    /api/v1/robots/:id
DELETE /api/v1/robots/:id
POST   /api/v1/robots/:id/deploy    — deployed=true, scheduler подхватывает
POST   /api/v1/robots/:id/undeploy  — deployed=false, scheduler останавливает

# Backtest
POST   /api/v1/backtest/run
GET    /api/v1/backtest/:run_id/status
GET    /api/v1/backtest/:run_id/results

# Live metrics (через WS hub — уже есть)
# Добавляем типы сообщений: robot_update, live_trade, live_metric
```

---

## 9. Frontend: 3 вкладки LAB

### Интеграция в UI

Добавляем кнопку "LAB" в шапку приложения. При клике — открывается `LabPanel.svelte` поверх или рядом с текущим layout.

### Tab 1: Live Robots

- Список роботов (имя, symbol, статус, P&L, трейды)
- Клик → правая панель: equity curve (lightweight-charts), параметры с редактированием, кнопки "Stop" / "Send to Backtest"
- Статус обновляется через WebSocket (существующий WS hub)

### Tab 2: Market Browser

- Расширение существующего ChartFrame (уже работает)
- Добавить: список инструментов с поиском, быстрое переключение между ними
- Аналитика: volume profile, текущие параметры инструмента

### Tab 3: Backtest Lab

- Выбор робота из списка
- Период: date_from, date_to
- Grid параметров: таблица ключ → [min, max, step] или список значений
- Кнопка "Run" → запуск backtest
- Прогресс-бар (polling status)
- Таблица результатов: строка = комбинация параметров, колонки = Sharpe, Drawdown, WinRate, Return, Trades
- Клик на строку → equity curve
- "Deploy" на выбранной строке → `PUT /robots/:id` + `POST /robots/:id/deploy`

---

## 10. Технические ограничения v1

| Ограничение | Причина | Когда снять |
|-------------|---------|-------------|
| Один робот на счёт | Нет risk manager | v2 |
| Только минутные бары для бэктеста | Finam не даёт исторические тики | v2 + свой recorder |
| Без walk-forward | Сложность | v2 |
| Без отмены бэктеста на лету | subprocess kill не чистый | v1.1 |
| Slippage не моделируется | Упрощение | v1.1 |

---

## 11. Зависимости и инфраструктура

**Новые Python зависимости:**
- `asyncpg` — Postgres async клиент (Python читает таблицы Prisma напрямую)
- `croniter` — парсинг cron выражений
- `multiprocessing` (stdlib) — изоляция бэктестов

**Новые Node.js инструменты (только для миграций):**
- `prisma` — `npx prisma migrate deploy` при деплое
- `@prisma/client` + `@prisma/adapter-pg` — по стандарту Shectory

**Новая БД:** `project_stl` на Hoster Postgres.
- Создание: `CREATE DATABASE project_stl OWNER stl_user;`
- `DATABASE_URL` в `.env` на Hoster
- Миграции запускаются из папки `prisma/` в корне репо

**Деплой:** существующий systemd сервис `shectory-trader` — без изменений в деплой-процессе.
Добавить шаг в deploy script: `npx prisma migrate deploy` перед рестартом сервиса.

**Nginx:** без изменений — LAB endpoints идут через `/api/` который уже проксируется.

---

## 12. Последовательность реализации

1. **DB + migrations** — создать таблицы, asyncpg pool в `trader/db.py`
2. **STL Runtime** — Protocol + LiveRuntime + BacktestRuntime
3. **Scheduler** — asyncio-based планировщик роботов
4. **Backtest Engine** — subprocess runner + order matching + metrics
5. **REST API** — новые endpoints в `trader/api/app.py`
6. **Frontend** — LabPanel + 3 вкладки
7. **Референс-стратегии** — EMA Crossover + RSI Mean Reversion
8. **Интеграция и тест** — end-to-end: написать робота → backtest → deploy → live
