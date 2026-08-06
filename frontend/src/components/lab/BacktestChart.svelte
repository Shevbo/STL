<!-- BacktestChart.svelte
     - candles (dimmed) + per-fill trade triangles placed at the exact fill price
     - dashed connectors: green = long episode, red = short episode (open → FULL close)
     - hover tooltip on a triangle: date/time, price, type (open/average/partial/full N)
     - resting + planned order price lines
     - top-right stats overlay; bottom equity ("График доходности робота")
     - QUIK-style nav: wheel = candle-width zoom, shift+wheel / drag = horizontal pan,
       native time-scale scrollbar; interval selector pinned in a fixed header row
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth, errText } from '../../lib/fetch-auth';
  import { fmtPrice } from '$lib/format';
  import { downloadCSV } from '$lib/csv';
  import {
    toFills, rolledPnl, priceMarkers,
    positionRects, exitStats, tpSlByLevel, commissionBreakdown, commissionFor,
  } from '../../lib/lab-analytics';
  import ScreenTag from './ScreenTag.svelte';
  import ParamPanel from './ParamPanel.svelte';
  import { helpFor } from '$lib/strategy-help';

  let {
    result, symbol, strategy = null, dateFrom, dateTo, pointValue = 1, defaultInterval = 60,
    openOrders = [], plannedOrders = [], taker = true, runParams = {}, paramSchema = [], onRerun = null,
    onApplyParams = null, applyBusy = false, applyMsg = '', sweepHref = null,
    segments = null, pointValues = null, live = 0, liveTick = null, onNet = null, onVm = null, floatRub = null,
    netOverride = null, livePosition = null, journalSuspect = false,
    pointValueKnown = true, closeSeries = null, screenId = '', hideStats = false,
  }: {
    screenId?: string;   // ID окна для дебага (см. ScreenTag)
    // Спрятать рублёвый отчёт «Результат». Нужен ПОРТФЕЛЬНОМУ роботу (team-46): он
    // держит доли портфеля в разных инструментах, и пересчёт филлов ОДНОГО тикера
    // в рубли «как будто это контракты» — враньё. Маркеры на графике остаются.
    hideStats?: boolean;
    // ГОТОВЫЙ ряд реализованных результатов (₽ на момент закрытия) — журнал
    // алготорговли. Когда он есть, кривая доходности строится ПО НЕМУ, а не по
    // пересчёту филлов: журнал знает НАСТОЯЩУЮ комиссию каждой сделки и ведёт
    // позицию непрерывно, а любой пересчёт по цене входа даёт свой дрейф.
    closeSeries?: Array<{ time: number; pnl: number }> | null;
    pointValueKnown?: boolean;
    result: any; symbol: string; strategy?: any; dateFrom: string; dateTo: string;
    pointValue?: number; defaultInterval?: number;
    openOrders?: Array<{ side: string; price: number; qty: number; order_id?: string; role?: string }>;
    plannedOrders?: Array<{ side: string; price: number; qty: number; reason?: string }>;
    // A rolled robot's continuous chart: fetch each contract's bars over its own window
    // and concatenate on the time axis (real prices, a visible step at the roll). fromTs/
    // toTs are in CHART-AXIS time (bar epochs) so bars are filtered to their contract.
    segments?: Array<{ symbol: string; dateFrom: string; dateTo: string; fromTs: number; toTs: number }> | null;
    // Per-contract point values {symbol: pv} for molecule-exact rolled P&L (else pointValue).
    pointValues?: Record<string, number> | null;
    // taker=true → backtest (exchange fee + broker); false → live (maker, broker only).
    taker?: boolean;
    // Editable params panel: current params + their schema (labels) + a re-run callback.
    runParams?: Record<string, any>;
    paramSchema?: Array<{ key: string; label?: string }>;
    onRerun?: ((p: Record<string, any>, dates?: { dateFrom: string; dateTo: string }) => void) | null;
    // Стенд ЖИВОГО робота: у панели параметров должно быть действие «применить к
    // роботу», иначе оператор правит поля, а сохранить нечем (жалоба 29.07).
    // Бэктест передаёт onRerun, живой стенд — onApplyParams. Оба сразу не нужны.
    onApplyParams?: ((p: Record<string, any>) => void) | null;
    /** Ссылка «перебрать параметры этого робота» — стенд знает его id. */
    sweepHref?: string | null;
    applyBusy?: boolean;
    applyMsg?: string;
    // live > 0: refresh the candle TAIL every `live` seconds via series.update()
    // (no setData, zoom preserved) so a LIVE robot's chart moves with the market.
    live?: number;
    // Sub-bar reactivity: freshest tick in CHART-AXIS time ({t: seconds, p: price}).
    // Merged into the FORMING candle via series.update() — the candle breathes with
    // every tick instead of waiting for the next closed bar.
    liveTick?: { t: number; p: number } | null;
    // Fires the authoritative net result (₽, net of commission) so a parent can
    // show the SAME number in a header badge instead of recomputing it.
    onNet?: ((net: number) => void) | null;
    onVm?: ((vm: number) => void) | null;  // ВМ откр. позиции — для панелей родителя
    // Live robot floating (unrealized) P&L on the open position (₽). null = n/a
    // (backtest). Shown in the Результат panel so it isn't seen as frozen.
    floatRub?: number | null;
    // AUTHORITATIVE realized net (₽) supplied by the parent for a LIVE robot: the
    // runner's own realized_pnl × ₽/point. Overrides the tail-replay net, which is
    // WRONG for a robot whose history exceeds the 200-fill mirror tail (the tail
    // starts mid-position, so replaying it from flat mis-attributes the P&L). null
    // = backtest / use the replay.
    netOverride?: number | null;
    // AUTHORITATIVE current position (signed contracts) for a LIVE robot. Same
    // disease as netOverride: when the fill journal is incomplete (200-tail cut,
    // lost fills, fix_state corrections filtered out) the flat-start replay ends
    // on a WRONG position and paints a never-closing "open position" rectangle
    // across days of later trades (incl. their TP/SL exits). When this is set and
    // the replayed end position disagrees, the trailing open rect is suppressed —
    // closed (bounded) rects stay. null = backtest / trust the replay.
    livePosition?: number | null;
    // Recon verdict from the agent: the robot's trades diverge from the QUIK
    // account tables. Book and journal can be CONSISTENTLY wrong together
    // (fills lost before recording — book froze on the same hole), so the
    // livePosition check alone stays silent; recon compares against QUIK truth
    // and catches exactly that case. true → suppress the open rect too.
    journalSuspect?: boolean;
  } = $props();

  // ── Editable parameters panel (collapsed by default; edit → re-run backtest) ──
  let paramsOpen = $state(false);
  let editParams = $state<Record<string, any>>({});
  // Пересинхронизация с приходящими параметрами — но НЕ поверх правки оператора.
  // На стенде живого робота зеркало обновляется каждые 3 секунды, и набранные
  // значения молча затирались: оператор правил поле, жал «Сохранить», ловил
  // ошибку — и его цифры уже подменились текущими (29.07, отказ по 502 при
  // рестарте бэкенда). Пока панель открыта и в ней есть несохранённые изменения,
  // держим ввод оператора.
  let editTouched = $state(false);
  $effect(() => {
    const incoming = { ...(params || {}) };
    if (paramsOpen && editTouched) return;    // не трогаем незаконченную правку
    editParams = incoming;
  });
  $effect(() => { if (!paramsOpen) editTouched = false; });   // закрыли панель — правка сброшена
  // Период исторических данных — тоже редактируемый: пересчёт может идти на
  // другом окне, чем исходный прогон (просьба оператора 2026-07-25).
  let editFrom = $state('');
  let editTo = $state('');
  $effect(() => { editFrom = (dateFrom || '').slice(0, 10); editTo = (dateTo || '').slice(0, 10); });
  // Русские метки: сначала схема стратегии, затем общий словарь модификаторов —
  // открытая из витрины кампании карточка схемы не имеет, и параметры печатались
  // сырыми ключами (bet_max/bet_step, вопрос оператора 2026-07-25).
  const RU_LABELS: Record<string, string> = {
    qty: 'Контрактов на вход', tp_atr: 'Тейк-профит, ×ATR (×10)',
    avg_max: 'Усреднение: макс контрактов', avg_step_atr: 'Усреднение: шаг, ×ATR (×10)',
    avg_atr_n: 'ATR период усреднения', min_gap_pts: 'Разножка: мин. отступ, пункты',
    cooldown_min: 'Остывание после профита, мин', cooldown_pct: 'Остывание: порог профита, %',
    bet_step: 'Ставка: +N конт. после убытка (0=выкл)', bet_max: 'Ставка: потолок добавки, конт.',
    super_y: 'Суперусреднение: +N конт. (0=выкл)', super_z: 'Суперусреднение: макс эскалаций',
    fast: 'Быстрый период', slow: 'Медленный период', signal: 'Сигнальный период',
    period: 'Период', mult: 'Множитель', lookback: 'Окно поиска, баров',
    atr_period: 'ATR период', ema_period: 'EMA период',
    ema1: 'EMA1 (быстрая)', ema2: 'EMA2 (медленная)',
  };
  // Имя параметра: схема прогона -> справочник стратегии (helpFor, тот же источник,
  // что у подсказок фрейма) -> локальный словарь -> голый ключ последним. Без
  // справочника в листе стояли `fast`, `slow`, `qty` вместо человеческих названий.
  const labelFor = (k: string) =>
    paramSchema.find((s) => s.key === k)?.label
    || helpFor(String(strategy || ''), k)?.title
    || RU_LABELS[k] || k;
  const editKeys = $derived(Object.keys(editParams).filter((k) => k !== 'symbol'));
  // ЕДИНЫЙ ФРЕЙМ ПАРАМЕТРОВ. Панель на графике — самое используемое место, где
  // оператор видит и правит параметры, и до 06.08.2026 она была отдельной
  // реализацией: обрезанные подписи, 10px, `true` текстом. Фрейм обязан быть
  // узнаваемым ВЕЗДЕ (реал / paper / бэктест), поэтому здесь тот же ParamPanel и
  // тот же ключ памяти вида, что в редакторе стенда: выбрал «Панель» — она
  // «Панель» на всех экранах.
  let sheetOpen = $state(false);           // широкий лист параметров поверх графика
  // Размер листа тянется мышкой за границы и ЗАПОМИНАЕТСЯ: у каждого свой монитор и
  // своя привычка, а подбирать размер на каждом открытии — работа впустую.
  const PS_SIZE_KEY = 'ps_size';
  let psW = $state<number | null>(null);
  let psH = $state<number | null>(null);
  try {
    const s = JSON.parse(localStorage.getItem(PS_SIZE_KEY) || 'null');
    if (s && s.w > 0) { psW = s.w; psH = s.h || null; }
  } catch { /* приватный режим */ }
  function savePsSize() {
    try { localStorage.setItem(PS_SIZE_KEY, JSON.stringify({ w: psW, h: psH })); }
    catch { /* приватный режим */ }
  }
  /** Тяга за границу. Лист отцентрован, поэтому обе стороны растут одновременно —
      сдвиг указателя удваивается, иначе край «убегает» от курсора вдвое медленнее. */
  function psDrag(e: PointerEvent, axis: 'x' | 'y' | 'xy') {
    e.preventDefault();
    const el = (e.currentTarget as HTMLElement).parentElement as HTMLElement;
    const x0 = e.clientX, y0 = e.clientY;
    const w0 = el.offsetWidth, h0 = el.offsetHeight;
    const maxW = Math.max(320, window.innerWidth - 24);
    const maxH = Math.max(240, window.innerHeight - 24);
    const move = (m: PointerEvent) => {
      if (axis !== 'y') psW = Math.min(maxW, Math.max(360, w0 + (m.clientX - x0) * 2));
      if (axis !== 'x') psH = Math.min(maxH, Math.max(220, h0 + (m.clientY - y0) * 2));
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      savePsSize();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
  function psReset() { psW = null; psH = null; savePsSize(); }
  /** Перенос узла в <body>. Лист параметров лежал внутри контейнера графика и был
      обрезан его рамкой — растянуть по высоте было нельзя. position:fixed тут не
      спасает: любой предок с transform/filter создаёт новый содержащий блок, и
      «фиксированный» элемент снова оказывается в чужих границах. Перенос — приём
      без этой оговорки. */
  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') sheetOpen = false; };
    window.addEventListener('keydown', esc);
    return {
      destroy() { window.removeEventListener('keydown', esc); node.remove(); },
    };
  }
  /** Клавиатурой: стрелки двигают границу на 24px, Shift — на 8px для точной
      подгонки. Тянущаяся граница обязана работать без мыши. */
  function psKey(e: KeyboardEvent, axis: 'x' | 'y' | 'xy') {
    const d = e.shiftKey ? 8 : 24;
    const cur = (e.currentTarget as HTMLElement).parentElement as HTMLElement;
    const w = psW ?? cur.offsetWidth, h = psH ?? cur.offsetHeight;
    if (axis !== 'y' && (e.key === 'ArrowRight' || e.key === 'ArrowLeft'))
      psW = Math.max(360, w + (e.key === 'ArrowRight' ? d : -d));
    else if (axis !== 'x' && (e.key === 'ArrowDown' || e.key === 'ArrowUp'))
      psH = Math.max(220, h + (e.key === 'ArrowDown' ? d : -d));
    else if (e.key === 'Home') psReset();
    else return;
    e.preventDefault();
    savePsSize();
  }
  const VIEW_KEY = 'pe_view';
  let pview = $state<'list' | 'panel'>(
    (() => { try { return localStorage.getItem(VIEW_KEY) === 'panel' ? 'panel' : 'list'; }
             catch { return 'list'; } })());
  function setPview(v: 'list' | 'panel') {
    pview = v;
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* приватный режим */ }
  }
  // Схема для фрейма: поля стратегии ПЛЮС всё, что реально несёт прогон/робот.
  // Ключ вне схемы (exit_only и прочие служебные) обязан быть виден — невидимый
  // параметр нельзя проверить, и именно так 05.08.2026 слетел exit_only.
  const panelSchema = $derived.by(() => {
    const known = (paramSchema || []).filter((s: any) => s.key in editParams || s.key === 'symbol');
    const seen = new Set(known.map((s: any) => s.key));
    // Метка «служебный» значит ОДНО: параметра нет в схеме стратегии, перебор его
    // не тронет (exit_only и подобные). Она осмысленна ТОЛЬКО когда схема известна.
    // В окне Ботстора схема не передаётся, и пометка легла на все поля подряд —
    // метка, стоящая на всём, не значит ничего (06.08.2026).
    const schemaKnown = (paramSchema || []).length > 0;
    const extra = Object.keys(editParams).filter((k) => !seen.has(k)).map((k) => ({
      key: k,
      label: labelFor(k),                 // человеческое имя, а не голый ключ
      type: typeof editParams[k] === 'boolean' ? 'bool' : (k === 'symbol' ? 'text' : 'number'),
      extra: schemaKnown,
    }));
    return [...known.map((s: any) => ({ ...s, label: s.label || labelFor(s.key) })), ...extra];
  });

  function applyParams() {
    if (!onRerun) return;
    const out: Record<string, any> = { ...editParams };
    for (const k of Object.keys(out)) {                     // numeric fields → numbers
      if (typeof params[k] === 'number') out[k] = Number(out[k]);
    }
    onRerun(out, { dateFrom: editFrom, dateTo: editTo });
  }
  const paramsDirty = $derived(
    editKeys.some((k) => String(editParams[k]) !== String((params || {})[k]))
    || editFrom !== (dateFrom || '').slice(0, 10) || editTo !== (dateTo || '').slice(0, 10));

  // ── Избранное: сохранить текущий прогон под своим именем ────────────────────
  let favName = $state('');
  let favMsg = $state('');
  async function saveFavorite() {
    const name = favName.trim();
    if (!name) { favMsg = 'дай имя набору'; return; }
    favMsg = '…';
    try {
      const res = await fetchWithAuth('/api/v1/lab/favorites', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          strategy_id: (strategy && (strategy.id || strategy)) || runParams?.strategy_id || '',
          symbol, params: params || {},
          date_from: editFrom, date_to: editTo,
          run_id: (result && result.run_id) || '',
          net_profit: result?.net_profit ?? null,
        }),
      });
      const d = await res.json().catch(() => ({}));
      favMsg = res.ok ? 'сохранено ⭐' : (d?.detail || 'HTTP ' + res.status);
      if (res.ok) favName = '';
    } catch (e: any) { favMsg = e?.message || 'ошибка'; }
  }

  // Trade triangle colors — distinct teal/rose tonality, brighter than candles.
  const BUY_COLOR = '#2ee6a6';   // teal-green (entry / averaging, buy side)
  const SELL_COLOR = '#ff5c8a';  // rose-red (entry / averaging, sell side)
  const TP_COLOR = '#19e36a';    // bright green — closing fill in profit (take-profit)
  const SL_COLOR = '#ff3b3b';    // bright red — closing fill in loss (stop-loss)

  let containerEl: HTMLDivElement;
  let candleEl: HTMLDivElement;
  let equityEl: HTMLDivElement;
  let scrollTrackEl: HTMLDivElement;
  let roRef: ResizeObserver | null = null;

  let tvCandle: any = null, tvEquity: any = null;
  let candleSeries: any = null, volumeSeries: any = null;
  let longSeries: any = null, shortSeries: any = null, equitySeries: any = null;
  let buyMarkSeries: any = null, sellMarkSeries: any = null;
  let orderPriceLines: any[] = [];
  let markIndex: Array<{ time: number; price: number; side: 'buy' | 'sell'; label: string; rawTime: number; close?: any }> = [];

  // ── Кластеризация стрелок сделок ──────────────────────────────────────────
  // Сотни стрелок на ширину экрана рябят и не читаются. Когда видимых маркеров
  // больше порога (localStorage stl_cluster_threshold, по умолчанию 50), экран
  // делится на колонки ~30px и стрелки одной колонки и стороны схлопываются в
  // одну с подписью ×N. Зум внутрь — кластеры распадаются на живые стрелки.
  const CLUSTER_PX = 30;
  function clusterThreshold(): number {
    const v = Number(localStorage.getItem('stl_cluster_threshold') || 50);
    return Number.isFinite(v) && v >= 5 ? v : 50;
  }
  let allMarkers: any[] = [];
  let markerBarTimes: number[] = [];
  let markerApplyTimer: ReturnType<typeof setTimeout> | null = null;
  function scheduleMarkerApply() {
    if (markerApplyTimer) clearTimeout(markerApplyTimer);
    markerApplyTimer = setTimeout(applyMarkers, 120);
  }
  function barIdxByTime(t: number): number {
    let lo = 0, hi = markerBarTimes.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (markerBarTimes[m] < t) lo = m + 1; else hi = m; }
    return lo;
  }
  function applyMarkers() {
    if (!tvCandle || !candleSeries) return;
    if (!allMarkers.length || !markerBarTimes.length) { candleSeries.setMarkers(allMarkers); return; }
    let lr: any = null;
    try { lr = tvCandle.timeScale().getVisibleLogicalRange(); } catch { /* not ready */ }
    if (!lr) { candleSeries.setMarkers(allMarkers); return; }
    const from = Math.max(0, Math.floor(lr.from));
    const to = Math.min(markerBarTimes.length - 1, Math.ceil(lr.to));
    if (to <= from) { candleSeries.setMarkers(allMarkers); return; }
    const t0 = markerBarTimes[from], t1 = markerBarTimes[to];
    const visible = allMarkers.filter((m) => m.time >= t0 && m.time <= t1);
    if (visible.length <= clusterThreshold()) { candleSeries.setMarkers(allMarkers); return; }
    // Ширина колонки в барах: ширина шкалы / 30px.
    let widthPx = 900;
    try { widthPx = tvCandle.timeScale().width() || widthPx; } catch { /* оставим дефолт */ }
    const cols = Math.max(1, Math.floor(widthPx / CLUSTER_PX));
    const barsPerCol = Math.max(1, Math.ceil((to - from + 1) / cols));
    const groups = new Map<string, any[]>();
    for (const m of visible) {
      const col = Math.floor((barIdxByTime(m.time) - from) / barsPerCol);
      const key = col + '|' + m.position;      // выше/ниже бара кластеруем раздельно
      const g = groups.get(key);
      if (g) g.push(m); else groups.set(key, [m]);
    }
    const out: any[] = [];
    for (const arr of groups.values()) {
      if (arr.length === 1) { out.push(arr[0]); continue; }
      const mid = arr[Math.floor(arr.length / 2)];
      out.push({ time: mid.time, position: mid.position, shape: mid.shape,
                 color: mid.color, text: '×' + arr.length });
    }
    out.sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(out);
  }
  let syncReady = false;

  let loading = $state(true);
  let error = $state('');
  let stats = $state<any>(null);
  let exits = $state<any>(null);   // TP/SL exit analytics
  let levelStats = $state<any[]>([]);   // TP/SL distribution by peak averaging level
  let commission = $state<any>(null);   // broker/exchange commission breakdown
  let netResult = $state(0);            // Σ realized close PnL (₽, net of commission)
  // ВМ открытой позиции (правило оператора 26.07: фин.рез ВЕЗДЕ = фикс + варьмаржа,
  // «+0 ₽ при 10 продажах в рынке» — враньё). floatRub от родителя приоритетен.
  let vmOpen = $state(0);
  // Живой робот: сколько реализовано ДО первой сделки в журнале (200-хвост не
  // достаёт дальше). Не null → кривая стартует не с нуля, и подпись это объясняет.
  let equityCarry = $state<{ rub: number; fromTs: number } | null>(null);
  // Живой робот без журнала: кривую не рисуем и говорим об этом прямо.
  let equityBlind = $state(false);
  // Сделок в результате НЕТ вовсе. У свипа на много комбинаций так и есть: массивы
  // сделок и equity вырезаются при записи (иначе Postgres на VDS и nginx не тянут),
  // и панель рисовала пустую ось от −6 до 7 — будто данные есть, просто их не видно.
  // Пустая ось это нарисованная неправда; лучше сказать словами.
  const equityEmpty = $derived(!(result?.trades?.length));
  // Сколько закрытых сделок легло ЗА пределами загруженных баров (источник баров графика
  // короче теста) — их P&L сжат в последнюю точку; предупреждаем оператора.
  let equityTailBeyond = $state(0);
  let statsExpanded = $state(false);    // report collapsed to 2 lines by default
  let showTrades = $state(false);       // trades-table overlay
  let tradeRows = $state<any[]>([]);    // per-trade rows for the table

  // Resizable P&L pane (operator: «слепой и очень узкий»). Drag the divider to
  // grow the equity field; persisted so it survives reloads. The ResizeObserver
  // already re-sizes the chart to the div's clientHeight.
  let equityPx = $state<number>(Number(localStorage.getItem('bt_equity_px')) || 240);
  let eqResizing = false;
  function startEqResize(ev: PointerEvent) {
    eqResizing = true;
    (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId);
    ev.preventDefault();
  }
  function moveEqResize(ev: PointerEvent) {
    if (!eqResizing) return;
    // dragging UP grows the pane: delta = how far the pointer is above the divider.
    const root = (ev.currentTarget as HTMLElement).closest('.bt-root') as HTMLElement | null;
    if (!root) return;
    const rb = root.getBoundingClientRect();
    equityPx = Math.max(90, Math.min(rb.height - 180, rb.bottom - ev.clientY - 34));
  }
  function endEqResize(ev: PointerEvent) {
    if (!eqResizing) return;
    eqResizing = false;
    try { localStorage.setItem('bt_equity_px', String(Math.round(equityPx))); } catch {}
  }
  let crossLabel = $state('');
  // "LIVE ПОТОКА" marker: x-pixel of the newest bar (where the live stream feeds the
  // chart). null = off-screen/hidden. Repositioned on pan/zoom and each live tail.
  let liveLineX = $state<number | null>(null);
  function positionLiveLine() {
    const x = lastBarTime ? tvCandle?.timeScale().timeToCoordinate(lastBarTime as any) : null;
    liveLineX = (x != null && x >= 0) ? x : null;
  }
  let resampleMin = $state(defaultInterval);
  let margin = $state<number | null>(null);
  let tip = $state<{ x: number; y: number; head?: string; headKind?: 'tp' | 'sl' | 'neutral'; lines: string[] } | null>(null);

  // Custom horizontal scrollbar (lightweight-charts has no scrollbar widget).
  // Works in LOGICAL (bar-index) space — the chart pans/zooms by bar index, and
  // bars are NOT evenly spaced in time (gaps/weekends), so a time-based thumb
  // distorts the window. barCount = total bars; thumb maps over [0, barCount].
  let barCount = 0;
  let scrollThumb = $state({ left: 0, width: 100 });     // percent
  let draggingBar = false, dragStartX = 0, dragStartLeft = 0;

  const INTERVALS = [
    { label: '1м', v: 1 }, { label: '5м', v: 5 }, { label: '15м', v: 15 },
    { label: '30м', v: 30 }, { label: '1ч', v: 60 }, { label: '2ч', v: 120 },
    { label: '4ч', v: 240 }, { label: '12ч', v: 720 }, { label: '1д', v: 1440 },
  ];
  function pickInterval(v: number) { if (v !== resampleMin) { resampleMin = v; loadData(); } }

  let params = $derived(
    (result?.params && typeof result.params === 'object') ? result.params
      : (typeof result?.params === 'string' ? JSON.parse(result.params)
         : (runParams || {}))
  );

  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  const fmtRub = (v: number) => Math.round(v).toLocaleString('ru-RU') + ' ₽';
  // Commission for one fill, using this chart's instrument + taker/maker mode.
  const commissionForFill = (price: number, qty: number) => commissionFor(symbol, price, qty, pointValue, taker);
  // pointValue = 1 может быть НАСТОЯЩИМ коэффициентом (GZ/Si/SR: пункт = 1 ₽) или
  // заглушкой «коэффициент неизвестен» — различает только явный pointValueKnown.
  const unitLabel = $derived(pointValueKnown === false ? 'пункты' : '₽');
  const KIND_RU: Record<string, string> = {
    open: 'Открытие', average: 'Усреднение', enforce: 'Усиление', partial: 'Част. закрытие',
    full: 'Полн. закрытие', reverse: 'Реверс',
  };
  // Bar epochs carry Moscow wall-clock stamped as UTC, so format in UTC to match axis.
  // Chart-axis epochs here are MSK WALL-CLOCK stamped as UTC (the /api/v1/market/bars
  // loader stamps the Moscow ISS time as UTC; RobotWindow shifts fills +3h onto the same
  // grid). So rendering these epochs AS UTC yields the correct MSK wall-clock — do NOT
  // apply another Europe/Moscow / +3h shift (that double-counts and pushed every time 3h
  // ahead). The axis default formatter also renders UTC, so axis/crosshair/tooltip/table
  // all agree.
  const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const fmtDay = (ts: number) => new Date(ts * 1000).toLocaleDateString('ru-RU', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', year: '2-digit',
  });
  let periodLabel = $state('');   // actual loaded data span, shown in the header

  // Reset zoom to show the WHOLE test period (all bars) on screen.
  function fitAll() {
    try { tvCandle?.timeScale().fitContent(); } catch { /* not ready */ }
    scheduleRects();   // если видимый диапазон не изменился, событие не придёт
  }

  // Readable default view: the newest bars at >= MIN_CANDLE_PX per candle, so a
  // candle is a candle (not a hairline) and markers spread out instead of stacking.
  const MIN_CANDLE_PX = 6;
  function showTail(total: number) {
    try {
      const w = candleEl?.clientWidth || 1200;
      const want = Math.max(60, Math.floor(w / MIN_CANDLE_PX));
      if (total <= want) { tvCandle.timeScale().fitContent(); return; }
      tvCandle.timeScale().setVisibleLogicalRange({ from: total - want, to: total - 1 });
    } catch { try { tvCandle?.timeScale().fitContent(); } catch { /* not ready */ } }
  }

  async function loadMeta() {
    try {
      const res = await fetchWithAuth(`/api/v1/instruments/${encodeURIComponent(symbol)}/meta`);
      if (res.ok) { const m = await res.json(); margin = m?.initial_margin ?? null; }
    } catch { margin = null; }
  }

  onMount(async () => {
    const { createChart, LineStyle } = await import('lightweight-charts');
    const chartOpts = {
      layout: { background: { color: '#0a0a15' }, textColor: '#666' },
      grid: { vertLines: { color: '#15152470' }, horzLines: { color: '#15152470' } },
      // fixLeftEdge/fixRightEdge clamp panning+zoom to the data so there are never
      // empty gaps on the left/right when you zoom out — data always fills the view.
      timeScale: {
        // rightOffset (set from bar count in loadData) reserves ~7% width on the
        // right so the newest arrows aren't jammed under the price-axis order labels.
        // fixRightEdge OFF so that reserved gap actually shows.
        borderColor: '#2d2d4a', timeVisible: true, rightOffset: 0,
        fixLeftEdge: true, fixRightEdge: false,
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d2d4a', minimumWidth: 84 },
      // QUIK-like: wheel zooms candle width; click-drag pans horizontally. Only the
      // price axis rescales on drag (never the chart body / time axis).
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: {
        mouseWheel: true, pinch: true,
        axisPressedMouseMove: { time: false, price: true },
        axisDoubleClickReset: true,
      },
    };

    // Top chart is the ONLY interactive one (перетаскивание/зум — здесь), но шкала
    // времени у него СВОЯ. Раньше её прятали, оставляя одну ось внизу под кривой
    // доходности: в реальном окне блок графика (68vh) не влезает целиком, оператор
    // смотрит на свечи — а под ними ни одной даты. Свечи обязаны быть само-читаемы.
    // Оси не разъезжаются: обеим шкалам цены задан один minimumWidth (см. chartOpts).
    tvCandle = createChart(candleEl, {
      ...chartOpts,
      width: candleEl.clientWidth || 600, height: candleEl.clientHeight || 280,
    });
    // Candles dimmed further (~15% more, toward background) so triangles dominate.
    candleSeries = tvCandle.addCandlestickSeries({
      upColor: '#155a33', downColor: '#69241d',
      borderUpColor: '#1d6e40', borderDownColor: '#7d2a22',
      wickUpColor: '#1d6e40', wickDownColor: '#7d2a22',
    });
    volumeSeries = tvCandle.addHistogramSeries({ priceScaleId: 'vol', color: '#4caf5018', priceFormat: { type: 'volume' } });
    tvCandle.priceScale('vol').applyOptions({ scaleMargins: { top: 0.9, bottom: 0 } });

    longSeries = tvCandle.addLineSeries({
      color: '#2ee6a6', lineWidth: 1, lineStyle: LineStyle.Dashed,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    shortSeries = tvCandle.addLineSeries({
      color: '#ff5c8a', lineWidth: 1, lineStyle: LineStyle.Dashed,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });

    const markAnchor = {
      lineVisible: false, pointMarkersVisible: false,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    };
    buyMarkSeries = tvCandle.addLineSeries(markAnchor);
    sellMarkSeries = tvCandle.addLineSeries(markAnchor);

    // Equity chart shows the visible time axis but is NON-interactive: it only
    // mirrors the candle chart's range (one-way). Making it interactive created a
    // two-way sync loop that drifted the candle width while dragging.
    tvEquity = createChart(equityEl, {
      ...chartOpts,
      handleScroll: false, handleScale: false,
      width: equityEl.clientWidth || 600, height: equityEl.clientHeight || 150,
    });
    equitySeries = tvEquity.addBaselineSeries({
      baseValue: { type: 'price', price: 0 },
      // Solid green field above 0 (operator: TSLab-style «залитое зелёное поле»),
      // red field below. Strong fill + a 2px line so the curve is not «слепой».
      topLineColor: '#00e676', topFillColor1: '#00e67688', topFillColor2: '#00e67618',
      bottomLineColor: '#ff5252', bottomFillColor1: '#ff525218', bottomFillColor2: '#ff525288',
      lineWidth: 2, priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });

    // Crosshair: time label + trade tooltip if hovering near a triangle.
    const onCross = (p: any) => {
      crossLabel = (p && p.time) ? fmtTs(p.time) : '';
      hitTestTooltip(p);
    };
    tvCandle.subscribeCrosshairMove(onCross);
    tvEquity.subscribeCrosshairMove((p: any) => { crossLabel = (p && p.time) ? fmtTs(p.time) : ''; });

    // ONE-WAY sync: equity mirrors the candle chart's logical range. Logical (not
    // time) range avoids drift from uneven bar spacing, and one-way avoids the
    // feedback loop that rescaled candle width during a drag.
    tvCandle.timeScale().subscribeVisibleLogicalRangeChange((lr: any) => {
      if (!lr) return;
      try { tvEquity.timeScale().setVisibleLogicalRange(lr); } catch { /* transient */ }
      if (syncReady && !draggingBar) updateThumb(lr);
      // МИРАЖ-фикс (26.07): после fitContent автоскейл цены доезжает на СЛЕДУЮЩЕМ
      // кадре; одиночный синхронный updateRects брал старую шкалу и растягивал
      // боксы позиций в «свечи-призраки» над графиком («Весь период» на 1ч).
      // scheduleRects гонит пересчёт несколько кадров, пока шкала не устаканится.
      scheduleRects();
      positionLiveLine();
      scheduleMarkerApply();   // пере-кластеризация стрелок под новый зум
    });

    // Shift+wheel = horizontal pan (QUIK). Plain wheel is left to handleScale (zoom).
    candleEl.addEventListener('wheel', onWheelPan, { passive: false });
    equityEl.addEventListener('wheel', onWheelPan, { passive: false });

    const ro = new ResizeObserver(() => {
      // Elements may be gone if the window closed mid-resize — guard against null.
      if (candleEl) tvCandle?.applyOptions({ width: candleEl.clientWidth, height: candleEl.clientHeight });
      if (equityEl) tvEquity?.applyOptions({ width: equityEl.clientWidth, height: equityEl.clientHeight });
      scheduleRects();   // recompute the position-box overlay over a few frames (coords settle after resize)
    });
    // Observe the PANES, not just the root: dragging the divider changes the
    // candle/equity split without changing the root's size, so a root-only observer
    // never fired and the chart canvas kept its stale height (the «некорректно при
    // сдвиге границы» glitch).
    ro.observe(containerEl);
    if (candleEl) ro.observe(candleEl);
    if (equityEl) ro.observe(equityEl);
    roRef = ro;

    await loadMeta();
    await loadData();
    if (live > 0) liveTimer = setInterval(refreshTail, Math.max(5, live) * 1000);
  });

  function onWheelPan(ev: WheelEvent) {
    if (!ev.shiftKey || !tvCandle) return;
    ev.preventDefault();
    const ts = tvCandle.timeScale();
    const pos = ts.scrollPosition();
    // 1 wheel notch ≈ 3 bars; shift sign so wheel-down scrolls forward in time.
    ts.scrollToPosition(pos + (ev.deltaY > 0 ? -3 : 3), false);
  }

  // Reflect the visible window as a thumb over [0, barCount] in LOGICAL space.
  // The logical range can extend past the data (rightOffset, partial bars), so
  // clamp into the data bounds before mapping to percent.
  function updateThumb(lr: { from: number; to: number }) {
    if (barCount <= 0) { scrollThumb = { left: 0, width: 100 }; return; }
    const from = Math.max(0, lr.from);
    const to = Math.min(barCount, lr.to);
    const left = Math.max(0, (from / barCount) * 100);
    const width = Math.min(100 - left, ((to - from) / barCount) * 100);
    scrollThumb = { left, width: Math.max(2, width) };
  }

  // Drag the scrollbar thumb → shift the visible LOGICAL range across the bars.
  function onBarDown(ev: PointerEvent) {
    draggingBar = true; dragStartX = ev.clientX; dragStartLeft = scrollThumb.left;
    try { (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId); } catch { /* no active pointer */ }
  }
  function onBarMove(ev: PointerEvent) {
    if (!draggingBar || !scrollTrackEl || !tvCandle || barCount <= 0) return;
    const trackW = scrollTrackEl.clientWidth || 1;
    const dPct = ((ev.clientX - dragStartX) / trackW) * 100;
    const newLeft = Math.max(0, Math.min(100 - scrollThumb.width, dragStartLeft + dPct));
    // Keep the current window WIDTH (zoom) constant; only move its start bar.
    const winBars = (scrollThumb.width / 100) * barCount;
    const fromBar = (newLeft / 100) * barCount;
    try {
      tvCandle.timeScale().setVisibleLogicalRange({ from: fromBar, to: fromBar + winBars });
    } catch { /* transient */ }
    scrollThumb = { ...scrollThumb, left: newLeft };
  }
  function onBarUp(ev: PointerEvent) {
    draggingBar = false;
    try { (ev.target as HTMLElement).releasePointerCapture?.(ev.pointerId); } catch { /* no active pointer */ }
  }

  const fmtDur = (secs: number) => {
    const m = Math.round(secs / 60);
    if (m < 60) return `${m} мин`;
    const h = Math.floor(m / 60), mm = m % 60;
    if (h < 24) return `${h} ч ${mm} мин`;
    const d = Math.floor(h / 24);
    return `${d} дн ${h % 24} ч`;
  };

  function hitTestTooltip(p: any) {
    if (!p || !p.point || p.time == null) { tip = null; return; }
    const ts = tvCandle.timeScale();
    const px = p.point.x, py = p.point.y;

    // 1) nearest trade triangle to the cursor in BOTH axes. Many fills land on one bar
    // (arrows stack at the same X); the old X-only pick returned a NEIGHBOUR's event, so
    // the tooltip's side/price/time disagreed with the trade log. Score by dx+dy within
    // thresholds so the arrow actually under the cursor wins.
    let best: any = null, bestScore = Infinity, bestMy: number | null = null;
    for (const m of markIndex) {
      const mx = ts.timeToCoordinate(m.time);
      if (mx == null) continue;
      const dx = Math.abs(mx - px);
      if (dx > 11) continue;
      const my = candleSeries.priceToCoordinate(m.price);
      if (my == null) continue;
      // ponytail: an 'inBar' arrow renders offset from its price coordinate, so the
      // hover point never sat on the visual arrow (operator had to hunt below it).
      // Widen the vertical catch to the arrow's rendered height instead of chasing
      // lightweight-charts' exact marker geometry (not exposed).
      const dy = Math.abs(my - py);
      if (dy > 24) continue;
      const score = dx + dy;
      if (score < bestScore) { bestScore = score; best = m; bestMy = my; }
    }
    if (best) {
        let head: string, headKind: 'tp' | 'sl' | 'neutral' = 'neutral';
        const lines: string[] = [];
        if (best.close) {
          // Exit fills: make the TP/SL type the headline so it's unmistakable.
          head = best.close.exitLabel;                 // "Частичный TP · ..." etc.
          headKind = best.close.exit === 'TP' ? 'tp' : 'sl';
          lines.push(best.label);                      // "Полн. закрытие N (всего в поз. V)"
          lines.push(`${best.side === 'buy' ? 'Покупка' : 'Продажа'} @ ${fmtPrice(best.price)}`);
          lines.push(fmtTs(best.rawTime));
          lines.push(`В позиции: ${fmtDur(best.close.holdSecs)}`);
          lines.push(`Макс. контрактов: ${best.close.maxContracts}`);
          lines.push(`Фин. результат: ${fmtMoney(best.close.pnl)} ₽`);
        } else {
          // Entry / averaging fills.
          head = best.label;
          lines.push(`${best.side === 'buy' ? 'Покупка' : 'Продажа'} @ ${fmtPrice(best.price)}`);
          lines.push(fmtTs(best.rawTime));
        }
        if (best.id) lines.push(`ID: ${best.id}`);
        tip = { x: ts.timeToCoordinate(best.time) ?? px, y: bestMy ?? py, head, headKind, lines };
        return;
    }

    // 2) order / planned price line near the cursor (y within 6px)?
    for (const li of lineIndex) {
      const ly = candleSeries.priceToCoordinate(li.price);
      if (ly != null && Math.abs(ly - py) <= 6) {
        tip = { x: px, y: ly, lines: [li.text] };
        return;
      }
    }

    // 3) inside a position rectangle that is too small for an inline P&L label?
    for (const r of rectPx) {
      if (r.showLabel) continue;   // big enough: P&L is already drawn in the box
      if (px >= r.left && px <= r.left + r.width && py >= r.top && py <= r.top + r.height) {
        tip = {
          x: r.left + r.width / 2, y: r.top + r.height / 2,
          head: `${r.dir === 'long' ? 'Лонг' : 'Шорт'}${r.open ? ' (открыта)' : ''}`,
          headKind: r.pnl >= 0 ? 'tp' : 'sl',
          lines: [`Результат сделки: ${fmtMoney(r.pnl)} ₽`],
        };
        return;
      }
    }
    tip = null;
  }

  onDestroy(() => {
    cancelAnimationFrame(rectRaf);
    if (liveTimer) clearInterval(liveTimer);
    roRef?.disconnect();
    candleEl?.removeEventListener('wheel', onWheelPan);
    equityEl?.removeEventListener('wheel', onWheelPan);
    tvCandle?.remove(); tvEquity?.remove();
  });

  // ── Live tail: append/refresh the newest candles WITHOUT setData ────────────
  // series.update() mutates the last bar / appends new ones, so pan/zoom and the
  // visible range survive. Dates are computed at refresh time (a tab left open
  // across midnight keeps moving). Fetch window = today±1d, cheap on the server.
  let lastBarTime = 0;                 // newest bar currently on the chart
  let tailBar: any = null;             // full OHLCV of the newest bar (tick merge base)
  let lastEquityValue = 0;             // last cumulative P&L, carried onto appended bars
  let liveTimer: ReturnType<typeof setInterval> | null = null;
  let tailBusy = false;

  // Merge one tick into the forming candle. Server bars stay authoritative
  // (refreshTail overwrites); ticks only extend high/low/close between refreshes.
  function applyLiveTick(t: number, p: number) {
    if (!candleSeries || !lastBarTime || p <= 0 || resampleMin <= 0) return;
    const bucket = resampleMin * 60;
    const m = Math.floor(t / bucket) * bucket;
    if (m < lastBarTime) return;                 // tick from an already-closed bar
    if (!tailBar || m > tailBar.time) {
      if (tailBar && m > tailBar.time) {
        equitySeries?.update({ time: m, value: lastEquityValue });
        barCount += 1;
      }
      tailBar = { time: m, open: p, high: p, low: p, close: p, volume: 0 };
    } else {
      tailBar.high = Math.max(tailBar.high, p);
      tailBar.low = Math.min(tailBar.low, p);
      tailBar.close = p;
    }
    lastBarTime = tailBar.time;
    candleSeries.update({ time: tailBar.time, open: tailBar.open, high: tailBar.high,
                          low: tailBar.low, close: tailBar.close });
  }

  $effect(() => {
    const lt = liveTick;
    if (lt && lt.p > 0 && syncReady) applyLiveTick(lt.t, lt.p);
  });

  async function refreshTail() {
    if (!live || loading || tailBusy || !candleSeries || !lastBarTime) return;
    tailBusy = true;
    try {
      // Window = TODAY only: the tail endpoint does an ISS top-up server-side, so a
      // wide window on a fast cadence starved the backend event loop (the robots
      // mirror timed out behind it). Ticks animate the forming candle at 1s anyway.
      const now = new Date();
      const isoD = (d: Date) => d.toISOString().slice(0, 10);
      const from = isoD(now);
      const to = isoD(new Date(now.getTime() + 86400_000));
      const fresh = await fetchBars(symbol, from, to);
      let appended = false;
      for (const b of fresh) {
        if (b.time < lastBarTime) continue;
        candleSeries.update({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close });
        volumeSeries.update({ time: b.time, value: b.volume, color: b.close >= b.open ? '#26a65b20' : '#c0392b20' });
        if (b.time > lastBarTime) {
          equitySeries.update({ time: b.time, value: lastEquityValue });
          lastBarTime = b.time;
          barCount += 1;
          appended = true;
        }
        tailBar = { ...b };            // server bar = authoritative tick-merge base
      }
      if (appended) { periodLabel = `${periodLabel.split(' — ')[0]} — ${fmtDay(lastBarTime)}`; positionLiveLine(); }
    } catch { /* transient network error — next tick retries */ }
    tailBusy = false;
  }

  async function fetchBars(sym: string, from: string, to: string): Promise<any[]> {
    const res = await fetchWithAuth(
      `/api/v1/market/bars?symbol=${encodeURIComponent(sym)}&date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}&resample_min=${resampleMin}`
    );
    if (!res.ok) throw new Error(await errText(res));
    return await res.json();
  }

  // LIVE mode: the history window scales with the interval — 3 days of 1m candles
  // is fine, but 30m/1h/4h need weeks-months or the chart "starts at 01.07".
  // Backtest charts keep their exact requested period (dateFrom untouched).
  function liveWindow(): { from: string; to: string } {
    const isoD = (t: number) => new Date(t).toISOString().slice(0, 10);
    if (!live) {
      // Окно баров ОБЯЗАНО покрывать сделки результата. Избранное/лидер мог прийти с
      // коротким/чужим окном дат (editFrom/editTo), а бары тянутся по нему — тогда часть
      // сделок вылезает за бары, и привязка кривой к итогу переворачивает её в ±млн.
      // Расширяем запрос до реального диапазона сделок (время сделки — unix-сек).
      let from = dateFrom, to = dateTo;
      const fills = toFills(result?.trades);
      if (fills.length) {
        const tMin = isoD(Math.min(...fills.map((f: any) => f.time)) * 1000);
        const tMax = isoD((Math.max(...fills.map((f: any) => f.time)) + 86400) * 1000);
        if (tMin < from) from = tMin;
        if (tMax > to) to = tMax;
      }
      return { from, to };
    }
    const days = resampleMin >= 720 ? 730 : resampleMin >= 240 ? 365
      : resampleMin >= 120 ? 180 : resampleMin >= 60 ? 120
      : resampleMin >= 30 ? 60 : resampleMin >= 15 ? 30
      : resampleMin >= 5 ? 14 : 4;
    const from = isoD(Date.now() - days * 86400_000);
    const to = isoD(Date.now() + 86400_000);   // recomputed per load: long-lived tabs keep moving
    return { from: from < dateFrom ? from : dateFrom, to: to > dateTo ? to : dateTo };
  }

  // Continuous chart: for a rolled robot, fetch each contract's bars over its own
  // window (fromTs..toTs in chart-axis time) and concatenate — real prices, a visible
  // step at the roll, no overlap. Single-contract (backtest) falls back to one fetch.
  async function loadBars(): Promise<any[]> {
    if (segments && segments.length > 1) {
      const all: any[] = [];
      for (const seg of segments) {
        const part = await fetchBars(seg.symbol, seg.dateFrom, seg.dateTo);
        // Помним КОНТРАКТ каждого бара: ВМ открытой позиции нельзя считать по
        // цене чужого контракта (27.07: последним баром оказался мёртвый RIM6
        // @105 250, средняя позиции — RIU6 @90 005, и стенд нарисовал −261 715 ₽).
        for (const b of part) if (b.time >= seg.fromTs && b.time < seg.toTs) all.push({ ...b, sym: seg.symbol });
      }
      all.sort((a, b) => a.time - b.time);
      const out: any[] = []; let lastT = -Infinity;
      for (const b of all) if (b.time !== lastT) { out.push(b); lastT = b.time; }   // unique ascending times
      return out;
    }
    const w = liveWindow();
    const rows = await fetchBars(symbol, w.from, w.to);
    return rows.map((b: any) => ({ ...b, sym: symbol }));
  }

  // Position rectangles (chart-axis time → pixels), recomputed on every pan/zoom so the
  // boxes track the candles. X uses timeToCoordinate — the SAME mapping lightweight-charts
  // uses for the markers — so a box aligns with its own arrows at every timeframe. (A
  // bar-index mapping drifts: the time scale's logical space also includes the marker
  // anchor points, so a candle index != a logical index, and the gap changes per interval.)
  // Off-screen ends are clamped to the edge so partly-visible boxes still draw (container clips).
  let posRects: any[] = [];
  let rectPx = $state<Array<{ left: number; top: number; width: number; height: number;
    dir: string; open: boolean; pnl: number; label: string; showLabel: boolean }>>([]);
  function updateRects() {
    if (!tvCandle || !candleSeries || !posRects.length) { rectPx = []; return; }
    const ts = tvCandle.timeScale();
    const vr: any = ts.getVisibleRange();              // { from, to } in chart-axis time, or null
    const W = candleEl?.clientWidth ?? 1200;
    const xFor = (t: number): number | null => {
      const c = ts.timeToCoordinate(t as any);
      if (c != null) return c as number;
      if (!vr) return null;
      return t < (vr.from as number) ? -20 : W + 20;   // off-screen → clamp to the correct edge
    };
    const out: any[] = [];
    for (const r of posRects) {
      const x1 = xFor(r.tIn), x2 = xFor(r.tOut);
      const y1 = candleSeries.priceToCoordinate(r.pIn);
      const y2 = candleSeries.priceToCoordinate(r.pOut);
      if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
      const left = Math.min(x1, x2), top = Math.min(y1, y2);
      const width = Math.max(2, Math.abs(x2 - x1)), height = Math.max(2, Math.abs(y2 - y1));
      const label = `${r.pnl >= 0 ? '+' : ''}${Math.round(r.pnl).toLocaleString('ru-RU')} ₽`;
      // Inline label only when there is room; otherwise it shows on hover (hitTest).
      out.push({ left, top, width, height, dir: r.dir, open: !!r.open, pnl: r.pnl,
                 label, showLabel: width >= label.length * 6 + 6 && height >= 13 });
    }
    rectPx = out;
  }

  // Reposition the boxes across the next few animation frames. On the FIRST render,
  // priceToCoordinate() is still mapping against the pre-autoscale price range (fitContent
  // + the price-scale autoscale only take effect on the next paint), so a single
  // synchronous updateRects() placed the boxes on stale Y coordinates — they "floated"
  // away from the candles until a manual zoom re-ran updateRects. Running it over ~6
  // frames lets the scale settle so the boxes lock onto the candles immediately.
  let rectRaf = 0;
  function scheduleRects(times = 6) {
    cancelAnimationFrame(rectRaf);
    let n = 0;
    const tick = () => {
      updateRects();
      if (++n < times) rectRaf = requestAnimationFrame(tick);
    };
    rectRaf = requestAnimationFrame(tick);
  }

  // Preserve the operator's zoom/pan across LIVE reloads: on the live stand a
  // new robot fill recreates `result` -> loadData -> a full view reset every
  // time made the chart unusable. Keep the visible range when reloading the
  // SAME symbol at the SAME interval; refit only on first load / a different
  // instrument / an interval switch (resampling changes logical indexing).
  let _viewKey = '';
  async function loadData() {
    loading = true; error = ''; syncReady = false;
    const viewKey = `${symbol}:${resampleMin}`;
    const keepRange = (viewKey === _viewKey)
      ? tvCandle?.timeScale().getVisibleLogicalRange() : null;
    try {
      const bars: any[] = await loadBars();
      if (!bars.length) { error = `Нет данных для ${symbol}. Загрузите через "Load from ISS".`; loading = false; return; }

      candleSeries.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
      volumeSeries.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? '#26a65b20' : '#c0392b20' })));
      barCount = bars.length;
      periodLabel = `${fmtDay(bars[0].time)} — ${fmtDay(bars[bars.length - 1].time)}`;
      lastBarTime = bars[bars.length - 1].time;
      tailBar = { ...bars[bars.length - 1] };

      const fills = toFills(result?.trades);
      // Roll-aware: P&L summed PER CONTRACT with each contract's own point value (a
      // single-contract backtest collapses to a plain replay). taker → backtest fee
      // model (exchange+broker), maker → live (broker only).
      const pvArg = (pointValues && Object.keys(pointValues).length) ? pointValues : pointValue;
      const rolled = rolledPnl(fills, pvArg, taker, { bucketSecs: resampleMin * 60 });
      const events = rolled.events;

      // triangles at exact fill price + hover index; bright entry / dim AVG / TP-SL close
      const pm = priceMarkers(events, { buy: BUY_COLOR, sell: SELL_COLOR, tp: TP_COLOR, sl: SL_COLOR });
      exits = exitStats(events);
      levelStats = tpSlByLevel(events);
      // Attach ALL markers to the CANDLE series so lightweight-charts snaps each to its
      // containing bar. Feeding them to separate line series via setData added off-grid
      // time points to the shared (index-based) time scale, which floated the arrows AND
      // the P&L boxes away from the candles. Keep the anchor series empty.
      buyMarkSeries.setData([]);
      sellMarkSeries.setData([]);
      allMarkers = [...pm.buy.markers, ...pm.sell.markers]
        .sort((a, b) => (a.time as number) - (b.time as number));
      markerBarTimes = bars.map((b) => b.time as number);
      applyMarkers();   // с кластеризацией по текущему зуму (see applyMarkers)
      markIndex = pm.index;

      // Position RECTANGLES per episode (replaces the old dashed connectors): box from
      // open→close, entry(avg)→exit levels, green long / red short, AVG markers visible
      // inside. Built from roll-aware events so a box never spans the roll. The line
      // series stay empty (kept only to preserve series z-order).
      const lastBar = bars[bars.length - 1];
      posRects = positionRects(events, lastBar.time, lastBar.close);
      // Incomplete-journal guard: replay end position vs the runner's authoritative
      // one. On mismatch the episode boundaries after the hole are provably wrong —
      // drop the sprawling open rect rather than draw a lie over later trades.
      // ponytail: suppression only; resyncing the replay at journal fix_state
      // entries would heal mid-tail holes if this ever needs to draw again.
      const endPos = events.length ? (events[events.length - 1] as any).posAfter : 0;
      if (journalSuspect || (livePosition != null && endPos !== livePosition))
        posRects = posRects.filter((r: any) => !r.open);
      longSeries.setData([]);
      shortSeries.setData([]);

      // P&L curve (₽, from ZERO): running sum of closed-trade results carried onto
      // each candle time so the pane shares the price chart's axis. Baseline 0 —
      // green above / red below, no 100000-equity fiction. Works for backtest AND
      // live (events exist for both); result.equity_curve is no longer used, and the
      // agent robot no longer draws a flat 100000 zombie line.
      // SINGLE PROFITABILITY LOGIC: when the backend result carries net_profit
      // (any backtest), THAT is the authoritative number shown as «Результат» —
      // identical to the hit-parade «Чистая прибыль». The engine (compute_metrics)
      // is the one source of truth the sweep optimises on. The frontend replay is
      // used only to DRAW the curve/markers; its raw sum can drift from the engine
      // (rounding, roll accounting), so the curve is anchored to end exactly at the
      // engine net. A LIVE robot has no engine result → engineNet is null and the
      // real-money replay (rolled.net) stands as before.
      const engineNet = (result && result.net_profit != null) ? Number(result.net_profit) : null;
      // Anchor the DRAWN curve to end at the AUTHORITATIVE net: a backtest's engine
      // net_profit, or (LIVE robot) the parent-supplied netOverride = runner realized_pnl.
      // Without it the raw fill-replay drifts (partial 200-fill tail, from-flat, fix_state
      // resets) — v2 drew -42373 while realized was -15790. Now curve endpoint == badge.
      const anchorNet = engineNet != null ? engineNet
        : (netOverride != null && Number.isFinite(netOverride) ? netOverride : null);
      // Источник кривой: журнал, если он передан, иначе пересчёт филлов.
      // У ЖИВОГО робота (netOverride задан) без журнала кривую не рисуем вовсе:
      // пересчёт обрезанного 200-хвоста с флэта стартует посреди позиции и врёт
      // — а нарисованное враньё хуже пустого места, потому что выглядит правдой.
      const haveJournal = !!(closeSeries && closeSeries.length);
      equityBlind = netOverride != null && !haveJournal;
      const closes = haveJournal
        ? closeSeries!.map((p) => ({ time: p.time, close: { pnl: p.pnl } }))
            .sort((a: any, b: any) => a.time - b.time)
        : events.filter((e: any) => e.close).sort((a: any, b: any) => a.time - b.time);
      let maxDD = 0;
      if (equityBlind) {
        equitySeries.setData([]);
        equityCarry = null;
        lastEquityValue = 0;
      }
      // Кривая рисуется ТОЛЬКО с первой известной сделки. Смещение (см. ниже)
      // поднимает всю кривую к пожизненному итогу, и на барах ДО журнала это
      // рисовало ровную полку на уровне, которого тогда не было: робот ещё не
      // торговал, а график показывал +200k за месяц до первой сделки, а дальше
      // «падение» с 310k до 126k. Форма была верной, вранья добавляла полка.
      const firstEvTime = events.length
        ? Math.min(...events.map((e: any) => e.time)) : null;
      let startIdx = 0;
      if (firstEvTime != null) {
        const i = bars.findIndex(b => (b.time as number) >= firstEvTime);
        startIdx = i < 0 ? bars.length : Math.max(0, i - 1);  // один бар до входа — базовая линия
      }
      const curveBars = bars.slice(startIdx);
      if (curveBars.length && !equityBlind) {
        let k = 0, cum = 0, peak = 0;
        const raw = curveBars.map(b => {
          while (k < closes.length && closes[k].time <= b.time) { cum += closes[k].close.pnl; k++; }
          return { time: b.time as number, value: cum };
        });
        // Сделки ПОСЛЕ последнего загруженного бара (бар-источник графика короче теста —
        // напр. открыли из избранного с чужим/коротким окном дат) обязаны попасть в сумму.
        // Иначе rawEnd — ЧАСТИЧНЫЙ огрызок, и привязка engineNet/rawEnd ниже ПЕРЕВОРАЧИВАЕТ
        // и раздувает форму в ±2.5М при честном итоге +724k — ровно тот «бред» на графике.
        const kBefore = k;
        while (k < closes.length) { cum += closes[k].close.pnl; k++; }
        equityTailBeyond = k - kBefore;
        if (raw.length && equityTailBeyond > 0)
          raw[raw.length - 1] = { time: raw[raw.length - 1].time, value: cum };
        const rawEnd = raw.length ? raw[raw.length - 1].value : 0;
        // Привязка конца кривой к авторитетному итогу — ДВУМЯ способами:
        //  - бэктест (engineNet): МАСШТАБ. raw уже ≈ engineNet по тем же сделкам,
        //    множитель лишь снимает копеечный дрейф округления/ролла.
        //  - живой робот (netOverride = ПОЖИЗНЕННЫЙ реализованный раннера): СМЕЩЕНИЕ,
        //    никогда не масштаб. Форма берётся из журнала, но это лишь ОКНО сделок
        //    (обрезка 200-хвоста не достаёт историю до армирования), поэтому raw <
        //    пожизненного. Смещение поднимает базовую линию к истинному итогу, сохраняя
        //    КАЖДЫЙ шаг между сделками точным. Масштаб здесь растягивал форму в день,
        //    которого не было (MACD·RIU6: -30k->+104k из ниоткуда). Полный журнал ->
        //    смещение ≈ 0; обрезанный -> смещение = нераскрытая ранняя история.
        const offset = (netOverride != null && Number.isFinite(netOverride))
          ? netOverride - rawEnd : 0;
        // engineNet-масштаб — ТОЛЬКО копеечная поправка дрейфа округления/ролла: одинаковый
        // знак и кратность в [0.4, 2.5]. Вне этих границ rawEnd структурно не равен итогу
        // (бары не покрыли сделки, дрейф модели комиссии) — тогда множитель СОЛЖЁТ о форме
        // (перевернёт/раздует), поэтому рисуем ЧЕСТНЫЙ raw как есть. Конец может не точно
        // сойтись с бейджем, но кривая перестаёт врать (никаких ±2.5М из воздуха).
        const _ratio = (engineNet != null && rawEnd !== 0) ? engineNet / rawEnd : 1;
        const _scaleOk = engineNet != null && rawEnd !== 0 && _ratio >= 0.4 && _ratio <= 2.5;
        const adj = _scaleOk
          ? (v: number) => v * _ratio
          : (netOverride != null && Number.isFinite(netOverride))
            ? (v: number) => v + offset
            : (v: number) => v;
        // Подписываем РОВНО ТО, ЧТО ВИДНО: уровень, с которого начинается
        // нарисованная кривая. Он не ноль, когда часть сделок случилась раньше
        // первого загруженного бара (график грузит окно в несколько дней, а
        // журнал живёт дольше). Полка слева без подписи читается как «прибыль
        // взялась из воздуха» — именно на этом график ловили дважды.
        // Порог: мелочь — это дрейф модели комиссии, а не спрятанная история.
        const startValue = raw.length ? raw[0].value + offset : 0;
        const material = Math.abs(startValue) > Math.max(1000, Math.abs(netOverride ?? 0) * 0.02);
        equityCarry = (engineNet == null && material)
          ? { rub: startValue, fromTs: raw.length ? raw[0].time : 0 } : null;
        const curve = raw.map(p => {
          const v = adj(p.value);
          if (v > peak) peak = v;
          if (peak - v > maxDD) maxDD = peak - v;
          return { time: p.time, value: v };
        });
        // Выравниваем шкалу времени equity со свечами: перед кривой добавляем
        // WHITESPACE-точки на КАЖДЫЙ бар до первой сделки. Тогда число точек у обеих
        // серий одинаково, и односторонний logical-range sync мапит индекс-в-индекс
        // БЕЗ сдвига. Whitespace ничего не рисует — фикс «полки до первой сделки» цел.
        // Раньше equity стартовала с startIdx и была на startIdx баров короче свечей:
        // нижний график уезжал вбок, а смена интервала меняла startIdx и рождала
        // «второй график из ниоткуда» + отставание.
        const lead = bars.slice(0, startIdx).map(b => ({ time: b.time as number }));
        equitySeries.applyOptions({ baseValue: { type: 'price', price: 0 } });
        equitySeries.setData([...lead, ...curve]);
        lastEquityValue = curve.length ? curve[curve.length - 1].value : 0;
      } else {
        equitySeries.setData([]);
        lastEquityValue = 0;
      }

      // All round-trip / money stats from the roll-aware result — single source of
      // truth, identical to the robot summary and the showcase (no more disagreement).
      const closesPnl = events.filter(e => e.close).map(e => e.close!.pnl);
      // «Результат» + «сделок» = the engine's numbers when present (backtest),
      // so the chart and the hit-parade can never disagree; else the live replay.
      const authNet = engineNet != null ? engineNet : rolled.net;
      // Live robot: parent supplies the runner's own realized_pnl × ₽/point via
      // netOverride — authoritative over the tail replay (see the prop doc above).
      const shownNet = (netOverride != null && Number.isFinite(netOverride)) ? netOverride : authNet;
      const authTrades = (result && result.total_trades != null) ? Number(result.total_trades) : rolled.closes;
      stats = {
        fills: fills.length,
        roundTrips: authTrades,
        longRT: events.filter(e => e.close && e.side === 'sell').length,   // sold to close a long
        shortRT: events.filter(e => e.close && e.side === 'buy').length,   // bought to close a short
        maxAbsPos: rolled.peakContracts,
        avgPerTrade: closesPnl.length ? closesPnl.reduce((a, b) => a + b, 0) / closesPnl.length : 0,
        maxProfit: closesPnl.length ? Math.max(...closesPnl) : 0,
        maxLoss: closesPnl.length ? Math.min(...closesPnl) : 0,
        netProfit: shownNet,
        maxDDmoney: maxDD,
        // «Фактор восст.»: у бэктеста ЕСТЬ движковый RF — берём его (то же число,
        // что в хитпараде). Свой пересчёт по кривой оставлен фолбэком: на
        // сглаженной ресемплом кривой maxDD занулялся и RF печатался прочерком,
        // хотя движок его посчитал (карточка лидера GDU6, 2026-07-25).
        recovery: (result && result.recovery_factor != null)
          ? Number(result.recovery_factor)
          : (maxDD > 0 ? shownNet / maxDD : null),
      };
      netResult = shownNet;     // parent-supplied authoritative net (live) else engine/replay
      onNet?.(netResult);       // parent header badge shows the SAME number
      // ВМ открытой позиции. ТРИ ЖЁСТКИХ ГЕЙТА (каждый ловил живой фантом):
      //  1) множитель ₽/пункт — строго текущего контракта (point-coef trap);
      //  2) ЦЕНА — с ТОГО ЖЕ контракта, что и позиция: последний бар может
      //     принадлежать умершему контракту после ролла (−261 715 ₽, 27.07);
      //  3) бар не старше 4 суток — по мёртвому контракту ВМ не считаем вовсе.
      // Реплей без доверия (обрезанный журнал) ВМ тоже не считает: лучше честный
      // фикс, чем ВМ от вранной позиции. floatRub (агент-экран) приоритетен.
      const replayTrusted = !(journalSuspect || (livePosition != null && endPos !== livePosition));
      const pvCur = (pointValues && rolled.currentSymbol && pointValues[rolled.currentSymbol] != null)
        ? pointValues[rolled.currentSymbol] : (pointValue ?? 1);
      const vmBar: any = bars.length ? bars[bars.length - 1] : null;
      const barSym = String(vmBar?.sym ?? symbol ?? '').toUpperCase();
      const posSym = String(rolled.currentSymbol || symbol || '').toUpperCase();
      const barFresh = !!vmBar && (Date.now() / 1000 - vmBar.time) < 4 * 86400;
      const priceOk = !!vmBar && barSym === posSym && barFresh;
      vmOpen = floatRub != null ? floatRub
        : (replayTrusted && priceOk && rolled.position !== 0 && rolled.openAvg > 0
            ? (vmBar.close - rolled.openAvg) * rolled.position * pvCur : 0);
      onVm?.(vmOpen);
      // Broker vs exchange commission split (transparency).
      commission = commissionBreakdown(fills, pointValue, symbol, taker);
      // Per-trade rows for the trades table (one row per fill, with role + close PnL).
      tradeRows = events.map((e, i) => ({
        n: i + 1, time: e.rawTime, kind: e.kind, side: e.side, qty: e.qty, price: e.price,
        posAfter: e.posAfter,
        comm: commissionForFill(e.price, e.qty),
        pnl: e.close ? e.close.pnl : null,
        exit: e.close ? e.close.exit : null,
        label: e.label,
      }));

      // First load of this symbol/interval: show the READABLE TAIL, not everything.
      // fitContent() squeezed the whole loaded window into the canvas — at 5m over a
      // 14-day live window that is ~2500 candles in ~1500px, i.e. sub-pixel candles
      // drawn as hairline dashes with every trade marker piled into one unreadable
      // blob (operator: «фантомные чёрточки», «чухня»). «Весь период» stays one click
      // away (fitAll) for the rare full-window look.
      // Same-view LIVE reload: restore the operator's zoom/pan instead.
      if (keepRange) {
        try { tvCandle.timeScale().setVisibleLogicalRange(keepRange); }
        catch { tvCandle.timeScale().fitContent(); }
      } else {
        showTail(bars.length);
      }
      _viewKey = viewKey;
      syncReady = true;
      const lr = tvCandle.timeScale().getVisibleLogicalRange();
      // ~7% of the visible width reserved on the right (in bars) so the newest
      // arrows clear the price-axis order labels.
      if (lr) tvCandle.timeScale().applyOptions({ rightOffset: Math.max(2, Math.round((lr.to - lr.from) * 0.07)) });
      if (lr) updateThumb(lr); else scrollThumb = { left: 0, width: 100 };
      positionLiveLine();
      drawOrderLines();
      scheduleRects();   // defer over frames so boxes lock onto candles on first render
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // Horizontal price lines: resting orders (solid) + planned algo triggers (dotted),
  // green = buy, red = sell. No on-chart titles (axis labels off) — the description
  // shows only on hover (see hitTestTooltip → lineIndex).
  let lineIndex: Array<{ price: number; text: string }> = [];
  function drawOrderLines() {
    if (!candleSeries) return;
    for (const pl of orderPriceLines) { try { candleSeries.removePriceLine(pl); } catch { /* gone */ } }
    orderPriceLines = []; lineIndex = [];
    for (const o of openOrders ?? []) {
      const buy = o.side === 'buy';
      const role = o.role;                       // decoded intent (тейк/вход/усреднение)
      const qtyTxt = o.qty ? ` ×${o.qty}` : '';
      // Axis label = the signal role (falls back to bare side); colour already encodes buy/sell.
      orderPriceLines.push(candleSeries.createPriceLine({
        price: o.price, color: buy ? BUY_COLOR : SELL_COLOR, lineWidth: 2, lineStyle: 0,
        axisLabelVisible: true, title: (role || `заявка ${buy ? 'BUY' : 'SELL'}`) + qtyTxt,
      }));
      lineIndex.push({ price: o.price, text:
        `${role ? role + ' — ' : ''}${buy ? 'покупка' : 'продажа'}${o.qty ? ' ' + o.qty + ' конт.' : ''} @ ${fmtPrice(o.price)}`.trim() });
    }
    for (const o of plannedOrders ?? []) {
      const buy = o.side === 'buy';
      orderPriceLines.push(candleSeries.createPriceLine({
        price: o.price, color: buy ? BUY_COLOR : SELL_COLOR, lineWidth: 1, lineStyle: 1,  // dotted = plan
        axisLabelVisible: true, title: `план ${buy ? 'BUY' : 'SELL'}`,
      }));
      const why = (o as any).reason ? ` — ${(o as any).reason}` : '';
      lineIndex.push({ price: o.price, text: `План ${buy ? 'BUY' : 'SELL'} ${o.qty || ''} @ ${fmtPrice(o.price)}${why}`.trim() });
    }
  }

  $effect(() => { if (result && candleSeries) loadData(); });
  $effect(() => { openOrders; plannedOrders; if (candleSeries && syncReady) drawOrderLines(); });
</script>

<div class="bt-root" bind:this={containerEl}>
  <!-- Pinned control header: never wraps, fixed position above the chart. -->
  <div class="bt-header">
    <span class="bt-symbol">{symbol}</span>
    {#if periodLabel}<span class="bt-period" title="Период теста">{periodLabel}</span>{/if}
    {#if strategy}
      <a class="bt-strategy" href={strategy.source} target="_blank" rel="noopener">{strategy.name} ↗</a>
    {/if}
    <span class="bt-legend">
      <span class="lg lg-long">▲ покупка</span><span class="lg lg-short">▼ продажа</span>
      <span class="lg lg-tp">■ TP</span><span class="lg lg-sl">■ SL</span>
    </span>
    <!-- Interval block pinned at the far right; the crosshair date/time is NOT
         here (it would shift these buttons). It lives in an on-chart overlay. -->
    <div class="bt-intervals">
      <button class="bt-fit" title="Показать весь период теста" onclick={fitAll}>Весь период</button>
      {#each INTERVALS as iv}
        <button class:active={resampleMin === iv.v} onclick={() => pickInterval(iv.v)}>{iv.label}</button>
      {/each}
    </div>
    {#if screenId}<ScreenTag id={screenId} name="график бэктеста" inline />{/if}
  </div>

  <div class="bt-candle-area">
    <div class="candle" bind:this={candleEl}></div>

    <!-- "LIVE ПОТОКА": thin blue line at the newest bar — where the live stream
         feeds price into the robot's calc. Sits in the ~7% right gap. -->
    {#if liveLineX != null}
      <div class="live-flow" style="left:{liveLineX}px"><span>LIVE ПОТОКА</span></div>
    {/if}

    <!-- Position rectangles: diagonal = entry vertex → exit vertex (exact fill prices,
         AVG never moves them); green long / red short; dashed border = still-open. The
         episode P&L sits in the centre (or on hover when the box is too small). -->
    <div class="pos-rects">
      {#each rectPx as r}
        <div class="pos-rect {r.dir}" class:open={r.open}
             style="left:{r.left}px; top:{r.top}px; width:{r.width}px; height:{r.height}px;">
          {#if r.showLabel}
            <span class="pr-pnl" class:pos={r.pnl > 0} class:neg={r.pnl < 0}>{r.label}</span>
          {/if}
        </div>
      {/each}
    </div>

    <!-- Editable params frame (top-left), collapsed until clicked. Edit a value and
         "Пересчитать" re-runs the backtest with the new params. -->
    <div class="bc-params" class:open={paramsOpen}
         onpointerdown={(e) => e.stopPropagation()} onwheel={(e) => e.stopPropagation()}>
      <button class="bc-params-h"
              onclick={() => { paramsOpen = !paramsOpen; if (paramsOpen && pview === 'panel') sheetOpen = true; }}
              title="Параметры прогона — клик чтобы развернуть/свернуть">
        ⚙ Параметры {paramsOpen ? '▾' : '▸'}
      </button>
      {#if paramsOpen}
        <div class="bc-params-body">
          <div class="bc-view" role="group" aria-label="Вид параметров">
            <button class:on={pview === 'list'} onclick={() => setPview('list')}
                    title="плотный список прямо здесь">Список</button>
            <button class:on={pview === 'panel'}
                    onclick={() => { setPview('panel'); sheetOpen = true; }}
                    title="развёрнутый лист: группы в колонках, крупные значения, перевод в пункты">Панель</button>
          </div>
          {#if pview === 'panel'}
            <!-- Лист открывается САМИМ переключателем: отдельная кнопка «Открыть
                 параметры» была лишним шагом, и когда лист по любой причине не
                 показывался, это читалось как «кнопка не работает». Здесь остаётся
                 только путь назад, если оператор лист закрыл. -->
            <button class="bc-open-sheet" onclick={() => sheetOpen = true}>
              {sheetOpen ? 'Лист параметров открыт' : 'Открыть лист параметров ▸'}
            </button>
          {:else}
          {#each editKeys as k}
            <label class="bc-prow" title={labelFor(k)}>
              <span class="bc-pk">{labelFor(k)}</span>
              {#if typeof params[k] === 'number'}
                <input class="bc-pv" type="number" step="any" bind:value={editParams[k]}
                       oninput={() => editTouched = true}
                       onkeydown={(e) => e.key === 'Enter' && applyParams()} />
              {:else}
                <input class="bc-pv" type="text" bind:value={editParams[k]}
                       oninput={() => editTouched = true}
                       onkeydown={(e) => e.key === 'Enter' && applyParams()} />
              {/if}
            </label>
          {/each}
          {/if}
          <label class="bc-prow bc-prow-date" title="Начало окна исторических данных">
            <span class="bc-pk">Период с</span>
            <input class="bc-pv bc-pv-date" type="date" bind:value={editFrom} />
          </label>
          <label class="bc-prow bc-prow-date" title="Конец окна исторических данных">
            <span class="bc-pk">по</span>
            <input class="bc-pv bc-pv-date" type="date" bind:value={editTo} />
          </label>
          {#if onRerun}
            <button class="bc-apply" class:dirty={paramsDirty} onclick={applyParams}>
              Пересчитать бэктест
            </button>
          {/if}
          {#if strategy}
            <!-- Пересчёт даёт ОДИН результат, а рядом почти всегда нужен вопрос
                 «а что этот робот показывал раньше». История прогонов живёт в
                 Backtest Lab, но найти её оттуда — отдельный поход; ссылка
                 открывает её сразу отфильтрованной по этой стратегии и тикеру. -->
            <a class="bc-runs" target="_blank" rel="noopener"
               href={'/?lab=backtest&hist=' + encodeURIComponent(String(strategy) + ' ' + symbol)}
               title="все сохранённые прогоны этого робота на этом инструменте: период, лидер, параметры">
              Прогоны робота ↗
            </a>
          {/if}
          {#if onApplyParams}
            <button class="bc-apply live" class:dirty={paramsDirty} disabled={applyBusy}
                    title="записать эти значения в РАБОТАЮЩЕГО робота — применятся на следующем баре"
                    onclick={() => onApplyParams({ ...editParams })}>
              {applyBusy ? 'Сохраняю…' : '✔ Сохранить в робота'}
            </button>
            {#if applyMsg}<div class="bc-apply-msg">{applyMsg}</div>{/if}
          {/if}
          <!-- Избранное: набор параметров + период + результат под своим именем.
               Открывается потом из «Бэктест → Избранное» без пересчёта. -->
          <div class="bc-fav">
            <input class="bc-pv" type="text" bind:value={favName} placeholder="имя набора"
                   onkeydown={(e) => e.key === 'Enter' && saveFavorite()} />
            <button class="bc-fav-btn" onclick={saveFavorite} title="Сохранить этот набор параметров и результат в избранное">⭐ В избранное</button>
          </div>
          {#if favMsg}<div class="bc-fav-msg">{favMsg}</div>{/if}
        </div>
      {/if}
    </div>

    <!-- ШИРОКИЙ ЛИСТ ПАРАМЕТРОВ. Развёрнутому фрейму нужна ширина: в поповере 156px
         подписи обрезались, а поля ввода уезжали за край — оператор видел кишку из
         заголовков без единого управляемого поля (06.08.2026). Лист перекрывает
         график, потому что в момент правки параметров смотрят на параметры. -->
    {#if sheetOpen && pview === 'panel'}
      <div class="ps-back" role="presentation" use:portal
           onclick={(e) => { if (e.target === e.currentTarget) sheetOpen = false; }}>
        <div class="ps" role="dialog" aria-label="Параметры робота"
             style={`${psW ? `width:${psW}px;` : ''}${psH ? `height:${psH}px;` : ''}`}>
          <header class="ps-h">
            <div>
              <h3>Параметры</h3>
              <span>{strategy || 'стратегия'} · {symbol}</span>
            </div>
            <div class="ps-view" role="group" aria-label="Вид">
              <button onclick={() => { setPview('list'); sheetOpen = false; }}
                      title="вернуться к плотному списку в углу графика">Список</button>
              <button class="on">Панель</button>
            </div>
            <button class="ps-x" onclick={() => sheetOpen = false} aria-label="Закрыть">✕</button>
          </header>

          <div class="ps-body">
            <ParamPanel wide strategyId={String(strategy || '')} schema={panelSchema}
                        bind:values={editParams} baseline={params}
                        ctx={{ atr: 0, price: Number(params?.last_close) || 0 }}
                        disabledKeys={['symbol']} />
            <div class="ps-period">
              <label><span>Период с</span>
                <input type="date" bind:value={editFrom} /></label>
              <label><span>по</span>
                <input type="date" bind:value={editTo} /></label>
              <span class="ps-note">окно исторических данных для пересчёта</span>
            </div>
          </div>

          <!-- Действия внизу и всегда на виду: правка параметров кончается одним из
               трёх решений — записать роботу, пересчитать здесь, перебрать в Лаборатории. -->
          <footer class="ps-f">
            {#if onApplyParams}
              <button class="ps-btn primary" class:dirty={paramsDirty} disabled={applyBusy}
                      title="записать эти значения в РАБОТАЮЩЕГО робота — применятся на следующем баре"
                      onclick={() => onApplyParams({ ...editParams })}>
                {applyBusy ? 'Сохраняю…' : '✔ Сохранить в робота'}</button>
            {/if}
            {#if onRerun}
              <button class="ps-btn" class:dirty={paramsDirty} onclick={applyParams}
                      title="пересчитать бэктест с этими значениями за выбранный период">
                ↻ Пересчитать бэктест</button>
            {/if}
            {#if sweepHref}
              <a class="ps-btn" href={sweepHref} target="_blank" rel="noopener"
                 title="открыть Лабораторию с этими параметрами и задать диапазоны перебора">
                ⚙ Перебрать параметры ↗</a>
            {/if}
            {#if strategy}
              <a class="ps-btn ghost" target="_blank" rel="noopener"
                 href={'/?lab=backtest&hist=' + encodeURIComponent(String(strategy) + ' ' + symbol)}
                 title="все сохранённые прогоны этого робота на этом инструменте">
                История прогонов ↗</a>
            {/if}
            <span class="ps-spacer"></span>
            {#if applyMsg}<span class="ps-msg">{applyMsg}</span>{/if}
            <button class="ps-btn ghost" onclick={() => sheetOpen = false}>Закрыть</button>
          </footer>

          <!-- Границы тянутся мышкой; двойной клик по любой — вернуть размер по
               умолчанию. Ручки узкие, но с запасом попадания в 6px. -->
          <button class="ps-rz e" type="button" aria-label="Ширина листа"
                  title="Потяните — размер; стрелки — с клавиатуры; двойной клик — сброс"
                  onpointerdown={(e) => psDrag(e, 'x')} ondblclick={psReset}
                  onkeydown={(e) => psKey(e, 'x')}></button>
          <button class="ps-rz s" type="button" aria-label="Высота листа"
                  title="Потяните — размер; стрелки — с клавиатуры; двойной клик — сброс"
                  onpointerdown={(e) => psDrag(e, 'y')} ondblclick={psReset}
                  onkeydown={(e) => psKey(e, 'y')}></button>
          <button class="ps-rz se" type="button" aria-label="Размер листа"
                  title="Потяните — размер; стрелки — с клавиатуры; двойной клик — сброс"
                  onpointerdown={(e) => psDrag(e, 'xy')} ondblclick={psReset}
                  onkeydown={(e) => psKey(e, 'xy')}></button>
        </div>
      </div>
    {/if}

    <!-- On-chart crosshair date/time, like TradingView/QUIK (shifted right to clear
         the params frame). -->
    {#if crossLabel}<div class="cross-overlay">{crossLabel}</div>{/if}

    {#if tip}
      <div class="trade-tip" style="left:{tip.x + 12}px; top:{tip.y - 8}px;">
        {#if tip.head}
          <div class="tt-head tt-{tip.headKind ?? 'neutral'}">{tip.head}</div>
        {/if}
        {#each tip.lines as l}
          <div class="tt-sub">{l}</div>
        {/each}
      </div>
    {/if}

    {#if stats && !hideStats}
      <div class="stats-overlay" class:open={statsExpanded}>
        <!-- collapsed: 2 lines. click to expand (frees up chart area). -->
        <button class="st-toggle" onclick={() => statsExpanded = !statsExpanded}
                title={statsExpanded ? 'Свернуть отчёт' : 'Развернуть отчёт'}>
          <!-- Правило: «Результат» = фикс + ВМ открытой позиции. Разбивка строкой ниже,
               чтобы фикс (закрытые сделки) оставался виден и сверяем. -->
          <div class="st-head">
            <span>Результат</span>
            <b class:pos={netResult + vmOpen > 0} class:neg={netResult + vmOpen < 0}>{fmtMoney(netResult + vmOpen)} ₽</b>
            <span class="st-chev">{statsExpanded ? '▴' : '▾'}</span>
          </div>
          <div class="st-head2">
            <span>{stats.roundTrips} сделок · комиссия {commission ? fmtRub(commission.total) : '—'}</span>
          </div>
          {#if vmOpen !== 0}
            <div class="st-head2 st-live">
              <span>фикс <b class:pos={netResult > 0} class:neg={netResult < 0}>{fmtMoney(netResult)}</b>
                · ВМ откр. поз. <b class:pos={vmOpen > 0} class:neg={vmOpen < 0}>{fmtMoney(vmOpen)}</b> ₽</span>
            </div>
          {/if}
        </button>

        {#if statsExpanded}
          <div class="st-body">
            <div class="st-row"><span>Всего сделок</span><b>{stats.roundTrips}</b>
              <span class="st-sub">(L {stats.longRT} / S {stats.shortRT})</span></div>
            <div class="st-row"><span>Макс. позиция</span><b>{stats.maxAbsPos} конт.</b>
              <span class="st-sub">ГО: {margin != null ? fmtMoney(stats.maxAbsPos * margin).replace('+','') + ' ₽' : '—'}</span></div>
            <div class="st-row"><span>Средн. на сделку</span>
              <b class:pos={stats.avgPerTrade > 0} class:neg={stats.avgPerTrade < 0}>{fmtMoney(stats.avgPerTrade)}</b></div>
            <div class="st-row"><span>Макс. прибыль</span><b class="pos">{fmtMoney(stats.maxProfit)}</b></div>
            <div class="st-row"><span>Макс. убыток</span><b class="neg">{fmtMoney(stats.maxLoss)}</b></div>
            <div class="st-row"><span>Фактор восст.</span><b>{stats.recovery != null ? stats.recovery.toFixed(2) : '—'}</b></div>

            {#if commission}
              <div class="st-sep"></div>
              <div class="st-row st-comm-h"><span>Комиссия ({taker ? 'тейкер' : 'мейкер'})</span><b class="neg">−{fmtRub(commission.total)}</b></div>
              <div class="st-row"><span>· брокеру (Finam 0,45/конт.)</span><b class="neg">−{fmtRub(commission.broker)}</b></div>
              <div class="st-row"><span>· бирже (MOEX{taker ? ` ${(commission.rate * 100).toFixed(4)}%` : ', мейкер 0'})</span><b class="neg">−{fmtRub(commission.exchange)}</b></div>
              <div class="st-row"><span>· филлов / контрактов</span><span class="st-sub">{commission.fills} / {commission.contracts}</span></div>
            {/if}

            {#if exits && (exits.tp + exits.sl) > 0}
              <div class="st-sep"></div>
              <div class="st-row"><span>Выходы TP / SL</span>
                <b><span class="pos">{exits.tp}</span> / <span class="neg">{exits.sl}</span></b>
                <span class="st-sub">{(exits.winRateByExit * 100).toFixed(0)}% TP</span></div>
              <div class="st-row"><span>· полные</span>
                <span class="st-sub">TP {exits.tpFull} / SL {exits.slFull}</span></div>
              <div class="st-row"><span>· частичные</span>
                <span class="st-sub">TP {exits.tpPartial} / SL {exits.slPartial}</span></div>
              <div class="st-row"><span>Прибыль TP</span><b class="pos">{fmtMoney(exits.tpPnl)}</b></div>
              <div class="st-row"><span>Убыток SL</span><b class="neg">{fmtMoney(exits.slPnl)}</b></div>
            {/if}

            {#if levelStats.length}
              <div class="st-sep"></div>
              <div class="st-lvl-h">TP/SL по глубине усреднения
                <span class="st-sub">пик контрактов в сделке → исход. Стопа нет — смотри НЕТТО ₽, не только счёт: один глубокий SL съедает пачку мелких TP.</span></div>
              <div class="st-lvl-tbl">
                <div class="st-lvl-row st-lvl-head">
                  <span>Ур.</span><span class="num">TP</span><span class="num">SL</span><span class="num">TP/SL</span>
                  <span class="num">Σ TP</span><span class="num">Σ SL</span><span class="num">Нетто</span></div>
                {#each levelStats as L}
                  {@const net = L.tpPnl + L.slPnl}
                  <div class="st-lvl-row">
                    <span class="mono">{L.level}</span>
                    <span class="num pos">{L.tp}</span>
                    <span class="num neg">{L.sl}</span>
                    <span class="num">{L.sl ? (L.tp / L.sl).toFixed(1) : (L.tp ? '∞' : '—')}</span>
                    <span class="num pos">{L.tpPnl ? fmtMoney(L.tpPnl) : '—'}</span>
                    <span class="num neg">{L.slPnl ? fmtMoney(L.slPnl) : '—'}</span>
                    <span class="num" class:pos={net >= 0} class:neg={net < 0}>{fmtMoney(net)}</span>
                  </div>
                {/each}
              </div>
            {/if}

            <button class="st-trades-btn" onclick={() => showTrades = true}>Открыть таблицу сделок бэктеста →</button>
            <div class="st-foot">Суммы в ₽, чистыми (за вычетом комиссии {taker ? 'тейкер: биржа + брокер' : 'мейкер: только брокер'}).</div>
          </div>
        {/if}
      </div>
    {/if}

    <!-- full per-trade table (all details), opened from the report -->
    {#if showTrades}
      <div class="trades-pane">
        <div class="tp-head">
          <span class="tp-title">Сделки бэктеста · {symbol} · {tradeRows.length} филлов</span>
          <button class="tp-csv" onclick={() => downloadCSV(
            tradeRows.map(r => ({ '№': r.n, время_UTC: fmtTs(r.time), тип: KIND_RU[r.kind] ?? r.kind,
              сторона: r.side === 'buy' ? 'покупка' : 'продажа', кол_во: r.qty, цена: r.price,
              комиссия: r.comm, позиция_после: r.posAfter, результат_руб: r.pnl })),
            'trades-' + symbol)}>Выгрузить в CSV</button>
          <button class="tp-close" onclick={() => showTrades = false}>✕</button>
        </div>
        <div class="tp-wrap">
          <table class="tp-table">
            <thead>
              <tr><th>#</th><th>Время (UTC)</th><th>Тип</th><th>Сторона</th><th class="num">Кол.</th><th class="num">Цена</th><th class="num">Комиссия</th><th class="num">Поз. после</th><th class="num">Результат ₽</th></tr>
            </thead>
            <tbody>
              {#each tradeRows as r}
                <tr>
                  <td>{r.n}</td>
                  <td>{fmtTs(r.time)}</td>
                  <td>{KIND_RU[r.kind] ?? r.kind}</td>
                  <td class={r.side === 'buy' ? 'pos' : 'neg'}>{r.side === 'buy' ? 'покупка' : 'продажа'}</td>
                  <td class="num">{r.qty}</td>
                  <td class="num">{fmtPrice(r.price)}</td>
                  <td class="num neg">−{fmtRub(r.comm)}</td>
                  <td class="num">{r.posAfter}</td>
                  <td class="num" class:pos={r.pnl > 0} class:neg={r.pnl < 0}>{r.pnl != null ? fmtMoney(r.pnl) : '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}

    {#if loading}<div class="overlay">Загрузка…</div>{/if}
    {#if error}<div class="overlay error">{error}</div>{/if}
  </div>

  <!-- drag this divider up/down to resize the P&L field -->
  <div class="bt-resizer" title="потяни, чтобы изменить высоту графика доходности"
       onpointerdown={startEqResize} onpointermove={moveEqResize} onpointerup={endEqResize}></div>
  <!-- Без известного ₽/пункт кривая идёт в ПУНКТАХ — подпись обязана это говорить,
       иначе пункты читаются как рубли (у BR пункт = 785 ₽, у RTS = 1.57 ₽). -->
  <div class="bt-equity-label">P&L робота, {unitLabel} (нарастающим по закрытым сделкам)
    {#if equityBlind}<span class="bt-equity-blind"
      >· журнал сделок не загрузился — кривая не строится, чтобы не показать неверную</span
      >{:else if equityEmpty}<span class="bt-equity-blind"
      >· в этом прогоне сохранены только метрики (свип не хранит сделки) — кривой нет,
      откройте лидера кнопкой пересчёта</span>{/if}
    {#if equityCarry}<span class="bt-equity-carry"
      >· кривая с {fmtDay(equityCarry.fromTs)}: к этому моменту робот уже
      реализовал {fmtMoney(equityCarry.rub)} {unitLabel}, более ранние сделки вне окна графика</span>{/if}
    {#if equityTailBeyond > 0}<span class="bt-equity-blind"
      >· ⚠ данные графика короче теста: {equityTailBeyond} сделок после последнего бара сжаты в конец кривой</span>{/if}</div>
  <div class="equity" bind:this={equityEl} style="height:{equityPx}px"></div>

  <!-- Custom horizontal scrollbar: drag the thumb to scroll across the data span. -->
  <div class="bt-scrollbar" bind:this={scrollTrackEl}>
    <div
      class="bt-thumb" role="scrollbar" tabindex="0" aria-controls="bt-chart" aria-valuenow={Math.round(scrollThumb.left)}
      style="left:{scrollThumb.left}%; width:{scrollThumb.width}%;"
      onpointerdown={onBarDown} onpointermove={onBarMove} onpointerup={onBarUp}
    ></div>
  </div>

  <div class="bt-hint">Колесо — масштаб свечи · Shift+колесо или перетаскивание графика — прокрутка · полоса ниже — скролл</div>
</div>

<style>
  .bt-root { display: flex; flex-direction: column; height: 100%; background: #0a0a15; }
  .bt-header {
    display: flex; align-items: center; gap: 10px; flex-wrap: nowrap; overflow: hidden;
    padding: 5px 10px; background: #0f0f1e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
    min-height: 30px;
  }
  .bt-symbol { font-size: 13px; color: #4caf50; font-weight: 600; flex-shrink: 0; }
  .bt-period { font-size: 10px; color: #9ab; background: #12203a; border: 1px solid #24406a; border-radius: 3px; padding: 1px 7px; white-space: nowrap; flex-shrink: 0; }
  .bt-strategy { font-size: 11px; color: #6aa8ff; text-decoration: none; flex-shrink: 0; }
  .bt-strategy:hover { text-decoration: underline; }
  .bt-params { display: flex; gap: 4px; overflow: hidden; flex-shrink: 1; min-width: 0; }
  .bt-param { font-size: 10px; font-family: monospace; color: #888; background: #1a1a2e; border-radius: 2px; padding: 1px 5px; white-space: nowrap; }
  .bt-legend { display: flex; gap: 8px; font-size: 10px; flex-shrink: 0; }
  .lg-long { color: #2ee6a6; } .lg-short { color: #ff5c8a; }
  .lg-tp { color: #19e36a; } .lg-sl { color: #ff3b3b; }
  /* Interval selector pinned to the far right, never wraps, never moves. */
  .bt-intervals { display: flex; gap: 1px; flex-shrink: 0; margin-left: auto; }
  .bt-intervals button { background: transparent; color: #555; border: 1px solid transparent; font-size: 10px; padding: 2px 7px; border-radius: 3px; cursor: pointer; }
  .bt-intervals button:hover { color: #aaa; }
  .bt-intervals button.active { color: #4caf50; border-color: #4caf5066; background: #4caf5012; }
  .bt-intervals button.bt-fit { color: #9ab; border-color: #24406a; background: #12203a; margin-right: 6px; }
  .bt-intervals button.bt-fit:hover { color: #cfe; border-color: #6aa8ff66; }

  .bt-candle-area { position: relative; flex: 1; min-height: 0; }
  .candle { position: absolute; inset: 0; }

  /* Position rectangles overlay — above candles, below tooltips; never intercepts
     pointer events (pan/zoom/hover pass through to the chart). */
  .pos-rects { position: absolute; inset: 0; overflow: hidden; pointer-events: none; z-index: 3; }
  .live-flow { position: absolute; top: 0; bottom: 0; width: 1px; background: #38bdf855;
    box-shadow: 0 0 6px #38bdf844; pointer-events: none; z-index: 2; }
  .live-flow span { position: absolute; top: 4px; left: 4px; font-size: 10px; letter-spacing: .5px;
    color: #7dd3fc; white-space: nowrap; writing-mode: vertical-rl; text-orientation: mixed; }
  .pos-rect {
    position: absolute; border: 1px solid; border-radius: 1px; box-sizing: border-box;
    display: flex; align-items: center; justify-content: center;
  }
  /* Long = green, short = red (clearly distinct hues, not teal/rose). */
  /* Faint by default so a busy always-in-market robot (150 boxes tiling) reads as soft
     position bands, not a dominant grid; the P&L still shows on hover / in big boxes. */
  .pos-rect.long  { background: #00e67612; border-color: #00e67655; }
  .pos-rect.short { background: #ff525212; border-color: #ff525255; }
  .pos-rect.open  { border-style: dashed; }
  .pr-pnl {
    font-size: 10px; font-family: monospace; font-weight: 700; white-space: nowrap;
    padding: 0 3px; border-radius: 2px; background: #0a0a15cc; color: #ccc;
  }
  .pr-pnl.pos { color: #00e676; } .pr-pnl.neg { color: #ff5252; }

  /* On-chart crosshair date/time overlay — shifted right to clear the params frame. */
  .cross-overlay {
    position: absolute; top: 6px; left: 156px; z-index: 6;
    font-size: 11px; font-family: monospace; color: #6aa8ff;
    background: #0f0f1ecc; border: 1px solid #2d2d4a; border-radius: 3px;
    padding: 2px 7px; pointer-events: none; white-space: nowrap;
  }

  /* Editable params frame (top-left, collapsible). */
  /* ── Широкий лист параметров ───────────────────────────────────────────── */
  /* Лист живёт в <body> (см. portal), ВНЕ поддерева компонента — поэтому его стили
     обязаны быть :global. Локальные Svelte вырезал как неиспользуемые, и лист
     оказался обычным блоком в конце документа: position:static, top 1049px, то есть
     «в подвале» под страницей (06.08.2026). */
  :global(.ps-back) { position: fixed; inset: 0;
    /* ВЫШЕ ВСЕХ ОБОЛОЧЕК. Лист вынесен в <body>, но соревнуется по слою со всем
       приложением: LabPanel рендерит Лабораторию на 1000, витрина Showcase на 3000.
       На 200 он честно открывался и был НЕВИДИМ под ними — кнопка писала «открыт»,
       а оператор не находил окна (06.08.2026). Выше только плашка об устаревшей
       вкладке (9000): она должна перекрывать вообще всё. */
    z-index: 8000; background: rgba(4,6,14,.72);
    display: flex; align-items: center; justify-content: center; padding: 16px; }
  :global(.ps) { position: relative; display: flex; flex-direction: column;
    width: min(1160px, 100%); max-height: calc(100vh - 32px);
    background: #0a1020; border: 1px solid #1c2a46; border-radius: 6px;
    box-shadow: 0 18px 60px rgba(0,0,0,.6); overflow: hidden; }
  :global(.ps-h) { display: flex; align-items: center; gap: 14px; padding: 12px 16px;
    border-bottom: 1px solid #1c2a46; flex: none; }
  :global(.ps-h h3) { margin: 0; font: 600 15px/1.2 system-ui, sans-serif; color: #dfe8f7; }
  :global(.ps-h > div > span) { font: 400 12px/1.4 ui-monospace, Consolas, monospace; color: #7c8cab; }
  :global(.ps-view) { display: inline-flex; margin-left: auto; border: 1px solid #24406a;
    border-radius: 4px; overflow: hidden; }
  :global(.ps-view button) { background: #0d1526; border: 0; color: #7c8cab; cursor: pointer;
    font: 600 11px/1 system-ui, sans-serif; padding: 7px 13px; }
  :global(.ps-view button + button) { border-left: 1px solid #24406a; }
  :global(.ps-view button.on) { background: #16243c; color: #dfe8f7; }
  :global(.ps-x) { background: none; border: 1px solid #2d2d4a; color: #99a; cursor: pointer;
    font-size: 13px; line-height: 1; padding: 5px 9px; border-radius: 3px; }
  :global(.ps-x:hover) { color: #e6e6f0; border-color: #4a4a70; }
  :global(.ps-body) { flex: 1; min-height: 0; overflow-y: auto; padding: 4px 14px 10px; }

  :global(.ps-period) { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    margin-top: 12px; padding-top: 12px; border-top: 1px solid #1c2a46; }
  :global(.ps-period label) { display: flex; align-items: center; gap: 8px; }
  :global(.ps-period span) { font: 600 13px/1 system-ui, sans-serif; color: #dfe8f7; }
  :global(.ps-period input) { height: 32px; background: #060b16; border: 1px solid #1c2a46;
    color: #fff; font: 500 14px/1 ui-monospace, Consolas, monospace; padding: 0 8px;
    border-radius: 4px; }
  :global(.ps-note) { font: 400 12px/1 system-ui, sans-serif; color: #7c8cab; }

  :global(.ps-f) { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: none;
    padding: 11px 14px; border-top: 1px solid #1c2a46; background: #0b1424; }
  :global(.ps-spacer) { flex: 1; }
  :global(.ps-btn) { display: inline-flex; align-items: center; height: 34px; padding: 0 15px;
    background: #16243c; border: 1px solid #2c4570; border-radius: 4px; color: #cfe2ff;
    font: 600 13px/1 system-ui, sans-serif; cursor: pointer; text-decoration: none; }
  :global(.ps-btn:hover) { background: #1d3050; border-color: #3a6aa8; color: #eaf3ff; }
  :global(.ps-btn.primary) { background: #14361f; border-color: #2f7a45; color: #9be5b0; }
  :global(.ps-btn.primary:hover) { background: #1a4628; color: #d6ffe0; }
  :global(.ps-btn.primary:disabled) { opacity: .55; cursor: default; }
  :global(.ps-btn.ghost) { background: none; color: #8b93a7; }
  :global(.ps-btn.ghost:hover) { color: #cfe2ff; }
  /* Несохранённая правка видна на самой кнопке — она и есть следующий шаг. */
  :global(.ps-btn.dirty) { box-shadow: 0 0 0 1px #f0a83c inset; }
  :global(.ps-msg) { font: 400 12px/1.4 system-ui, sans-serif; color: #f0d9a8; }

  /* Тянущиеся границы. Полоска подсвечивается под курсором — иначе непонятно, что
     край вообще живой. */
  /* Ручки — НАСТОЯЩИЕ кнопки: фокус и клавиатура достаются даром, а не борьбой
     с ролью separator, которую Svelte считает неинтерактивной. */
  :global(.ps-rz) { position: absolute; z-index: 2; padding: 0; border: 0; font: inherit;
    background: transparent; touch-action: none; }
  :global(.ps-rz:focus-visible) { outline: 2px solid #3d6ea8; outline-offset: -2px; }
  :global(.ps-rz:hover), :global(.ps-rz:active) { background: #2c4570; }
  :global(.ps-rz.e) { top: 0; right: 0; width: 6px; height: 100%; cursor: ew-resize; }
  :global(.ps-rz.s) { left: 0; bottom: 0; height: 6px; width: 100%; cursor: ns-resize; }
  :global(.ps-rz.se) { right: 0; bottom: 0; width: 14px; height: 14px; cursor: nwse-resize;
    background: linear-gradient(135deg, transparent 50%, #2c4570 50%); }
  :global(.ps-rz.se:hover) { background: linear-gradient(135deg, transparent 50%, #3a6aa8 50%); }

  .bc-open-sheet { align-self: stretch; margin: 2px 6px 4px; height: 30px;
    background: #16243c; border: 1px solid #2c4570; border-radius: 4px; color: #cfe2ff;
    font: 600 11px/1 system-ui, sans-serif; cursor: pointer; }
  .bc-open-sheet:hover { background: #1d3050; color: #eaf3ff; }

  .bc-params { position: absolute; top: 6px; left: 8px; z-index: 8; width: 156px;
    background: #0c0c18ee; border: 1px solid #2d2d4a; border-radius: 4px; overflow: hidden; }
  .bc-params.open { box-shadow: 0 6px 22px rgba(0,0,0,0.5); }
  .bc-params-h { width: 100%; text-align: left; background: #14223a; border: none;
    color: #cde; font-size: 11px; padding: 4px 8px; cursor: pointer; }
  .bc-params-h:hover { background: #1a2b48; }
  .bc-params-body { display: flex; flex-direction: column; gap: 3px; padding: 6px; max-height: 60vh; overflow-y: auto; }
  /* Развёрнутому фрейму нужна ширина: крупные значения и перевод в пункты не
     живут в 200px поповера. Список остаётся узким, как был. */
  .bc-params-body.wide { width: min(560px, 92vw); padding: 6px 0 8px; }
  .bc-view { display: inline-flex; align-self: flex-start; margin: 2px 0 6px 6px;
    border: 1px solid #24406a; border-radius: 4px; overflow: hidden; }
  .bc-view button { background: #0d1526; border: 0; color: #7c8cab; cursor: pointer;
    font: 600 11px/1 system-ui, sans-serif; padding: 6px 12px; }
  .bc-view button + button { border-left: 1px solid #24406a; }
  .bc-view button:hover { color: #cfe2ff; }
  .bc-view button.on { background: #16243c; color: #dfe8f7; }
  .bc-prow { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .bc-pk { font-size: 10px; color: #9ab; line-height: 1.1; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bc-pv { width: 52px; flex-shrink: 0; background: #0a1120; border: 1px solid #24406a; color: #cfe;
    font-size: 10px; border-radius: 3px; padding: 1px 4px; text-align: right; }
  .bc-pv:focus { outline: none; border-color: #4a7ad0; }
  /* Дата не влезала в 52px («01.0») — строку дат кладём вертикально, поле на всю
     ширину панели, чтобы дата показывалась целиком (1.5). */
  .bc-prow-date { flex-direction: column; align-items: stretch; gap: 1px; }
  .bc-prow-date .bc-pk { flex: none; }
  .bc-pv-date { width: 100%; box-sizing: border-box; text-align: left; padding: 2px 4px; }
  .bc-apply { margin-top: 4px; background: #1f5e3a; border: 1px solid #2e8b57; color: #cfe;
    font-size: 10px; border-radius: 3px; padding: 3px 6px; cursor: pointer; }
  .bc-apply:hover { background: #267346; }
  .bc-apply.dirty { background: #8a5a1f; border-color: #c8862f; }
  /* Ссылка на историю прогонов стоит ПОД кнопкой пересчёта: это соседний вопрос
     («что этот робот показывал раньше»), но не действие, поэтому не кнопка. */
  .bc-runs { display: block; margin-top: 4px; text-align: center; font-size: 10px;
    color: #7ab8ff; text-decoration: none; border: 1px solid #24406a; border-radius: 3px;
    padding: 3px 6px; background: #12203a; }
  .bc-runs:hover { border-color: #4dd0e1; color: #cff; }
  .bc-fav { display: flex; gap: 4px; margin-top: 6px; }
  .bc-fav .bc-pv { flex: 1; min-width: 0; }
  .bc-fav-btn { background: #1a1a2e; border: 1px solid #6a5a1f; color: #e0c36a;
    border-radius: 3px; font-size: 11px; padding: 3px 8px; cursor: pointer; white-space: nowrap; }
  .bc-fav-btn:hover { border-color: #c8a62f; }
  .bc-apply.live { background: #0e2a18; border-color: #2e7d32; color: #7ee2a0; font-weight: 600; }
  .bc-apply.live:disabled { opacity: .55; cursor: default; }
  .bc-apply-msg { margin-top: 4px; font-size: 10px; color: #9fd8b0; }
  .bc-fav-msg { color: #9aa0b4; font-size: 10px; margin-top: 3px; }

  .trade-tip {
    position: absolute; z-index: 8; pointer-events: none;
    background: #12121fee; border: 1px solid #3d3d5a; border-radius: 4px;
    padding: 5px 8px; font-size: 10px; white-space: nowrap;
    box-shadow: 0 4px 12px #000000aa;
  }
  .tt-head { color: #fff; font-weight: 700; margin-bottom: 3px; font-size: 11px; }
  .tt-head.tt-tp { color: #19e36a; }
  .tt-head.tt-sl { color: #ff3b3b; }
  .tt-head.tt-neutral { color: #fff; }
  .tt-sub { color: #aaa; font-family: monospace; }

  .stats-overlay {
    position: absolute; top: 6px; right: 92px; z-index: 5;
    background: #0f0f1ed9; border: 1px solid #2d2d4a; border-radius: 4px;
    padding: 5px 7px; display: flex; flex-direction: column; gap: 2px;
    backdrop-filter: blur(2px); min-width: 210px; max-width: 260px;
  }
  /* collapsed 2-line header (default) — clickable to expand */
  .st-toggle { display: block; width: 100%; background: none; border: none; padding: 0; cursor: pointer; text-align: left; }
  .st-head { display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: #999; }
  .st-head span:first-child { flex: 1; }
  .st-head b { font-size: 13px; }
  .st-chev { color: #6aa8ff; font-size: 11px; }
  .st-head2 { font-size: 10px; color: #667; margin-top: 1px; }
  .st-live { color: #8aa; } .st-live b { font-size: 10px; }
  .st-body { display: flex; flex-direction: column; gap: 2px; margin-top: 5px; border-top: 1px solid #2d2d4a; padding-top: 5px; }
  .st-row { display: flex; align-items: baseline; gap: 6px; font-size: 10px; color: #888; }
  .st-row span:first-child { flex: 1; }
  .st-row b { color: #ccc; font-size: 11px; }
  .st-comm-h b { font-size: 12px; }
  .st-sub { color: #555; font-size: 10px; }
  .st-sep { height: 1px; background: #2d2d4a; margin: 3px 0; }
  .st-trades-btn { margin-top: 6px; padding: 4px 8px; background: #12203a; border: 1px solid #24406a; color: #9cf; border-radius: 3px; font-size: 10px; cursor: pointer; }
  .st-trades-btn:hover { border-color: #6aa8ff66; color: #cfe; }
  .st-foot { font-size: 10px; color: #555; margin-top: 4px; font-style: italic; }
  .st-lvl-h { font-size: 10px; color: #9ab; margin: 2px 0 4px; }
  .st-lvl-h .st-sub { display: block; margin-top: 2px; line-height: 1.35; }
  .st-lvl-tbl { display: flex; flex-direction: column; gap: 1px; }
  .st-lvl-row { display: grid; grid-template-columns: 20px 1fr 1fr 1.1fr 1.5fr 1.5fr 1.5fr; gap: 4px; font-size: 10px; color: #aaa; align-items: baseline; }
  .st-lvl-head { color: #556; font-size: 9px; text-transform: uppercase; border-bottom: 1px solid #2d2d4a; padding-bottom: 2px; }
  .st-lvl-row .num { text-align: right; font-variant-numeric: tabular-nums; }
  .st-lvl-row .mono { font-family: ui-monospace, Consolas, monospace; color: #cde; }
  .pos { color: #4caf50; } .neg { color: #f44336; }

  /* full trades table overlay */
  .trades-pane { position: absolute; inset: 0; z-index: 12; background: #0a0a15f2; display: flex; flex-direction: column; }
  .tp-head { display: flex; align-items: center; justify-content: space-between; padding: 7px 10px; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .tp-title { font-size: 12px; color: #cde; font-weight: 600; }
  .tp-close { width: 24px; height: 24px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; cursor: pointer; }
  .tp-close:hover { color: #f44336; border-color: #f4433655; }
  .tp-wrap { flex: 1; overflow: auto; }
  .tp-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .tp-table th { position: sticky; top: 0; background: #0c0c18; color: #789; font-weight: 500; text-align: left; padding: 5px 10px; border-bottom: 1px solid #1e1e3a; white-space: nowrap; }
  .tp-table th.num, .tp-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .tp-table td { padding: 4px 10px; color: #aaa; border-bottom: 1px solid #14142a; white-space: nowrap; }
  .tp-table tr:hover td { background: #12122a; }

  .bt-equity-label {
    padding: 3px 10px; font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;
    background: #0f0f1e; border-top: 1px solid #1a1a2e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .bt-equity-carry { color: #9aa0b4; text-transform: none; letter-spacing: 0; }
  .bt-equity-blind { color: #e0a53c; text-transform: none; letter-spacing: 0; }
  .equity { flex: 0 0 auto; min-height: 0; }
  .bt-resizer { flex: 0 0 8px; cursor: ns-resize; background: #12203a;
    border-top: 1px solid #24406a; border-bottom: 1px solid #24406a; touch-action: none; }
  .bt-resizer:hover { background: #1c3054; }
  .tp-csv { background: #16162c; border: 1px solid #2d2d4a; color: #cde; margin-left: auto;
    margin-right: 8px; padding: 3px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  .tp-csv:hover { border-color: #4caf50; }

  .bt-scrollbar {
    position: relative; height: 14px; margin: 4px 10px; flex-shrink: 0;
    background: #1a1a2e; border: 1px solid #3a3a5a; border-radius: 7px;
  }
  .bt-thumb {
    position: absolute; top: 1px; bottom: 1px; min-width: 24px;
    background: #4a4a6e; border-radius: 6px; cursor: grab;
  }
  .bt-thumb:hover { background: #4caf5088; }
  .bt-thumb:active { cursor: grabbing; background: #4caf50aa; }

  .bt-hint { padding: 2px 10px; font-size: 10px; color: #555; background: #0f0f1e; border-top: 1px solid #1a1a2e; flex-shrink: 0; }

  .overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: #0a0a15cc; z-index: 10; font-size: 12px; color: #666; }
  .overlay.error { color: #f4433699; }
</style>
