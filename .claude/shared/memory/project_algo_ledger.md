---
name: project-algo-ledger
description: "Журнал алгосделок algo_trades (Postgres) — строгая бухгалтерия по спеке Бори: seq, robot_id, дата-время, trade_num, инструмент, qty, направление, лимит/рынок, gross/net (минус комиссия); отчёты/графики строятся по нему; SMS «Итог вчера» = method ledger"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7525bce9-2c09-4975-8992-6c434c6ffd84
  modified: 2026-07-22T07:44:21.494Z
---

Заказ Бори 2026-07-22: equity-дельта счёта не годится (ручная торговля +
ввод/вывод искажают) — нужен полный пофилловый учёт алготорговли.

**Схема:** `algo_trades` (seq BIGSERIAL — порядковый номер, robot_id, mode
real/paper, ts_ms, trade_num (QUIK), order_num, symbol, side, qty, price,
order_kind market/limit, point_value, pnl_gross_rub, commission_rub,
pnl_net_rub, pos_after, avg_after, dedup_key UNIQUE) + `algo_ledger_state`
(позиция/avg реплея на робота, seeded_at_ms).

**Инжест** (`trader/quik/algo_ledger.py`, таск в app.py lifespan, 30с):
- REAL: тегированные QUIK-сделки из agent-local-status `quik.trades` (факт
  биржи, есть trade_num; тег = robot_id[:20], quikTag). Филлы раннера real
  НЕ инжестятся (дубль). Известный пробел: record-fill-agent (без QUIK-тега).
- PAPER: `recent_fills` зеркала со status=="paper" (int64 приходят СТРОКАМИ
  из MessageToDict!).
- P&L: signed-space avg-cost реплей (идентичен runtime.py: partial reduce
  ДЕРЖИТ avg, flip реализует всё и открывает остаток по цене филла); gross =
  points x coef (params-фид, поле coef); commission_for(taker=True) для обоих
  режимов. Сид: первый раз увиденный робот сидится текущей позицией зеркала,
  филлы старше сида пропускаются (они уже внутри позиции).

**API** (`trader/api/quik_algo_ledger.py`): GET /api/v1/quik/algo-trades
(фильтры robot/mode/symbol/даты МСК, format=csv — UTF-8-BOM, ";") и
GET /algo-report?days=&mode= (по дням: trades/contracts/gross/commission/net/
cum_net + по роботам) — база для графиков.

**SMS/day-close:** hoster ~/stl-day-close.sh пишет ledger_net в ночную запись;
метод результата: ledger (взводится, когда ПРЕДЫДУЩАЯ запись уже имела
ledger_net — гарантия полного покрытия дня) > equity > pnl_sum (~ в SMS).
Первый полный ledger-день — 23.07 (если STL с журналом задеплоен 22.07).

Статус: feat/algo-ledger СМЕРЖЕН и задеплоен 22.07 (7 роботов засеяны, учёт
идёт). График: `EquityChart.svelte` (uPlot; линии cum-net по роботам цветами,
итог жирным, ГО янтарной заливкой, лента-теплокарта дневного net по нижней
кромке; KPI, фильтры 7д/30д/90д/год + real/paper, CSV отчёта и журнала,
таблица по роботам) — панель «Доходность» на главной (кнопка в TopBar) +
компакт-Frame на стенде робота; /algo-report расширен series/ГО/return_pct
(ветка feat/equity-chart). ГО = |поз|×маржа BUYDEPO из health.params зеркала —
появится после публикации агента с margin-relay (коммит a792471 в
fix/tape-replay-gate).
См. [[project-morning-sms-status]], [[reference-commission-model]],
[[reference-agent-robot-pnl]].
