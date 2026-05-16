# Shectory Trader Frontend — Design Spec

## 1. Architecture

**Tech stack:** Svelte 5 (runes), Vite, TypeScript. Single Page Application.

**Deployment:**
- Nginx на VDS отдаёт статику (dist/), проксирует `/api/` и `/ws/` на FastAPI :8000
- Браузер на ноутбуке подключается к VDS через HTTPS/WSS

**Два режима работы:**

| Режим | Источник данных | Назначение |
|---|---|---|
| Online | VDS → M8 Trader API (WebSocket) | Оперативный мониторинг и торговля |
| Offline | локальный JSON-файл | Бэктест стратегий без подключения к серверу |

**Файловая структура:**
```
frontend/
├── src/
│   ├── lib/
│   │   ├── stores/          # Svelte 5 $state stores (quotes, positions, robots)
│   │   ├── ws.ts            # WebSocket клиент с rAF-батчингом
│   │   ├── api.ts           # REST клиент (httpx → fetch)
│   │   └── types.ts         # Quote, Robot, Position, ServiceStatus
│   ├── components/
│   │   ├── TopBar.svelte
│   │   ├── RobotsPanel.svelte
│   │   ├── ChartFrame.svelte
│   │   ├── InstrumentPanel.svelte
│   │   ├── LabBar.svelte
│   │   ├── BottomBar.svelte
│   │   └── CodeEditor.svelte
│   ├── App.svelte
│   └── main.ts
├── vite.config.ts
└── package.json
```

---

## 2. Data Flow

**WebSocket → UI pipeline:**

```
WS сообщения (50 тиков/сек)
  ↓
ws.ts: накапливает в pending[]
  ↓
requestAnimationFrame (60 fps)
  ↓
flush: применяет pending[] к $state stores
  ↓
Svelte 5 runes: точечные обновления DOM (только изменившиеся ячейки)
```

**Состояния:**
```typescript
// stores/quotes.ts
let quotes = $state<Record<string, Quote>>({});

// stores/robots.ts
let robots = $state<Robot[]>([]);

// stores/services.ts
let services = $state<Record<string, "ok" | "warn" | "error">>({});
```

**Оффлайн-режим:** `ws.ts` заменяется на `offline-player.ts` — читает JSON, воспроизводит тики с той же rAF-логикой. Остальной UI не знает о разнице.

---

## 3. Оперативный режим — компоненты

### TopBar
Горизонтальная полоса сверху:
- Слева: счёт (депозит, свободные средства, в позиции, вариационная маржа)
- Справа: кнопка **Lab** (переключение режимов), индикатор соединения

### RobotsPanel (левая колонка)
Вертикальный список карточек роботов. Каждая карточка:
- Имя робота (= название фрейма на графике)
- Ключевые показатели: депозит, прибыль, количество сделок, текущая позиция
- Ссылка на настройки
- В Lab-режиме: рядом с живым P&L отображается гипотетический P&L из бэктеста

### ChartArea (центр)
Фреймы графиков — по одному на каждого активного робота. Имя фрейма = имя робота.

Каждый фрейм:
- **uPlot** — тиковый ряд bid/ask (высокочастотные данные, минимальный вес)
- **TradingView Lightweight Charts** — OHLC свечи + маркеры ордеров и сделок
- Объёмы под свечами
- В Lab-режиме: дополнительный ряд equity curve из бэктеста

### InstrumentPanel (правая колонка)
Параметры текущего инструмента (привязан к выбранному фрейму):
- Ценовой коридор (лимиты биржи)
- ГО (гарантийное обеспечение) / маржа
- Дата экспирации

### BottomBar
Две строки:
1. **Лента событий** — последние уведомления (сделки, алерты, ошибки), прокручиваемая
2. **Статус-бар** — компактная строка с индикаторами статуса сервисов: M0 Auth, M1 MD, M2 TX, M3 OMS, M5 Pos, M7 Audit. Каждый — цветная точка + аббревиатура. Занимает одну строку, не требует отдельной панели.

---

## 4. Lab Manager — компоненты

Лаба — оверлей поверх операционного режима. Включается кнопкой **Lab** в TopBar.

При включении Lab-режима появляется **LabBar** — горизонтальная панель между ChartArea и BottomBar. Три вкладки:

### LabBar → Strategies
- Список сохранённых стратегий/роботов с параметрами
- Кнопки: **Load** (загрузить на текущий фрейм), **Export** (скачать `.json` → загрузить на VDS через M8 API)
- Переключатель **Offline** — переводит источник данных в локальный JSON-файл

### LabBar → Backtest
- Выбор периода, инструмента, параметров запуска
- Кнопка **Run** → результаты рендерятся как equity curve поверх графика в ChartArea
- Статус прогресса (% выполнения)

### LabBar → Scripts
- Список `.py` скриптов
- Кнопка **Edit** → открывает CodeEditor

### CodeEditor
Плавающая resizable-панель (не модальное окно):
- **Monaco Editor** (движок VS Code) — подсветка синтаксиса Python
- Кнопки: **Save**, **Run** (запускает бэктест с текущим скриптом), **Close**
- Результаты Run идут в LabBar → Backtest

---

## 5. Итоговая компоновка

```
┌─────────────────────────────────────────────────────────────────┐
│ TopBar: [Депозит | Свободно | В позиции | Вар.маржа]  [Lab] [●] │
├──────────────┬──────────────────────────────┬───────────────────┤
│ RobotsPanel  │       ChartArea              │ InstrumentPanel   │
│              │  [Frame: Robot A]            │                   │
│ [Robot A] ▶  │  ┌────────────────────────┐ │ Коридор цен       │
│   P&L: +5%   │  │ TradingView (OHLC)     │ │ ГО: 12 400 ₽      │
│              │  │ uPlot (тики)           │ │ Экспирация: 17.06 │
│ [Robot B] ▶  │  └────────────────────────┘ │                   │
│   P&L: −1%   │  [Frame: Robot B]            │                   │
│              │  ┌────────────────────────┐ │                   │
│              │  │ ...                    │ │                   │
│              │  └────────────────────────┘ │                   │
├──────────────┴──────────────────────────────┴───────────────────┤
│ [Lab-режим] LabBar: [Strategies] [Backtest] [Scripts]           │
├─────────────────────────────────────────────────────────────────┤
│ BottomBar: события...  [M0●] [M1●] [M2●] [M3●] [M5●] [M7●]    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. MVP-скоуп (этап A+B)

**Этап A — Shell + Operational:**
- TopBar с балансом счёта
- RobotsPanel (статичный список, данные из M8 API)
- ChartFrame с uPlot (тиковый поток из WebSocket)
- InstrumentPanel (данные из M4 Instrument Registry)
- BottomBar со статус-баром сервисов

**Этап B — Lab Manager:**
- Кнопка Lab + LabBar (три вкладки)
- Backtest через Python-скрипт (офлайн JSON)
- CodeEditor с Monaco
- Export/Import роботов в JSON

**Вне скоупа MVP:**
- Визуальный блочный редактор (TSLab-style drag-and-drop)
- Ролевая модель (один пользователь на первом этапе)
- Мобильная версия
