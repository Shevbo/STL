<!-- AgentRobotScreen: full-page showcase of one AGENT-HOSTED robot ("все кишки").
     URL: /?agent_robot=<robot_id>[&agent=<agent_id>]
     Live chart (BacktestChart: candles + fills + working/planned order lines),
     signal internals (what the robot waits for, computed FVG features), planned
     orders, order history incl. rejected/skipped, latency pane. Polls the STL
     mirror every 5s; the agent's local state is the source of truth. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills, commissionFor, tradeEvents } from '../../lib/lab-analytics';
  import BacktestChart from './BacktestChart.svelte';
  import ScreenTag from './ScreenTag.svelte';
  import LatencyPane from './LatencyPane.svelte';
  import AgentBookPane from './AgentBookPane.svelte';
  import ParamEditor from './ParamEditor.svelte';
  import RobotIdentity from './RobotIdentity.svelte';
  import EquityChart from './EquityChart.svelte';
  import Splitter from './Splitter.svelte';
  import Frame from './Frame.svelte';
  import NavMenu from '../NavMenu.svelte';
  import { fetchAgentLocalStatus, type AgentLocalStatus } from '../../lib/agent-robots';
  import { annualizedPct, equityPaths, type EqPt as Pt } from '../../lib/lab-analytics';

  let { robotId, agentId = null }: { robotId: string; agentId?: string | null } = $props();

  const MSK_OFFSET = 3 * 3600;
  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);

  // ── Layout profiles (VS Code-style) + draggable frame sizes ──────────────────
  // 3 fixed profiles; EVERY frame border drags. Splitter persists each size to
  // localStorage by its own storageKey; the chosen profile is stored separately.
  // ponytail: fixed profiles (not free-form docking) — matches the VS Code mental
  // model and reuses the existing Splitter; add drag-drop docking only if asked.
  type Profile = 'stack' | 'chart-left' | 'chart-right';
  let profile = $state<Profile>('stack');
  try {
    const p = localStorage.getItem('ars_layout');
    if (p === 'stack' || p === 'chart-left' || p === 'chart-right') profile = p;
  } catch { /* default stack */ }
  function setProfile(p: Profile) { profile = p; try { localStorage.setItem('ars_layout', p); } catch {} }
  // Frame sizes (px); Splitter loads/persists via storageKey, these are the defaults.
  // st* = «stack» profile, sd* = «side» profiles (shared by chart-left/right mirror).
  let stChart = $state(440), stPing = $state(120), stDiag = $state(175), stSig = $state(300), stTrd = $state(430), stEq = $state(300);
  let sdChart = $state(1050), sdPing = $state(110), sdDiag = $state(150), sdSig = $state(190), sdTrd = $state(230), sdEq = $state(220);
  // ПЕРВОЕ ОТКРЫТИЕ: раскладываем фреймы ПО РАЗМЕРУ ЭКРАНА, а не по фиксированным
  // пикселям. Константы выше рассчитаны на один монитор: на широком снизу зияла
  // пустота, на ноутбуке нижний ряд схлопывался в полоски. Считаем доли от окна и
  // делим ряд поровну — все фреймы открыты и границы стоят равноудалённо.
  // Размеры, которые оператор УЖЕ подвинул (есть ключ в localStorage), не трогаем:
  // Splitter при монтировании возьмёт их и перезапишет наш расчёт.
  const SPLIT_PX = 6;                       // толщина разделителя
  function fitFrames() {
    const has = (k: string) => {
      try { return localStorage.getItem(k) != null; } catch { return true; }
    };
    // Рабочая область = окно минус шапка экрана и строка заголовков фреймов.
    const H = Math.max(520, window.innerHeight - 150);
    const W = Math.max(880, window.innerWidth - 16);
    // Стопка: график забирает 44% высоты (он главный), два узких пояса под ним,
    // остаток достаётся нижнему ряду.
    if (!has('ars_st_chart')) stChart = Math.round(H * 0.44);
    if (!has('ars_st_ping')) stPing = Math.round(Math.min(140, Math.max(64, H * 0.09)));
    if (!has('ars_st_diag')) stDiag = Math.round(Math.min(220, Math.max(96, H * 0.14)));
    // Нижний ряд: 4 фрейма (сигнал, сделки, доходность, логика) делят ширину
    // поровну; последний тянется остатком, поэтому задаём три первых.
    const col = Math.max(160, Math.round((W - 3 * SPLIT_PX) / 4));
    if (!has('ars_st_sig')) stSig = col;
    if (!has('ars_st_trd')) stTrd = col;
    if (!has('ars_st_eq')) stEq = col;
    // Боковые профили: график 55% ширины, правая колонка делит высоту на 6 равных
    // (пинг, диагностика, сигнал, сделки, доходность, логика-остаток).
    if (!has('ars_sd_chart')) sdChart = Math.round(W * 0.55);
    const row = Math.max(80, Math.round((H - 5 * SPLIT_PX) / 6));
    if (!has('ars_sd_ping')) sdPing = Math.min(row, 150);
    if (!has('ars_sd_diag')) sdDiag = row;
    if (!has('ars_sd_sig')) sdSig = row;
    if (!has('ars_sd_trd')) sdTrd = row;
    if (!has('ars_sd_eq')) sdEq = row;
  }
  if (typeof window !== 'undefined') fitFrames();
  // Which frame (if any) is maximized to the whole work area. Shared across all
  // Frame wrappers; a Frame hides itself when another id owns the maximize.
  let maxId = $state<string | null>(null);

  let report = $state<any>(null);
  let localStatus = $state<AgentLocalStatus | null>(null);
  let strategyDesc = $state<string>('');
  let error = $state('');

  // QUIK-link health + recon (agent vs QUIK account tables) for this robot.
  const health = $derived(localStatus?.health ?? null);
  const reconState = $derived(localStatus?.recon?.state ?? null);
  const reconCheck = $derived.by(() =>
    (localStatus?.recon?.robot_checks ?? []).find((c: any) => c.id === robotId) ?? null);
  const manualBlock = $derived(localStatus?.recon?.manual ?? null);

  const robot = $derived((report?.robots ?? []).find((r: any) => r.robot_id === robotId) ?? null);
  const signal = $derived.by(() => {
    try { return robot?.signal_json ? JSON.parse(robot.signal_json) : null; } catch { return null; }
  });
  // Статистика фильтров входа приезжает ВНУТРИ signal_json (раннер кладёт её туда,
  // чтобы не менять proto). saved_pts в ПУНКТАХ — в рубли переводим тем же ₽/пункт,
  // что и P&L робота; без коэффициента показываем пункты, а не врём рублями.
  const filterStats = $derived.by(() => {
    const fs = signal?.filter_stats;
    if (!fs) return null;
    return { ...fs, savedRub: pointCoef != null ? Number(fs.saved_pts) * pointCoef : null };
  });
  const params = $derived.by(() => {
    try { return robot?.params_json ? JSON.parse(robot.params_json) : {}; } catch { return {}; }
  });
  const symbol = $derived(robot?.symbol || 'RIU6');
  const position = $derived(Number(robot?.position ?? 0));
  const avgPrice = $derived(Number(robot?.avg_price ?? 0));
  // Floating (variation margin) on the OPEN position, marked to the freshest price:
  // signed contracts × (price − avg) × ₽/point.
  const floatRub = $derived(
    position !== 0 && avgPrice > 0 && pointCoef && liveTick?.p
      ? position * (liveTick.p - avgPrice) * pointCoef : null);
  // Commission to hit the market and close the whole position now (taker).
  const closeComm = $derived(
    position !== 0 && pointCoef && liveTick?.p
      ? commissionFor(symbol, liveTick.p, Math.abs(position), pointCoef, true) : 0);
  // «P&L + Маржа»: realized + (floating − exit commission) = what you keep if you
  // flatten at market right now. Uses the runner's AUTHORITATIVE realized (pnlRub),
  // not the chart's tail-replay (which is wrong for a long-history live robot).
  const pnlMargin = $derived(
    pnlRub !== null ? pnlRub + (floatRub ?? 0) - closeComm : null);
  // net floating (after the exit commission) — passed to the chart panel.
  const floatNetRub = $derived(floatRub !== null ? floatRub - closeComm : null);
  const heartbeatAge = $derived.by(() => {
    const hb = Number(robot?.heartbeat_unix_ms ?? 0);
    return hb ? Math.round((Date.now() - hb) / 1000) : null;
  });

  // Журнал сделок робота (algo_trades) — авторитетные ₽ по каждому филлу. Ключ —
  // номер заявки QUIK; одна заявка может дать несколько сделок (частичные исполнения),
  // поэтому суммируем. Пусто = журнал ещё не проглотил филл (инжест раз в 30с) или
  // сделка старше журнала: тогда ₽ по строке НЕ показываем вовсе.
  let ledgerByOrder = $state(new Map<string, { gross: number; comm: number }>());
  async function loadLedger() {
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/algo-trades?robot_id=${encodeURIComponent(robotId)}&limit=1000`,
        // 6 с не хватало: у активного робота это ~200 КБ, и на тонком канале
        // запрос обрывался. Молча — а карточка после этого переключалась на
        // заведомо неверный пересчёт обрезанного хвоста и рисовала его как правду.
        { signal: AbortSignal.timeout(20000) } as any);
      if (!res.ok) return;
      const d = await res.json();
      const m = new Map<string, { gross: number; comm: number }>();
      for (const r of d.trades ?? []) {
        const k = String(r.order_num ?? '');
        if (!k) continue;
        const prev = m.get(k) ?? { gross: 0, comm: 0 };
        m.set(k, { gross: prev.gross + Number(r.pnl_gross_rub ?? 0),
                   comm: prev.comm + Number(r.commission_rub ?? 0) });
      }
      ledgerByOrder = m;
      // Строго по ВРЕМЕНИ: журнал отдаёт по seq, а дозаполненная история вставлена
      // позже (seq выше) при более ранних датах — reverse() дал бы неверный порядок.
      ledgerRows = [...(d.trades ?? [])].sort((a: any, b: any) => Number(a.ts_ms) - Number(b.ts_ms));
    } catch { /* журнал недоступен -> строки останутся без ₽, но не с ВРАНЬЁМ */ }
  }
  let ledgerRows = $state<any[]>([]);
  // Журнал годится как источник для ГРАФИКА только если его цепочка начинается С НУЛЯ:
  // тогда replay-с-нуля в графике совпадает с реальной историей позиции. Проверяем по
  // первой строке: pos_after должен равняться её же дельте.
  const ledgerFromFlat = $derived.by(() => {
    const f = ledgerRows[0];
    if (!f) return false;
    return Number(f.pos_after) === (f.side === 'buy' ? Number(f.qty) : -Number(f.qty));
  });

  // ЖИВОЙ робот платит МЕЙКЕРСКИЙ тариф (брокер), а не тейкерский (биржа +
  // брокер): в график уходит taker={false}. Тейкерская модель приписывала роботу
  // биржевой сбор, которого он не платил. См. «Commission model» в CLAUDE.md.
  //
  // Кривая доходности берётся ИЗ ЖУРНАЛА: у него настоящая комиссия по каждому
  // филлу и непрерывная позиция. Пересчёт филлов по средней цене годится для
  // ярлыков сделок, но не для денег — он давал свой дрейф на каждой модели
  // комиссии, а на частичных выходах ещё и переплачивал комиссию входа.
  const closeSeries = $derived.by(() => {
    if (!ledgerFromFlat) return null;
    return ledgerRows
      .filter((r: any) => Number(r.pnl_net_rub) !== 0)
      .map((r: any) => ({ time: Math.floor(Number(r.ts_ms) / 1000) + MSK_OFFSET,
                          pnl: Number(r.pnl_net_rub) }));
  });

  // mirror fills -> trade rows -> chart fills (+MSK shift onto MSK-stamped bars)
  const trades = $derived((robot?.recent_fills ?? []).map((f: any) => ({
    time: Math.floor(Number(f.ts_unix_ms ?? 0) / 1000),
    iso: new Date(Number(f.ts_unix_ms ?? 0)).toISOString(),
    symbol: f.symbol || symbol,
    side: f.side === 'SIDE_SELL' ? 'sell' : 'buy',
    qty: Number(f.qty ?? 0),
    price: Number(f.price ?? 0),
    order_id: f.order_id ?? '',
    status: f.status ?? '',
  })));
  // REAL robot: the chart + "Результат" must count ONLY QUIK-confirmed fills
  // (status 'filled'). The runner's 200-tail also carries the PAPER-era history
  // (kept in the trades TABLE, labelled) — replaying paper+real through one book
  // fabricated "макс. позиция 2 конт" and a mixed-era net. Paper robots keep
  // counting their paper fills.
  // ИСТОЧНИК ГРАФИКА — ЖУРНАЛ, когда его цепочка начинается с нуля. Хвост зеркала
  // обрезан 200 филлами: replay-с-нуля по нему стартует ПОСЕРЕДИНЕ позиции, поэтому
  // почти каждое закрытие выглядело убыточным (стена ярлыков «SL» у стратегии, где
  // стоп-лосса нет вообще), а кривая доходности потом ещё и МАСШТАБИРОВАЛАСЬ, чтобы
  // упереться в пожизненный реализованный P&L — рисуя внутри окна график, которого не
  // было. Журнал ведёт позицию/среднюю непрерывно и хранит ₽/пункт в строке.
  const chartFills = $derived.by(() => {
    if (ledgerFromFlat) {
      return ledgerRows.map((r: any) => ({
        time: Math.floor(Number(r.ts_ms) / 1000) + MSK_OFFSET,
        side: r.side, qty: Number(r.qty), price: Number(r.price),
        order_id: String(r.order_num ?? ''),
      }));
    }
    return toFills(trades.filter((t: any) =>
      robot?.paper ? EXECUTED.has(t.status) : t.status === 'filled'))
      .map((f: any) => ({ ...f, time: f.time + MSK_OFFSET }));
  });

  // Per-fill lifecycle role (OPEN / AVG / ENF / TP / SL) + realized P&L on closes,
  // for the "Сделки робота" table. REUSES tradeEvents — the SAME classifier that
  // draws the chart markers — so table and chart never disagree. Joined to the raw
  // fills by INDEX over the executed subset (filled/paper), which tradeEvents keeps
  // in chronological order. Rejected/skipped fills change no position -> no action.
  function mapAction(e: any): { action: string; cls: string; pnl: number | null } {
    switch (e.kind) {
      case 'open':    return { action: 'OPEN', cls: 'a-open', pnl: null };
      case 'average': return { action: 'AVG',  cls: 'a-avg',  pnl: null };
      case 'enforce': return { action: 'ENF',  cls: 'a-enf',  pnl: null };
      case 'partial': return { action: (e.close?.exit ?? '') + ' ч.', cls: e.close?.exit === 'TP' ? 'a-tp' : 'a-sl', pnl: e.close?.pnl ?? null };
      case 'full':    return { action: e.close?.exit ?? '',           cls: e.close?.exit === 'TP' ? 'a-tp' : 'a-sl', pnl: e.close?.pnl ?? null };
      case 'reverse': return { action: (e.close?.exit ?? '') + '→OPEN', cls: e.close?.exit === 'TP' ? 'a-tp' : 'a-sl', pnl: e.close?.pnl ?? null };
      default:        return { action: '', cls: '', pnl: null };
    }
  }
  const tradeActions = $derived.by(() => {
    const isPaper = !!robot?.paper;
    const idx: number[] = [];
    const fills: any[] = [];
    trades.forEach((t: any, i: number) => {
      if (isPaper ? EXECUTED.has(t.status) : t.status === 'filled') {
        idx.push(i);
        fills.push({ time: t.time, side: t.side, qty: t.qty, price: t.price, order_id: t.order_id });
      }
    });
    // Agent robots trade MARKETABLE (cross the spread) in BOTH paper and real, so the
    // per-trade P&L is netted with the TAKER model — matching the runner's realized and
    // the backtest (paper used to net maker here, disagreeing with the runner).
    const evs = tradeEvents(fills, 60, pointCoef ?? 1, symbol, true);
    const m = new Map<number, { action: string; cls: string; pnl: number | null; comm: number | null }>();
    evs.forEach((e: any, k: number) => {
      // Commission (₽) of THIS fill — every fill pays it; shown per row + summed below.
      const comm = pointCoef != null ? commissionFor(symbol, e.price, e.qty, pointCoef, true) : null;
      m.set(idx[k], { ...mapAction(e), comm });
    });
    // P&L по строкам берём из ЖУРНАЛА (algo_trades), а не из этого replay: хвост
    // зеркала обрезан 200 филлами, поэтому replay-с-нуля стартует ПОСЕРЕДИНЕ позиции
    // и считает каждое закрытие от неверной средней. Живой MACD·RIU6 2026-07-22:
    // колонка суммировала -28 000 ₽ при фактических +21 617 ₽ по журналу. Журнал
    // ведёт среднюю непрерывно и хранит ₽/пункт в строке. Ярлык действия (AVG/TP/SL)
    // берём по-прежнему из replay — он про ФОРМУ сделки, а не про деньги.
    if (ledgerByOrder.size) {
      for (const [i, meta] of m) {
        const led = ledgerByOrder.get(String(trades[i]?.order_id ?? ''));
        m.set(i, { ...meta, pnl: led ? led.gross : null, comm: led ? led.comm : null });
      }
    }
    return m;
  });
  // Detailed-log totals (₽): commission paid across all fills + net realized on closes.
  const tradeTotals = $derived.by(() => {
    let comm = 0, net = 0;
    for (const [, meta] of tradeActions) {
      if (meta?.comm != null) comm += meta.comm;
      if (meta?.pnl != null) net += meta.pnl;
    }
    return { comm, net };
  });
  // trades newest-last; attach each row's action so the template can reverse freely.
  const tradeRows = $derived(trades.map((t: any, i: number) => ({ ...t, meta: tradeActions.get(i) ?? null })));

  // STABLE-IDENTITY chart props. BacktestChart fully reloads on every new `result`
  // object; the 5s mirror poll must NOT recreate props unless the CONTENT changed
  // (otherwise the chart lives in a permanent "загрузка" loop).
  let chartResult = $state<any>(null);
  let openOrders = $state<any[]>([]);
  let plannedOrders = $state<any[]>([]);
  let armedOrders = $state<any[]>([]);   // fire-on-signal (market), NOT price levels
  let _fpChart = '';
  let _fpOrders = '';
  $effect(() => {
    const fills = chartFills;
    // Include the authoritative net (pnlRub) so the chart RE-ANCHORS its equity
    // curve when the ₽/point coef finally loads (flaky VDS fetch): otherwise the
    // curve draws once with netOverride=null (raw tail-replay) and never re-scales
    // until the next fill — the curve showed -34452 while the badge was -42431.
    const fp = JSON.stringify([symbol, fills.length,
      fills.at(-1)?.time ?? 0, fills.at(-1)?.price ?? 0, params,
      pnlRub == null ? 0 : Math.round(pnlRub)]);
    if (fp !== _fpChart) {
      _fpChart = fp;
      chartResult = { trades: fills, equity_curve: [], params };
    }
  });
  $effect(() => {
    const open = (robot?.working_orders ?? []).map((w: any) => ({
      side: w.side === 'SIDE_SELL' ? 'sell' : 'buy',
      price: Number(w.price ?? 0), qty: Number(w.qty ?? 0), order_id: w.order_id ?? '',
    }));
    // Chart lines: ONLY real price levels (planned_orders, e.g. TP). "Armed" orders
    // are NOT price triggers — FVG fires on a closed-bar pattern, not on a level
    // touch — so drawing them as horizontal lines misled the operator ("price
    // crossed the line 5 times, robot ignores"). They live in the side panel only.
    const planned: any[] = [];
    for (const p of signal?.planned_orders ?? [])
      planned.push({ side: p.side, price: p.price, qty: p.qty, reason: p.reason });
    // A standalone-module strategy (us_open_fvg…) keeps its EXACT exit levels in
    // the strategy state, not in planned_orders — draw those too, they are real
    // price triggers the robot acts on (operator: «если точный расчёт — рисовать
    // на графике и показывать в этом фрейме»).
    const el = signal?.exit_levels;
    if (el && el.tp && el.sl) {
      const closeSide = el.dir > 0 ? 'sell' : 'buy';
      const qty = Math.abs(Number(robot?.position ?? 0)) || 1;
      planned.push({ side: closeSide, price: el.tp, qty, reason: 'тейк-профит (TP) — выход в плюс' });
      planned.push({ side: closeSide, price: el.sl, qty, reason: 'стоп-лосс (SL) — выход в минус' });
    }
    const armedList: any[] = [];
    for (const p of signal?.armed ?? [])
      armedList.push({ side: p.side, price: p.price, qty: p.qty, reason: p.reason });
    const fp = JSON.stringify([open, planned, armedList]);
    if (fp !== _fpOrders) {
      _fpOrders = fp;
      openOrders = open;
      plannedOrders = planned;
      armedOrders = armedList;
    }
  });

  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const dateFrom = iso(new Date(today.getTime() - 3 * 86400_000));
  const dateTo = iso(new Date(today.getTime() + 86400_000));

  function fmtMskTime(ms: number): string {
    return new Date(ms).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow',
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  // Freshest tick, passed into the chart in CHART-AXIS time (bars are MSK-stamped
  // epochs; tick received_at is true UTC ms -> shift +3h). Drives the forming candle.
  let liveTick = $state<{ t: number; p: number } | null>(null);
  // ₽ per price POINT (step_cost/price_step from the QLua params feed). The runner
  // accumulates realized P&L in PRICE POINTS; the UI converts to honest rubles.
  // The coef is stored WITH the symbol it belongs to and is only ever read back for
  // that same symbol. A bare `pointCoef` number kept the LAST resolved instrument's
  // value: the card mounts before the robot loads (symbol = another instrument), and
  // if the robot's own resolution then failed/arrived late, the stale coef stayed and
  // the card multiplied this robot's POINTS by a FOREIGN ₽/point. Seen live on
  // agent-ob-BRU6-v1: -7.11 pts × 1.57108 (RIU6) = "-11 ₽" instead of × 785.54
  // (BRU6) = -5 585 ₽ — a 500x understatement of a real loss. Symbol-bound state
  // makes that structurally impossible: unknown coef renders as nothing, never as ₽.
  let coefFor = $state<{ sym: string; coef: number } | null>(null);
  const pointCoef = $derived(coefFor && coefFor.sym === symbol ? coefFor.coef : null);
  async function loadCoef(sym: string = symbol) {
    if (!sym) return;
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
      const res = await fetchWithAuth(`/api/v1/quik/params${q}`,
        { signal: AbortSignal.timeout(4000) } as any);
      if (!res.ok) return;
      const d = await res.json();
      const row = (d.rows ?? []).find((r: any) => r.code === sym);
      if (row?.coef > 0) coefFor = { sym, coef: Number(row.coef) };
    } catch { /* optional; ₽ figures stay hidden until the coef is known */ }
  }
  // Re-resolve whenever the robot's symbol becomes known/changes, and keep retrying
  // while it is still unknown (a params gap must not leave the card ₽-less forever).
  $effect(() => { void loadCoef(symbol); });
  $effect(() => {
    if (pointCoef != null || !symbol) return;
    const id = setInterval(() => void loadCoef(symbol), 15_000);
    return () => clearInterval(id);
  });
  const pnlPoints = $derived(Number(robot?.realized_pnl ?? 0));
  const pnlRub = $derived(pointCoef ? pnlPoints * pointCoef : null);

  // «Доходность в год»: (фикс + ВМ) к МАКСИМАЛЬНОМУ ГО, хоть раз задействованному,
  // линейно приведённая к году. Пик контрактов и дату старта берём из ЖУРНАЛА
  // (зеркало хранит только хвост филлов — по нему пик не восстановить), ГО/контракт
  // — из QLua-параметров агента.
  let ledgerStat = $state<{ peak: number; first_ts: number } | null>(null);
  async function loadLedgerStat() {
    try {
      const res = await fetchWithAuth('/api/v1/quik/algo-robot-stats?mode=real',
        { signal: AbortSignal.timeout(6000) } as any);
      if (!res.ok) return;
      const d = await res.json();
      const s = d[robotId];
      if (s) ledgerStat = { peak: Number(s.peak ?? 0), first_ts: Number(s.first_ts ?? 0) };
    } catch { /* без журнала просто не покажем годовую */ }
  }
  $effect(() => { void robotId; void loadLedgerStat(); });
  const marginPer = $derived(
    (status?.health?.params ?? []).find((p: any) => p.code === symbol)?.margin ?? 0);
  const maxGo = $derived(marginPer * (ledgerStat?.peak ?? 0));
  const annPct = $derived(
    pnlMargin == null ? null : annualizedPct(pnlMargin, maxGo, ledgerStat?.first_ts ?? 0));
  const annDays = $derived(ledgerStat?.first_ts
    ? Math.max(1, Math.round((Date.now() - ledgerStat.first_ts) / 86_400_000)) : null);
  // pnlRub is the robot's AUTHORITATIVE realized — the agent's OWN number
  // (realized_pnl × ₽/point), matching its 127.0.0.1:8071 page. Both the header
  // badge and the chart's «Результат» (via netOverride) use it, so they can never
  // diverge. The chart's tail-replay net was WRONG for a long-history live robot
  // (>200 fills: the mirror tail starts mid-position, so replay-from-flat mis-
  // attributes P&L — showed -36 574 ₽ vs the agent's own -11 444 ₽).
  let tickAge = $state<number | null>(null);
  let mirrorAge = $state<number | null>(null);

  const FETCH_TO = 2500;   // ms; a hung request must never freeze the update loops

  // Exchange price step from a book ladder: min positive gap between adjacent
  // levels of ONE side (levels are step-multiples; empty levels are omitted, so
  // the minimum over the ladder converges to the true step).
  function inferStep(prices: number[]): number {
    const s = [...new Set(prices)].sort((a, b) => a - b);
    let min = 0;
    for (let i = 1; i < s.length; i++) {
      const d = s[i] - s[i - 1];
      if (d > 0 && (min === 0 || d < min)) min = d;
    }
    return min;
  }

  // Price display: keep the instrument's real precision. Math.round truncated the
  // cents on a sub-integer instrument (BRU6 showed 87 for a real fill of 86.63).
  // ponytail: 2 dp fits every FORTS instrument we trade (BR tick 0.01, the rest
  // integer); switch to a price-step-derived precision if a 0.001-tick one (NG) lands.
  const fmtPrice = (p: number) => Number(p).toLocaleString('ru-RU', { maximumFractionDigits: 2 });

  async function load() {
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
      const res = await fetchWithAuth(`/api/v1/quik/robots-mirror${q}`,
        { signal: AbortSignal.timeout(6000) } as any);  // mirror can queue behind heavy bar fetches
      if (!res.ok) { error = `mirror: HTTP ${res.status}`; return; }
      report = await res.json();
      mirrorAge = report?.received_at_ms ? Math.round((Date.now() - Number(report.received_at_ms)) / 1000) : null;
      error = '';
    } catch (e) { error = `mirror: ${String(e).slice(0, 60)}`; }
  }

  async function pollTick() {
    const sym = symbol;
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    let price = 0, ms = 0;
    try {
      const res = await fetchWithAuth(`/api/v1/quik/tick/${encodeURIComponent(sym)}${q}`,
        { signal: AbortSignal.timeout(FETCH_TO) } as any);
      if (res.ok) {
        const d = await res.json();
        price = Number(d.last || 0) || ((Number(d.bid || 0) && Number(d.ask || 0))
          ? (Number(d.bid) + Number(d.ask)) / 2 : Number(d.bid || d.ask || 0));
        ms = Number(d.received_at_unix_ms ?? 0);
      }
    } catch { /* fall through to the book */ }
    // The params-sheet tick has a history of dying while the per-code order-book
    // sheets stay alive. When the tick is stale (>15s), take the book instead —
    // the chart and the стакан then can NEVER diverge for long. The proxy price
    // is QUANTIZED to the instrument's price step inferred from the book ladder:
    // an off-grid "price" (89175 on a 10-step instrument) does not exist on the
    // exchange and must never be drawn. No inferable step -> best bid (real level).
    if (!ms || Date.now() - ms > 15_000) {
      try {
        const res = await fetchWithAuth(`/api/v1/quik/orderbook/${encodeURIComponent(sym)}${q}`,
          { signal: AbortSignal.timeout(FETCH_TO) } as any);
        if (res.ok) {
          const d = await res.json();
          const bidP = (d.bids ?? []).map((l: any) => Number(l.price)).filter((p: number) => p > 0);
          const askP = (d.asks ?? []).map((l: any) => Number(l.price)).filter((p: number) => p > 0);
          const step = Math.min(inferStep(bidP) || Infinity, inferStep(askP) || Infinity);
          const bb = bidP[0] ?? 0, ba = askP[0] ?? 0;
          const bookMs = Number(d.received_at_unix_ms ?? 0);
          let p = 0;
          if (bb > 0 && ba > 0) {
            p = Number.isFinite(step) && step > 0
              ? Math.round((bb + ba) / 2 / step) * step
              : bb;
          } else { p = bb || ba; }
          if (p > 0 && bookMs > ms) { price = p; ms = bookMs; }
        }
      } catch { /* next second retries */ }
    }
    tickAge = ms ? Math.max(0, Math.round((Date.now() - ms) / 1000)) : null;
    if (price > 0 && ms > 0)
      liveTick = { t: Math.floor(ms / 1000) + MSK_OFFSET, p: price };
  }

  async function loadStatus() {
    // agent local status (QUIK-link health + recon). Never throws (returns null).
    localStatus = await fetchAgentLocalStatus(agentId);
  }

  // Operator flatten: market-close the whole position + pause. Real money -> confirm.
  let flattening = $state(false);
  let flattenMsg = $state('');
  async function flattenNow() {
    if (!window.confirm(
      `ЗАКРЫТЬ ВСЮ позицию робота по рынку и ОСТАНОВИТЬ его?\n` +
      `Позиция: ${position > 0 ? '+' : ''}${position} конт. Это РЕАЛЬНЫЕ деньги.\n` +
      `Робот встанет на паузу до нажатия «Пуск».`)) return;
    flattening = true; flattenMsg = '';
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/robots/${encodeURIComponent(robotId)}/flatten-agent`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: agentId }) });
      flattenMsg = res.ok ? 'Закрытие по рынку отправлено. Робот на паузе.'
                          : `Ошибка: ${res.status}`;
    } catch (e) { flattenMsg = `Ошибка: ${String(e).slice(0, 80)}`; }
    finally { flattening = false; }
  }
  async function startRobot() {
    flattenMsg = '';
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/robots/${encodeURIComponent(robotId)}/start-agent`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: agentId }) });
      flattenMsg = res.ok ? 'Пуск отправлен.' : `Ошибка: ${res.status}`;
    } catch (e) { flattenMsg = `Ошибка: ${String(e).slice(0, 80)}`; }
  }
  // Пауза БЕЗ закрытия: блокирует новые входы, открытая позиция остаётся как есть
  // (в отличие от «Закрыть всё + стоп»). Тот же pause-agent, что и operator-pause.
  let pausing = $state(false);
  async function pauseRobot() {
    flattenMsg = ''; pausing = true;
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/robots/${encodeURIComponent(robotId)}/pause-agent`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: agentId }) });
      flattenMsg = res.ok ? 'Пауза отправлена. Позиция остаётся открытой.' : `Ошибка: ${res.status}`;
    } catch (e) { flattenMsg = `Ошибка: ${String(e).slice(0, 80)}`; }
    finally { pausing = false; }
  }
  // «ТОЛЬКО НА ВЫХОД»: робот доводит открытую позицию до закрытия по своему же
  // сигналу и не открывает новую. Пауза для этого не годится — она замораживает
  // робота ВМЕСТЕ с позицией. Нужен на экспирации, при разводе встречных роботов
  // (кросс-заявки) и перед выводом в бумагу. Снимается «Пуском».
  let exitBusy = $state(false);
  const exitOnly = $derived.by(() => {
    try { return !!JSON.parse(robot?.params_json || '{}').exit_only; } catch { return false; }
  });
  async function setExitOnly(on: boolean) {
    flattenMsg = ''; exitBusy = true;
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/robots/${encodeURIComponent(robotId)}/exit-only`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: agentId, on }) });
      flattenMsg = res.ok
        ? (on ? 'Режим «только на выход». Робот закроет позицию по своему сигналу и новых открывать не будет.'
              : 'Обычный режим восстановлен.')
        : `Ошибка: ${res.status}`;
    } catch (e) { flattenMsg = `Ошибка: ${String(e).slice(0, 80)}`; }
    finally { exitBusy = false; }
  }
  // Operator belief-correction from the STL stand: force the runner's believed position
  // to reality (e.g. 0 after a manual close the robot never emitted). BELIEF-ONLY — the
  // agent relays it as a runner fix_state, NEVER a real order. Server + agent both gate
  // on the robot being PAUSED + confirm_id == robotId, so the button only shows on pause.
  let posBusy = $state(false);
  async function zeroBelief() {
    const raw = window.prompt(
      `Записать роботу ВЕРУ о позиции (только вера, не реальный ордер).\n` +
      `id: ${robotId}\nТекущая вера: ${position}. Новая позиция (обычно 0):`, '0');
    if (raw === null) return;
    const pos = Number(raw);
    if (!Number.isInteger(pos)) { flattenMsg = 'Позиция должна быть целым числом.'; return; }
    let avg = 0;
    if (pos !== 0) {
      const a = window.prompt(`Средняя цена для позиции ${pos}:`, String(robot?.avg_price ?? 0));
      if (a === null) return;
      avg = Number(a);
      if (!(avg > 0)) { flattenMsg = 'Для ненулевой позиции нужна средняя цена > 0.'; return; }
    }
    const conf = window.prompt(`Подтверди: впиши точный ID робота\n${robotId}`, '');
    if (conf !== robotId) { flattenMsg = 'ID не совпал — отменено.'; return; }
    posBusy = true; flattenMsg = '';
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/robots/${encodeURIComponent(robotId)}/set-position-agent`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: agentId, position: pos, avg_price: avg, confirm_id: robotId }) });
      flattenMsg = res.ok ? `Вера обновлена: позиция ← ${pos}. Проверь через пару секунд.`
                          : `Ошибка: ${res.status}`;
    } catch (e) { flattenMsg = `Ошибка: ${String(e).slice(0, 80)}`; }
    finally { posBusy = false; }
  }
  // Record a MANUAL trade the robot never emitted (e.g. a position the operator closed
  // by hand in their own QUIK terminal). REALIZES P&L + lands in the fill history — the
  // agent rides the real fill path. NO exchange order. Server + agent gate on PAUSED +
  // confirm. Time is entered as HH:MM MSK (today) -> epoch ms (MSK = UTC+3).
  let fillBusy = $state(false);
  async function recordFill() {
    const sideRaw = window.prompt(
      `Записать РУЧНУЮ сделку (реализует P&L + в историю; реальный ордер НЕ выставляется).\n` +
      `id: ${robotId}\nСторона: "sell" (продажа/закрытие лонга) или "buy" (покупка):`,
      position > 0 ? 'sell' : 'buy');
    if (sideRaw === null) return;
    const side = sideRaw.trim().toLowerCase();
    if (side !== 'buy' && side !== 'sell') { flattenMsg = 'Сторона — buy или sell.'; return; }
    const qRaw = window.prompt('Кол-во контрактов:', String(Math.abs(position) || 1));
    if (qRaw === null) return;
    const qty = Number(qRaw);
    if (!Number.isInteger(qty) || qty <= 0) { flattenMsg = 'Кол-во — целое > 0.'; return; }
    const pRaw = window.prompt('Цена сделки:', String(robot?.last_close ?? ''));
    if (pRaw === null) return;
    const price = Number(pRaw);
    if (!(price > 0)) { flattenMsg = 'Цена должна быть > 0.'; return; }
    const tRaw = window.prompt('Время сделки ЧЧ:ММ по МСК (сегодня). Пусто = сейчас:', '');
    if (tRaw === null) return;
    let ts_unix_ms = 0;
    const m = tRaw.trim().match(/^(\d{1,2}):(\d{2})$/);
    if (m) {
      const d = new Date();
      ts_unix_ms = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(),
                            Number(m[1]) - 3, Number(m[2]), 0);  // MSK = UTC+3
    }
    const conf = window.prompt(`Подтверди: впиши точный ID робота\n${robotId}`, '');
    if (conf !== robotId) { flattenMsg = 'ID не совпал — отменено.'; return; }
    if (!window.confirm(
      `Записать ${side.toUpperCase()} ${qty} @ ${price}${m ? ' в ' + tRaw.trim() + ' МСК' : ''}?\n` +
      `Реализует P&L робота. Реальный ордер НЕ выставляется.`)) return;
    fillBusy = true; flattenMsg = '';
    try {
      const res = await fetchWithAuth(
        `/api/v1/quik/robots/${encodeURIComponent(robotId)}/record-fill-agent`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_id: agentId, side, qty, price,
            ts_unix_ms: ts_unix_ms || undefined, confirm_id: robotId }) });
      flattenMsg = res.ok ? `Сделка записана: ${side} ${qty} @ ${price}. Статистика обновится через пару секунд.`
                          : `Ошибка: ${res.status}`;
    } catch (e) { flattenMsg = `Ошибка: ${String(e).slice(0, 80)}`; }
    finally { fillBusy = false; }
  }
  // Clone this robot's exact strategy+params+symbol into a fresh PAPER robot on the
  // agent. deploy-agent needs no scriptCode/STL record — the runner resolves the
  // strategy by id. New robot_id must be colon-free (attribution parses on ':').
  let cloneBusy = $state(false);
  let cloneMsg = $state('');
  async function cloneToPaper() {
    const sid = robot?.strategy_id;
    if (!sid) { cloneMsg = 'Нет strategy_id — робот ещё не отобразился из зеркала.'; return; }
    if (!window.confirm(`Скопировать параметры робота «${robotId}» и запустить НОВЫЙ робот в PAPER?\nСтратегия: ${sid} · ${symbol}. Реальные деньги не задействуются.`)) return;
    const newId = `${sid}-${symbol}-c${Date.now().toString(36)}`.replace(/[^A-Za-z0-9_-]/g, '');
    cloneBusy = true; cloneMsg = '';
    try {
      const res = await fetchWithAuth(`/api/v1/quik/robots/${encodeURIComponent(newId)}/deploy-agent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, strategy_id: sid, params: { ...params, symbol },
          symbol, schedule: robot?.schedule ?? '09:00-23:55',
          max_position: Math.max(1, Number(robot?.max_position ?? 1) || 1), paper: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      cloneMsg = `Развёрнут в PAPER: «${newId}».`;
    } catch (e) { cloneMsg = `Ошибка клонирования: ${String(e).slice(0, 80)}`; }
    finally { cloneBusy = false; }
  }

  // Display name overlay (agent robots have no name of their own — robot_id is the key).
  const displayName = $derived(robot?.display_name || robotId);
  async function renameRobot() {
    const cur = (robot?.display_name as string) || '';
    const next = window.prompt(`Имя робота (id: ${robotId})\nПусто = вернуть к id:`, cur);
    if (next === null) return;
    await fetchWithAuth(`/api/v1/quik/robots/${encodeURIComponent(robotId)}/rename`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: next.trim() }),
    });
    await load();   // reflect immediately
  }

  // ---- params editor (GUI, no hand-JSON): params apply next bar; changing
  // max_position/schedule relays a full spec re-deploy (zero-loss). ----
  let editMode = $state(false);
  let draft = $state<Record<string, any>>({});
  let draftMaxPos = $state<number>(1);
  let draftSchedule = $state('');
  let saving = $state(false);
  let saveMsg = $state('');

  function startEdit() {
    draft = { ...params };
    draftMaxPos = Number(robot?.max_position ?? 1) || 1;
    draftSchedule = robot?.schedule ?? '09:00-23:55';
    saveMsg = '';
    editMode = true;
  }

  // Применить параметры ИЗ ПАНЕЛИ НА ГРАФИКЕ. Там оператор правит те же значения,
  // но кнопки сохранения не было вовсе: поля редактируются, а деть их некуда.
  // Дальше идём тем же путём, что и редактор «Логика стратегии» — /params.
  let chartApplyBusy = $state(false);
  let chartApplyMsg = $state('');
  async function applyParamsFromChart(next: Record<string, any>) {
    const isReal = !robot?.paper;
    const changed = Object.entries(next)
      .filter(([k, v]) => String(params?.[k] ?? '') !== String(v))
      .map(([k, v]) => `${k}=${v}`);
    if (!changed.length) { chartApplyMsg = 'Значения не изменились.'; return; }
    if (isReal && !window.confirm(
      `Робот торгует РЕАЛЬНЫМИ деньгами.
Применить: ${changed.join(', ')}?`)) return;
    chartApplyBusy = true; chartApplyMsg = '';
    try {
      // symbol не трогаем: инструмент робота меняется только редеплоем спеки.
      const clean: Record<string, any> = { ...(params || {}) };
      for (const [k, v] of Object.entries(next)) {
        if (k === 'symbol') continue;
        clean[k] = Number.isFinite(Number(v)) ? Number(v) : v;
      }
      const res = await fetchWithAuth(`/api/v1/quik/robots/${encodeURIComponent(robotId)}/params`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, params_json: JSON.stringify(clean) }),
      });
      const d = await res.json().catch(() => ({}));
      chartApplyMsg = res.ok
        ? 'Применено — вступит в силу с нового бара.'
        : (res.status === 502 || res.status === 503
            ? `Сервер STL недоступен (${res.status}) — обычно это перезапуск, займёт до минуты. `
              + 'Ваши значения в полях сохранены, нажмите «Сохранить» ещё раз.'
            : `Ошибка: ${d?.detail ?? res.status}`);
    } catch (e) {
      chartApplyMsg = `Ошибка: ${String(e).slice(0, 80)}`;
    } finally { chartApplyBusy = false; }
  }

  async function saveParams() {
    const isReal = !robot?.paper;
    const summary = `qty=${draft.qty} avg_max=${draft.avg_max} max_position=${draftMaxPos}`;
    if (isReal && !window.confirm(
      `Робот торгует РЕАЛЬНЫМИ деньгами.\nПрименить: ${summary}?`)) return;
    saving = true; saveMsg = '';
    try {
      // numbers stay numbers (inputs type=number give numbers; guard NaN)
      const clean: Record<string, any> = {};
      for (const [k, v] of Object.entries(draft))
        clean[k] = k === 'symbol' ? v : (Number.isFinite(Number(v)) ? Number(v) : v);
      const res = await fetchWithAuth(`/api/v1/quik/robots/${encodeURIComponent(robotId)}/params`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: agentId,
          params_json: JSON.stringify(clean),
          max_position: Math.max(1, Math.round(Number(draftMaxPos) || 1)),
          schedule: draftSchedule.trim() || null,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { saveMsg = `Ошибка: ${d?.detail ?? res.status}`; return; }
      saveMsg = d.redeployed
        ? 'Применено с редеплоем спеки (позиция/P&L сохранены).'
        : 'Применено (с нового бара).';
      editMode = false;   // mirror refresh (3s) will show the new values
    } catch (e) {
      saveMsg = `Ошибка: ${String(e).slice(0, 80)}`;
    } finally { saving = false; }
  }

  let paramSchema = $state<any[]>([]);
  let strategyCode = $state('');
  async function loadDesc(sid: string) {
    try {
      const res = await fetchWithAuth('/api/v1/strategies');
      if (!res.ok) return;
      const list = await res.json();
      const hit = (list ?? []).find((s: any) => s.id === sid);
      if (hit?.description) strategyDesc = hit.description;
      if (Array.isArray(hit?.params_schema)) paramSchema = hit.params_schema;
      // Контр-стратегии (<base>__inv) в списке нет — её код синтезируется так же,
      // как в Ботсторе: make_on_bar сам снимает суффикс и фейдит базовый сигнал.
      strategyCode = hit?.script_code
        || (/^[a-z0-9_]+$/.test(sid)
            ? `from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('${sid}')`
            : '');
    } catch { /* description is optional */ }
  }

  // ── «Рассчитать эффект точно» ───────────────────────────────────────────────
  // Копилка в signal_json — ОЦЕНКА (фантомы отсеянных входов). Точный ответ даёт
  // один прогон на i9 с ДВУМЯ комбо: параметры робота как есть и они же с
  // выключенными фильтрами. Оба считаются по ОДНИМ И ТЕМ ЖЕ барам, поэтому
  // разница net — чистый вклад фильтров, с комиссией. Это МОДЕЛЬ на биржевых
  // минутках, а не реплей живой ленты робота: сделки не совпадут поштучно.
  // ВАЖНО: два ОТДЕЛЬНЫХ прогона, а не два комбо в одном. Многокомбовый прогон
  // приезжает БЕЗ кривой (i9 режет trades+equity_curve на len>1, чтобы не топить
  // маленький Postgres), а нам нужны обе кривые. Бонусом одиночные прогоны ловит
  // кэш повторных прогонов — второй раз те же настройки отдаются мгновенно.
  type Ffx = { runs: string[]; status?: string; elapsed?: number; queue?: string; period?: string;
               withF?: number; withoutF?: number; trF?: number; trNoF?: number; err?: string;
               curveOn?: Pt[]; curveOff?: Pt[] };
  let ffx = $state<Ffx | null>(null);
  let ffxBusy = $state(false);
  const ffxKey = $derived(`ffx:${robotId}`);
  const FFX_OFF = { min_gap_pts: 0, cooldown_min: 0 };
  // Сравнивать нечего, когда у робота фильтры и так выключены: обе ветки — один
  // и тот же прогон, «+0 ₽» под заголовками «с фильтрами / без фильтров» читается
  // как «фильтры бесплатны», а это неправда — их просто нет.
  const filtersOn = $derived(Number((params as any)?.min_gap_pts ?? 0) > 0
                          || Number((params as any)?.cooldown_min ?? 0) > 0);
  const ruStatusFfx = (s?: string) => (
    { queued: 'в очереди на i9', pending: 'запуск', running: 'считается на i9' } as any
  )[s ?? ''] ?? 'ждём i9';

  /** Один прогон: ждём готовности, возвращаем строку результата (с кривой). */
  async function ffxWait(run_id: string, t0: number): Promise<any> {
    for (let i = 0; i < 900; i++) {              // до 30 мин: i9 может доедать раунд
      let sd: any;
      try {
        const sr = await fetchWithAuth(`/api/v1/backtest/${run_id}/status`);
        if (!sr.ok) throw new Error('status ' + sr.status);
        sd = await sr.json();
      } catch { await new Promise((r) => setTimeout(r, 2000)); continue; }
      if (ffx && ffx.status !== 'done') {
        ffx = { ...ffx, status: sd.status, elapsed: Math.round((Date.now() - t0) / 1000),
                queue: [sd.i9_offline ? 'i9 не на связи' : sd.runner,
                        sd.eta_sec ? `осталось ~${sd.eta_sec < 60 ? sd.eta_sec + 'с'
                                                  : Math.round(sd.eta_sec / 60) + ' мин'}` : '']
                       .filter(Boolean).join(' · ') };
      }
      if (sd.status === 'failed') throw new Error(sd.error_msg || 'прогон завершился ошибкой (логи i9)');
      if (sd.status === 'done') {
        const rr = await fetchWithAuth(`/api/v1/backtest/${run_id}/results?full=1`);
        const rows = rr.ok ? await rr.json() : [];
        if (!rows[0]) throw new Error('прогон завершён, но результат пуст');
        return rows[0];
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw new Error('нет результата за 30 мин');
  }

  async function ffxStart(extra: any, from: Date, to: Date): Promise<string> {
    // bar_offset_min — инфраструктурный сдвиг ТОЛЬКО для агентских баров (истинный
    // UTC); бэктест идёт по биржевым минуткам, где он обязан быть нулевым.
    const { bar_offset_min, ...p } = params as any;
    const res = await fetchWithAuth('/api/v1/backtest/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scriptCode: strategyCode, symbol, paramsGrid: {},
        baseParams: { ...p, ...extra, symbol },
        dateFrom: from.toISOString(), dateTo: to.toISOString(),
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()).run_id;
  }

  async function ffxCollect(runs: string[]) {
    const t0 = Date.now();
    try {
      const [on, off] = await Promise.all(runs.map((r) => ffxWait(r, t0)));
      ffx = { ...(ffx ?? { runs }), runs, status: 'done',
              withF: Number(on.net_profit ?? 0), withoutF: Number(off.net_profit ?? 0),
              trF: Number(on.total_trades ?? 0), trNoF: Number(off.total_trades ?? 0),
              curveOn: on.equity_curve ?? [], curveOff: off.equity_curve ?? [] };
    } catch (e: any) {
      ffx = { ...(ffx ?? { runs }), runs, err: String(e?.message ?? e) };
    } finally { localStorage.removeItem(ffxKey); }
  }

  async function runFilterEffect() {
    if (ffxBusy) return;
    ffxBusy = true;
    try {
      if (!strategyCode) { ffx = { runs: [], err: 'не знаю код стратегии робота' }; return; }
      const from = ledgerStat?.first_ts
        ? new Date(ledgerStat.first_ts) : new Date(Date.now() - 30 * 86_400_000);
      const to = new Date();
      const runs = await Promise.all([ffxStart({}, from, to), ffxStart(FFX_OFF, from, to)]);
      // Период показываем ФАКТИЧЕСКИЙ: без журнала точки старта нет и «весь срок»
      // было бы враньём — там просто последние 30 дней.
      const d = (x: Date) => x.toLocaleDateString('ru-RU');
      ffx = { runs, status: 'queued', elapsed: 0,
              period: `${d(from)} — ${d(to)}${ledgerStat?.first_ts ? ' (от первой сделки)' : ' (журнала нет: 30 дней)'}` };
      localStorage.setItem(ffxKey, JSON.stringify(runs));
      await ffxCollect(runs);
    } catch (e: any) {
      ffx = { ...(ffx ?? { runs: [] }), err: String(e?.message ?? e) };
    } finally { ffxBusy = false; }
  }
  // «История прогонов»: all saved parameter sweeps of THIS robot's strategy, so the
  // operator reviews every hit-parade without re-running (each row opens the standard
  // campaign showcase via ?campaign=). Loaded lazily on first open.
  let allCampaigns = $state<any[]>([]);
  let showHistory = $state(false);
  const strategyCampaigns = $derived(
    allCampaigns.filter((c: any) => (c.strategies ?? []).includes(robot?.strategy_id)));
  async function toggleHistory() {
    showHistory = !showHistory;
    if (showHistory && allCampaigns.length === 0) {
      try {
        const r = await fetchWithAuth('/api/v1/lab/campaigns');
        if (r.ok) allCampaigns = await r.json();
      } catch { /* history is auxiliary */ }
    }
  }

  // Live context for the param editor's «×ATR = N пунктов» conversions: price +
  // current ATR come from the runner's signal_json (explain.py, always present).
  const paramCtx = $derived({
    price: Number(signal?.last_close ?? liveTick?.p ?? avgPrice ?? 0) || 0,
    atr: Number(signal?.atr ?? 0) || 0,
    tp: Number(draft?.tp_atr ?? 0) || 0,   // стоп считается ДОЛЕЙ дистанции тейка
  });
  // Editor schema: the strategy's own params_schema when loaded, else synthesize
  // rows from the deployed params so the editor still renders before the fetch.
  const editorSchema = $derived(
    paramSchema.length
      ? paramSchema.filter((f: any) => f.key in draft || f.key === 'symbol')
      : Object.keys(draft).map((k) => ({ key: k, label: k, type: k === 'symbol' ? 'text' : 'number' })));
  // Reactive: the mirror resolves strategy_id AFTER mount, so a one-shot onMount
  // call raced it and could pin the default ('fvg') description forever.
  $effect(() => { const sid = robot?.strategy_id; if (sid) void loadDesc(sid); });

  // Non-blocking startup: a hung first request must not delay the timers (that
  // is exactly how the screen froze — onMount awaited a request that never
  // resolved, so setInterval was never installed).
  let timers: Array<ReturnType<typeof setInterval>> = [];
  onMount(() => {
    void load(); void pollTick(); void loadStatus(); void loadLedger();
    // Прогоны живут на i9 минутами: после F5 подхватываем свои run_id и досматриваем.
    try {
      const prev = JSON.parse(localStorage.getItem(`ffx:${robotId}`) || 'null');
      if (Array.isArray(prev) && prev.length === 2) {
        ffx = { runs: prev, status: 'queued' };
        void ffxCollect(prev);
      }
    } catch { localStorage.removeItem(`ffx:${robotId}`); }
    timers = [setInterval(load, 3000), setInterval(pollTick, 1000),
              setInterval(loadStatus, 4000), setInterval(loadCoef, 300_000),
              setInterval(loadLedger, 30_000)];   // журнал инжестится раз в 30с
  });
  onDestroy(() => { for (const t of timers) clearInterval(t); });
</script>

<div class="ars">
  <ScreenTag id="AGENT-ROBOT" name="стенд робота на агенте" />
  <div class="ars-head">
    <NavMenu />
    <span class="ars-icon">🤖</span>
    <RobotIdentity name={displayName} id={robotId} size="title" />
    <button class="ars-rename" title="Переименовать" onclick={renameRobot}>✏</button>
    {#if robot}
      <span class="badge" class:real={!robot.paper}>{robot.paper ? 'PAPER' : 'РЕАЛ'}</span>
      <span class="badge sym">{symbol}</span>
      <span class="badge" class:ok={robot.running} class:warn={!robot.running}>
        {robot.running ? 'РАБОТАЕТ' : (robot.paused ? 'ПАУЗА' : 'СТОП')}</span>
      <span class="badge dim">окно {robot.schedule}</span>
      <span class="badge dim">баров: {robot.bars_count ?? 0}</span>
      <span class="badge" class:ok={heartbeatAge !== null && heartbeatAge < 45} class:warn={heartbeatAge === null || heartbeatAge >= 45}>
        пульс {heartbeatAge === null ? '—' : heartbeatAge + 'с'}</span>
      <span class="badge pos" class:long={position > 0} class:short={position < 0}>
        позиция {position > 0 ? '+' : ''}{position}</span>
      <!-- Two metrics per operator spec: (1) realized P&L (closed trades − commission,
           WITHOUT the open position, static); (2) P&L + Маржа = flatten-at-market now
           value (realized + variation margin − exit commission), moves with price. -->
      {#if pnlRub !== null}
        <span class="badge pnl" class:up={pnlRub > 0} class:dn={pnlRub < 0}
              title="Реализованный P&L робота: закрытые сделки × ₽/пункт — авторитетное число самого агента (совпадает с его страницей 127.0.0.1:8071). Считается по филлам робота, БЕЗ учёта текущей позиции, но УЖЕ ЗА ВЫЧЕТОМ биржевой комиссии (taker, как в бэктесте); включает бумажный период до перевода на реал.">

          P&L {pnlRub > 0 ? '+' : ''}{Math.round(pnlRub).toLocaleString('ru-RU')} ₽</span>
        <span class="badge pnl" class:up={(pnlMargin ?? 0) > 0} class:dn={(pnlMargin ?? 0) < 0}
              title={`Если ударить по рынку и закрыть ВСЮ позицию прямо сейчас: реализованный ${Math.round(pnlRub).toLocaleString('ru-RU')} + вариац. маржа ${floatRub !== null ? (floatRub > 0 ? '+' : '') + Math.round(floatRub).toLocaleString('ru-RU') : '0'} − комиссия закрытия ${Math.round(closeComm).toLocaleString('ru-RU')} ₽.`}>
          P&L+Маржа {(pnlMargin ?? 0) > 0 ? '+' : ''}{Math.round(pnlMargin ?? pnlRub).toLocaleString('ru-RU')} ₽</span>
        <span class="badge pnl" class:up={(annPct ?? 0) > 0} class:dn={(annPct ?? 0) < 0}
              title={annPct == null
                ? 'Доходность в год: нужно ≥3 дней торговли и известное ГО'
                : `Доходность в год: (фикс+ВМ) к максимальному задействованному ГО ${Math.round(maxGo).toLocaleString('ru-RU')} ₽, линейно экстраполировано с ${annDays} дн торговли.`}>
          год {annPct == null ? '—' : (annPct > 0 ? '+' : '') + annPct.toFixed(1) + '%'}{annDays && annPct != null ? ` за ${annDays} дн` : ''}</span>
      {:else}
        <span class="badge pnl dim">P&L …</span>
      {/if}
      <span class="badge" class:ok={tickAge !== null && tickAge <= 10} class:warn={tickAge === null || tickAge > 10}
            title="возраст последнего тика QUIK (поток данных)">
        тик {tickAge === null ? '—' : tickAge + 'с'}</span>
      {#if !robot.paper}
        {#if robot.paused}
          <button class="rc-btn go" onclick={startRobot} title="возобновить работу робота">▶ Пуск</button>
          <button class="rc-btn" disabled={posBusy} onclick={zeroBelief}
                  title="исправить веру робота о позиции (фантом) — только вера, без реализации P&L и без реального ордера">
            {posBusy ? '…' : '✎ Позиция'}</button>
          <button class="rc-btn" disabled={fillBusy} onclick={recordFill}
                  title="записать РУЧНУЮ сделку (закрытие руками в терминале): реализует P&L и попадёт в историю, реальный ордер НЕ выставляется">
            {fillBusy ? '…' : '＋ Сделка'}</button>
        {:else}
          {#if exitOnly}
            <button class="rc-btn go" disabled={exitBusy} onclick={() => setExitOnly(false)}
                    title="вернуть робота в обычную работу: снова открывает позиции">
              {exitBusy ? '…' : '▶ Пуск'}</button>
          {:else}
            <button class="rc-btn" disabled={exitBusy} onclick={() => setExitOnly(true)}
                    title="дать роботу закрыть позицию по СВОЕМУ сигналу (TP/SL) и больше не открывать новых — экспирация, развод встречных роботов, вывод в бумагу">
              {exitBusy ? '…' : '⇥ Только на выход'}</button>
          {/if}
          <button class="rc-btn" disabled={pausing} onclick={pauseRobot}
                  title="остановить новые входы; открытая позиция ОСТАЁТСЯ">
            {pausing ? '…' : '⏸ Пауза'}</button>
          <button class="rc-btn danger" disabled={flattening} onclick={flattenNow}
                  title="закрыть всю позицию по рынку и остановить робота">
            {flattening ? '…' : '⏻ Закрыть всё + стоп'}</button>
        {/if}
        {#if flattenMsg}<span class="rc-msg">{flattenMsg}</span>{/if}
      {/if}
      <button class="rc-btn go" disabled={cloneBusy} onclick={cloneToPaper}
              title="скопировать параметры этого робота и запустить НОВЫЙ робот в PAPER на агенте">
        {cloneBusy ? '…' : '▶ Запустить в торговлю (paper)'}</button>
      {#if cloneMsg}<span class="rc-msg">{cloneMsg}</span>{/if}
    {:else if report}
      <span class="badge warn">робот {robotId} не найден на агенте</span>
    {/if}
    {#if error}<span class="badge warn">{error}</span>{/if}
    <div class="lay-switch" title="Раскладка экрана (запоминается)">
      <button class:on={profile === 'stack'} onclick={() => setProfile('stack')} title="Стопка: график сверху, панели снизу">▤</button>
      <button class:on={profile === 'chart-left'} onclick={() => setProfile('chart-left')} title="График слева во всю высоту, панели справа">◧</button>
      <button class:on={profile === 'chart-right'} onclick={() => setProfile('chart-right')} title="График справа во всю высоту, панели слева">◨</button>
    </div>
  </div>

  {#if robot && !robot.bars_count}
    {#if tickAge !== null && tickAge <= 10}
      <div class="feed-warn calm">Копим первый закрытый бар после рестарта (~1 мин) — тики из QUIK идут, стратегия ждёт свечу.</div>
    {:else}
      <div class="feed-warn">Нет закрытых баров и поток тиков из QUIK не идёт — проверь QLua-скрипт shectory_trade (файловая очередь) и открытые окна QUIK.</div>
    {/if}
  {/if}

  {#snippet chartRegion()}
  <div class="ars-chart">
    <AgentBookPane {symbol} {agentId} depth={10} />
    <div class="ars-chart-body">
    {#if chartResult}
    <BacktestChart
      result={chartResult}
      {symbol}
      screenId="AGENT-CHART"
      dateFrom={dateFrom}
      dateTo={dateTo}
      defaultInterval={1}
      live={20}
      liveTick={liveTick}
      pointValue={pointCoef ?? 1}
      pointValueKnown={pointCoef != null}
      taker={false}
      openOrders={openOrders}
      plannedOrders={plannedOrders}
      closeSeries={closeSeries}
      netOverride={robot?.paper ? null : pnlRub}
      floatRub={robot?.paper ? null : floatNetRub}
      livePosition={robot ? position : null}
      journalSuspect={reconCheck ? reconCheck.trades_ok === false : false}
      runParams={params}
      paramSchema={editorSchema}
      onApplyParams={applyParamsFromChart}
      applyBusy={chartApplyBusy}
      applyMsg={chartApplyMsg}
    />
    {/if}
    </div>
  </div>
  {/snippet}

  {#snippet pingPanel()}
  <div class="ars-lat"><LatencyPane minutes={360} /></div>
  {/snippet}

  {#snippet diagPanel()}
  <!-- QUIK-link diagnostics + recon vs QUIK account tables (agent local status) -->
  <div class="ars-diag-row" class:col={profile !== 'stack'}>
    <div class="diag-box">
      <div class="p-title">Связь с биржей (QUIK-агент)</div>
      <div class="diag-grid">
        <div class="dg" class:ok={localStatus?.agent?.link_up} class:bad={localStatus != null && !localStatus.agent?.link_up}>
          <span class="dgk">линк агент↔STL</span>
          <span class="dgv">{localStatus ? (localStatus.agent?.link_up ? 'на связи' : 'ОБРЫВ') : '—'}</span>
        </div>
        <div class="dg"><span class="dgk">RTT агент↔QUIK</span><span class="dgv">{health?.rtt_ms != null ? health.rtt_ms + ' мс' : '—'}</span></div>
        <div class="dg" class:warn={(health?.exchange_lag_ms ?? 0) > 3000}>
          <span class="dgk">лаг биржи</span><span class="dgv">{health?.exchange_lag_ms != null ? health.exchange_lag_ms + ' мс' : '—'}</span>
        </div>
        <div class="dg" class:warn={Math.abs(health?.clock_drift_ms ?? 0) > 5000}>
          <span class="dgk">дрейф часов VDS</span><span class="dgv">{health?.clock_drift_ms != null ? health.clock_drift_ms + ' мс' : '—'}</span>
        </div>
        <div class="dg" class:ok={health?.runner_healthy} class:bad={localStatus != null && !health?.runner_healthy}>
          <span class="dgk">runner</span><span class="dgv">{localStatus ? (health?.runner_healthy ? 'здоров' : 'НЕЗДОРОВ') : '—'}</span>
        </div>
        <div class="dg"><span class="dgk">отчёт runner</span><span class="dgv">{health?.runner_report_age_ms != null ? Math.round(health.runner_report_age_ms / 1000) + ' с' : '—'}</span></div>
        <div class="dg"><span class="dgk">возраст зеркала</span><span class="dgv">{mirrorAge != null ? mirrorAge + ' с' : '—'}</span></div>
        <div class="dg"><span class="dgk">статус получен</span><span class="dgv">{localStatus?.receivedAgeSec != null ? localStatus.receivedAgeSec + ' с' : '—'}</span></div>
      </div>
      {#if health?.feed?.length}
        <div class="feed-row">
          {#each health.feed as f}
            <span class="feed-chip" class:stale={f.age_ms > 15000} class:cur={f.code === symbol}>{f.code} · {Math.round(f.age_ms)}мс</span>
          {/each}
        </div>
      {/if}
    </div>

    <div class="diag-box recon-box" class:mismatch={reconState != null && reconState !== 'OK'} class:reconok={reconState === 'OK'}>
      <div class="p-title">Сверка с таблицами QUIK
        <span class="recon-state" class:ok={reconState === 'OK'} class:bad={reconState != null && reconState !== 'OK'}>{reconState ?? '—'}</span>
      </div>
      {#if reconCheck}
        <div class="recon-line">
          <span>позиция робота по данным агента: <b>{reconCheck.position}</b></span>
        </div>
        <div class="recon-line">
          <span class="rok" class:bad={!reconCheck.orders_ok}>заявки {reconCheck.orders_ok ? '✓ сходятся' : '✗ расходятся'}</span>
          <span class="rok" class:bad={!reconCheck.trades_ok}>сделки {reconCheck.trades_ok ? '✓ сходятся' : '✗ расходятся'}</span>
        </div>
      {:else if localStatus}
        <div class="recon-line dim">нет строки сверки для этого робота (агент ещё не публикует acc-таблицы, либо у робота нет позиции/заявок)</div>
      {:else}
        <div class="recon-line dim">загрузка статуса агента…</div>
      {/if}
      {#if manualBlock && ((manualBlock.orders?.length ?? 0) > 0 || (manualBlock.account_net?.length ?? 0) > 0)}
        <div class="recon-line manual">
          {#if (manualBlock.orders?.length ?? 0) > 0}
            <span class="mlabel">ручные заявки (не робот):</span>
            <span class="mchip">{manualBlock.orders.length} шт</span>
          {/if}
          {#if (manualBlock.account_net?.length ?? 0) > 0}
            <span class="mlabel" title="сырое нетто по счёту QUIK — роботы + ручная торговля вместе; справочно, не сверяется">нетто счёта (роботы + ручное):</span>
            {#each manualBlock.account_net ?? [] as n}<span class="mchip">{n.sec} {n.net > 0 ? '+' : ''}{n.net}</span>{/each}
          {/if}
        </div>
      {/if}
    </div>
  </div>
  {/snippet}

  {#snippet signalPanel()}
    <div class="panel">
      {#if filterStats}
        {@const fs = filterStats}
        <div class="fstats" title="Фильтры входа: сколько раз не пустили сделку и во что это обошлось/сберегло">
          <div class="fs-head">Фильтры входа</div>
          <div class="kv-grid">
            <div class="kv"><span>Разножка отсеяла</span><b>{fs.gap_skips ?? 0}</b></div>
            <div class="kv"><span>Остывание отсеяло</span><b>{fs.cooldown_skips ?? 0}</b></div>
            <div class="kv" title="ОЦЕНКА, а не факт. Каждый отсеянный вход считается несостоявшейся сделкой и держится до того же выхода, каким вышел бы робот: свой тейк (tp_atr x ATR) или разворот сигнала. Плюс = отсеянные входы были бы убыточными, фильтр сберёг деньги. Минус = недозаработали. Повторные отсевы в одном ценовом окне считаются ОДНОЙ несостоявшейся сделкой, иначе одно и то же намерение множилось бы каждый бар. Комиссия не учитывается; взаимное влияние на среднюю и потолок позиции — тоже.">
              <span>Эффект фильтров <span class="fs-est">оценка</span></span>
              <b class:yes={(fs.savedRub ?? 0) > 0} class:neg={(fs.savedRub ?? 0) < 0}>
                {fs.savedRub == null ? `${fs.saved_pts} пт`
                  : `${fs.savedRub >= 0 ? '+' : ''}${Math.round(fs.savedRub).toLocaleString('ru-RU')} ₽`}</b>
            </div>
            {#if fs.pending}<div class="kv" title="несостоявшиеся сделки, которые ещё «в позиции»: ждут своего тейка или разворота сигнала"><span>Ещё в позиции</span><b>{fs.pending}</b></div>{/if}
            {#if fs.since}<div class="kv" title="методику оценки меняли 29.07.2026 (был фиксированный час) — копилка считается с этого момента"><span>Считается с</span><b>{new Date(fs.since * 1000).toLocaleDateString('ru-RU')}</b></div>{/if}
            {#if fs.dropped}<div class="kv" title="переполнение буфера несостоявшихся сделок: эти отсевы в сумму НЕ вошли"><span>Не учтено отсевов</span><b>{fs.dropped}</b></div>{/if}
          </div>
          <div class="ffx">
            <button class="ffx-btn" disabled={!filtersOn || ffxBusy || (!!ffx && ffx.status !== 'done' && !ffx.err)}
                    onclick={runFilterEffect}
                    title="Один прогон на i9 с двумя наборами параметров: как у робота и он же с выключенными фильтрами. Оба считаются по ОДНИМ барам за весь срок жизни робота, разница финреза — точный вклад фильтров, комиссия учтена. Это модель на биржевых минутках, а не повтор живой ленты: сделки не совпадут поштучно.">
              {ffxBusy || (ffx && ffx.status !== 'done' && !ffx.err) ? 'Считается на i9…' : 'Рассчитать эффект точно'}
            </button>
            {#if !filtersOn}
              <div class="ffx-run">фильтры входа сейчас выключены (разножка 0, остывание 0) — сравнивать не с чем. Счётчики выше историчные.</div>
            {:else if ffx?.err}
              <div class="ffx-err">{ffx.err}</div>
            {:else if ffx && ffx.status !== 'done'}
              <div class="ffx-run">{ffx.queue || ruStatusFfx(ffx.status)}{ffx.elapsed ? ` · идёт ${ffx.elapsed}с` : ''}</div>
            {:else if ffx?.status === 'done' && ffx.withF != null && ffx.withoutF != null}
              {@const diff = ffx.withF - ffx.withoutF}
              <div class="kv-grid ffx-res">
                <div class="kv"><span>С фильтрами</span><b>{Math.round(ffx.withF).toLocaleString('ru-RU')} ₽ · {ffx.trF} сд.</b></div>
                <div class="kv"><span>Без фильтров</span><b>{Math.round(ffx.withoutF).toLocaleString('ru-RU')} ₽ · {ffx.trNoF} сд.</b></div>
                <div class="kv" title="точный вклад фильтров: плюс = фильтры заработали, минус = отняли">
                  <span>Эффект точно</span>
                  <b class:yes={diff > 0} class:neg={diff < 0}>{diff >= 0 ? '+' : ''}{Math.round(diff).toLocaleString('ru-RU')} ₽</b>
                </div>
              </div>
              {@const eq = equityPaths(ffx.curveOn ?? [], ffx.curveOff ?? [], 300, 90)}
              {#if eq}
                <svg class="ffx-chart" viewBox="0 0 300 90" preserveAspectRatio="none" role="img"
                     aria-label="кривые финреза с фильтрами и без">
                  <polyline points={eq.pb} class="c-off" />
                  <polyline points={eq.pa} class="c-on" />
                </svg>
                <div class="ffx-legend">
                  <span class="lg on">— с фильтрами</span>
                  <span class="lg off">— без фильтров</span>
                  <span class="lg sc">шкала: {Math.round(eq.lo).toLocaleString('ru-RU')} … {Math.round(eq.hi).toLocaleString('ru-RU')} ₽</span>
                </div>
              {/if}
              <div class="ffx-note">{ffx.period ? ffx.period + ' · ' : ''}модель на биржевых минутках, комиссия taker; поштучно со сделками робота не совпадает</div>
            {/if}
          </div>
        </div>
      {/if}
      {#if signal}
        <div class="wait" class:sig={signal.want != null && signal.want !== 0}>{signal.waiting_for ?? '—'}</div>
        {#if signal.features}
          {@const f = signal.features}
          <div class="kv-grid">
            <div class="kv"><span>Бычий разрыв (low &gt; high[i-2])</span><b class:yes={f.gap_up}>{f.gap_up ? 'ДА' : 'нет'}</b></div>
            <div class="kv"><span>Медвежий разрыв (high &lt; low[i-2])</span><b class:yes={f.gap_dn}>{f.gap_dn ? 'ДА' : 'нет'}</b></div>
            <div class="kv"><span>Тело свечи ×10⁴</span><b>{f.body_x10000}</b></div>
            <div class="kv"><span>Порог min_frac ×10⁴</span><b>{f.min_frac_x10000}</b></div>
            {#if f.bar_i}<div class="kv"><span>Бар i (O/H/L/C)</span>
              <b class="mono">{f.bar_i.o}/{f.bar_i.h}/{f.bar_i.l}/{f.bar_i.c}</b></div>{/if}
            {#if f.bar_i2}<div class="kv"><span>Бар i-2 (H/L)</span>
              <b class="mono">{f.bar_i2.h}/{f.bar_i2.l}</b></div>{/if}
          </div>
        {/if}
        {#if signal.exit_levels}
          {@const el = signal.exit_levels}
          <div class="kv-grid">
            <div class="kv" title="точный уровень выхода в плюс: робот закроет позицию по рынку, когда бар его коснётся">
              <span>Тейк-профит (TP)</span><b class="mono yes">{fmtPrice(el.tp)}</b></div>
            <div class="kv" title="точный уровень выхода в минус: робот закроет позицию по рынку, когда бар его коснётся">
              <span>Стоп-лосс (SL)</span><b class="mono neg">{fmtPrice(el.sl)}</b></div>
            <div class="kv"><span>Вход был по</span><b class="mono">{fmtPrice(el.entry)}</b></div>
          </div>
        {/if}
        {#if signal.range}
          <div class="kv-grid">
            <div class="kv" title="диапазон первых минут после открытия США — от его границ считается вход">
              <span>Диапазон открытия</span>
              <b class="mono">{fmtPrice(signal.range.lo)} — {fmtPrice(signal.range.hi)}</b></div>
          </div>
        {/if}
        <div class="p-title sub">Планируемые заявки
          <span class="where" title="ПЛАН и УРОВНИ живут только внутри робота на VDS: в QUIK заявки нет, пока условие не сработает. В QUIK уходит только «Выставленная заявка» ниже (у неё есть номер QUIK).">
            план робота · в QUIK ещё не отправлены
          </span>
        </div>
        {#if signal.entry_blocked}
          <div class="blocked-note" title="фильтр входа (разножка / остывание) стоит только на НАБОРЕ объёма: выход из позиции и тейк-профит он не задерживает">
            Вход сейчас закрыт фильтром: {signal.entry_blocked}
          </div>
        {/if}
        {#if plannedOrders.length || armedOrders.length}
          {#each plannedOrders as p}
            <div class="plan-row" class:buy={p.side === 'buy'} class:sell={p.side === 'sell'}
                 class:blocked={p.blocked}>
              <b>{p.side === 'buy' ? '▲ BUY' : '▼ SELL'} {p.qty}</b>
              <span class="mono">@ {fmtPrice(p.price)}</span>
              <span class="why">{p.reason}{p.blocked ? ` — НЕ УЙДЁТ: ${p.blocked}` : ''}</span>
            </div>
          {/each}
          {#each armedOrders as p}
            <div class="plan-row" class:buy={p.side === 'buy'} class:sell={p.side === 'sell'}>
              <b>{p.side === 'buy' ? '▲ BUY' : '▼ SELL'} {p.qty}</b>
              <span class="mono">по рынку</span>
              <span class="why">{p.reason} — сработает по ЗАКРЫТИЮ бара с паттерном, это не ценовой уровень</span>
            </div>
          {/each}
        {:else}
          <div class="empty">Нет — жду сигнала.</div>
        {/if}
        {#if openOrders.length}
          <div class="p-title sub">Выставленные заявки
            <span class="where live-where" title="эти заявки УЖЕ на бирже: номер — ключ QUIK, по нему сделка ищется в таблицах терминала">
              уже в QUIK · на бирже
            </span>
          </div>
          {#each openOrders as o}
            <div class="plan-row live" class:buy={o.side === 'buy'} class:sell={o.side === 'sell'}>
              <b>{o.side === 'buy' ? '▲ BUY' : '▼ SELL'} {o.qty}</b>
              <span class="mono">@ {fmtPrice(o.price)}</span>
              <span class="why mono">QUIK №{o.order_id}</span>
            </div>
          {/each}
        {/if}
      {:else}
        <div class="empty">Диагностика сигнала ещё не пришла.</div>
      {/if}
    </div>

    {/snippet}

    {#snippet tradesPanel()}
    <div class="panel">
      <div class="pt-note tnote">каждая строка = заявка робота; «Подтверждение» — ID из QUIK или paper</div>
      <div class="hist-scroll">
        <table>
          <thead><tr><th>Время (МСК)</th><th>Сторона</th><th title="роль сделки в жизненном цикле позиции: OPEN — открытие, AVG — усреднение против движения, ENF — усиление по движению, TP/SL — закрытие в плюс/минус, →OPEN — разворот">Действие</th><th>Кол-во</th><th>Цена</th><th title="реализованный результат сделки net комиссии, только на закрывающих (TP/SL/разворот)">P&amp;L ₽</th><th title="биржевая комиссия (taker) этого филла — платится на КАЖДОЙ сделке">Комиссия ₽</th><th>Статус</th><th>Подтверждение</th></tr></thead>
          <tbody>
            {#if trades.length === 0}
              <tr class="empty-row"><td colspan="9">Сделок пока нет — робот ждёт сигнала.</td></tr>
            {:else}
              {#each [...tradeRows].reverse() as t}
                <tr class:rej={t.status === 'rejected' || t.status === 'skipped'}>
                  <td class="mono">{fmtMskTime(t.time * 1000)}</td>
                  <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>{t.side === 'buy' ? '▲ buy' : '▼ sell'}</td>
                  <td>{#if t.meta?.action}<span class="act {t.meta.cls}">{t.meta.action}</span>{/if}</td>
                  <td class="mono">{t.qty}</td>
                  <td class="mono">{fmtPrice(t.price)}</td>
                  <td class="mono">{#if t.meta?.pnl != null && pointCoef != null}<span class={t.meta.pnl >= 0 ? 'buy' : 'sell'}>{t.meta.pnl >= 0 ? '+' : ''}{Math.round(t.meta.pnl).toLocaleString('ru-RU')} ₽</span>{/if}</td>
                  <td class="mono">{#if t.meta?.comm != null && t.meta.comm > 0}<span class="comm-cell" title="комиссия этого филла">−{t.meta.comm.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽</span>{/if}</td>
                  <td><span class="st st-{t.status}">{t.status}</span></td>
                  <td class="mono id">
                    {#if t.status === 'paper'}
                      <span class="confirm paper" title="виртуальное исполнение — заявка НЕ уходила в QUIK">paper</span>
                    {:else if t.order_id && !t.order_id.startsWith('rr:')}
                      <span class="confirm quik" title="номер заявки, подтверждённый QUIK">QUIK №{t.order_id}</span>
                    {:else}
                      <span class="confirm none" title="подтверждение из QUIK не получено (client_id: {t.order_id})">нет подтв.</span>
                    {/if}
                  </td>
                </tr>
              {/each}
            {/if}
          </tbody>
          {#if trades.length && pointCoef != null}
            <tfoot>
              <tr class="hist-tot">
                <td colspan="5" class="tot-lbl" title="сумма по журналу сделок за показанные строки (хвост до 200 филлов); полный реализованный P&L робота — в бейдже вверху">Итого (показанные, ₽):</td>
                <td class="mono" class:buy={tradeTotals.net >= 0} class:sell={tradeTotals.net < 0}
                    title="сумма реализованного по закрытиям, net комиссии">{tradeTotals.net >= 0 ? '+' : ''}{Math.round(tradeTotals.net).toLocaleString('ru-RU')} ₽</td>
                <td class="mono comm-cell" title="суммарная биржевая комиссия по всем филлам">−{Math.round(tradeTotals.comm).toLocaleString('ru-RU')} ₽</td>
                <td colspan="2"></td>
              </tr>
            </tfoot>
          {/if}
        </table>
      </div>
    </div>

    {/snippet}

    {#snippet logicPanel()}
    <div class="panel">
      {#if showHistory}
        <div class="hist-box">
          {#if strategyCampaigns.length === 0}
            <div class="hist-empty">Прогонов параметров для этой стратегии ещё нет.</div>
          {:else}
            {#each strategyCampaigns as c}
              <a class="hist-row mono" href={'/?campaign=' + encodeURIComponent(c.campaign)} target="_blank" rel="noopener"
                 title="открыть витрину прогона: карта плотности, лучшие наборы, график по клику">
                <span class="hist-id">{c.campaign}</span>
                <span class="hist-meta">{(c.symbols ?? []).join(', ')} · {c.combos?.toLocaleString('ru-RU')} комб. · {c.date_from ?? '—'}—{c.date_to ?? '—'}</span>
              </a>
            {/each}
          {/if}
        </div>
      {/if}
      <div class="desc">{strategyDesc || 'Fair Value Gap (ICT) — вход по 3-барному ценовому разрыву с подтверждением телом свечи.'}</div>
      {#if robot?.strategy_id}
        <a class="strat-longread" href={'/?strategy=' + encodeURIComponent(robot.strategy_id)}
           target="_blank" rel="noopener"
           title="полное описание стратегии: как работает, что означает каждый параметр, иллюстрации">
          Подробное описание стратегии и всех параметров ↗
        </a>
      {/if}
      <div class="p-title sub">Параметры
        {#if !editMode}
          <button class="pe-btn" onclick={startEdit}>Изменить</button>
        {/if}
      </div>
      {#if !editMode}
        <div class="kv-grid">
          {#each Object.entries(params) as [k, v]}
            <div class="kv"><span>{k}</span><b class="mono">{v}</b></div>
          {/each}
          <div class="kv"><span>max позиция</span><b class="mono">{robot?.max_position ?? '—'}</b></div>
          <div class="kv"><span>окно</span><b class="mono">{robot?.schedule ?? '—'}</b></div>
        </div>
        {#if saveMsg}<div class="pe-msg">{saveMsg}</div>{/if}
      {:else}
        <ParamEditor strategyId={robot?.strategy_id ?? 'fvg'}
                     schema={editorSchema} bind:values={draft} ctx={paramCtx}
                     disabledKeys={['symbol']} />
        <div class="pe-grid" style="margin-top:10px">
          <label class="pe-row spec">
            <span title="жёсткий потолок контрактов: заявка сверх него не отправляется вовсе (страховка перед лимитами агента). Изменение = редеплой спеки, позиция/P&L сохраняются.">max позиция (спека)</span>
            <input class="pe-in mono" type="number" min="1" bind:value={draftMaxPos} />
          </label>
          <label class="pe-row spec">
            <span title="часы МСК, когда робот активен и принимает сигналы. Формат 09:00-23:55.">окно (спека)</span>
            <input class="pe-in mono" bind:value={draftSchedule} placeholder="09:00-23:55" />
          </label>
        </div>
        <div class="pe-actions">
          <span class="pe-hint">параметры применятся на следующем баре</span>
          <button class="pe-btn" onclick={() => { editMode = false; saveMsg = ''; }}>Отмена</button>
          <button class="pe-btn save" disabled={saving} onclick={saveParams}>
            {saving ? 'Сохраняю…' : 'Сохранить'}</button>
        </div>
        {#if saveMsg}<div class="pe-msg">{saveMsg}</div>{/if}
      {/if}
    </div>
    {/snippet}

  {#if profile === 'stack'}
    <div class="work stack">
      <Frame fid="chart" title="График + доходность" bind:maxId basis={stChart}>{@render chartRegion()}</Frame>
      <Splitter dir="h" bind:size={stChart} min={200} def={440} storageKey="ars_st_chart" />
      <div class="panels-col">
        <Frame fid="ping" title="Задержка до биржи" bind:maxId basis={stPing}>{@render pingPanel()}</Frame>
        <Splitter dir="h" bind:size={stPing} min={46} def={120} storageKey="ars_st_ping" />
        <Frame fid="diag" title="Диагностика и сверка QUIK" bind:maxId basis={stDiag}>{@render diagPanel()}</Frame>
        <Splitter dir="h" bind:size={stDiag} min={60} def={175} storageKey="ars_st_diag" />
        <div class="bottom-row">
          <Frame fid="signal" title="Сигнал сейчас" bind:maxId basis={stSig}>{@render signalPanel()}</Frame>
          <Splitter dir="v" bind:size={stSig} min={120} def={300} storageKey="ars_st_sig" />
          <Frame fid="trades" title={`Сделки робота (${trades.length})`} bind:maxId basis={stTrd}>{@render tradesPanel()}</Frame>
          <Splitter dir="v" bind:size={stTrd} min={140} def={430} storageKey="ars_st_trd" />
          <Frame fid="equity" title="Доходность (журнал)" bind:maxId basis={stEq}>
            <EquityChart robotId={robotId} compact />
          </Frame>
          <Splitter dir="v" bind:size={stEq} min={140} def={300} storageKey="ars_st_eq" />
          <Frame fid="logic" title="Логика стратегии" bind:maxId>
            {#snippet head()}<button class="pe-btn hist" onclick={toggleHistory} title="все сохранённые прогоны перебора параметров этой стратегии">История прогонов{#if strategyCampaigns.length} ({strategyCampaigns.length}){/if}</button>{/snippet}
            {@render logicPanel()}
          </Frame>
        </div>
      </div>
    </div>
  {:else}
    <div class="work side" class:reverse={profile === 'chart-right'}>
      <Frame fid="chart" title="График + доходность" bind:maxId basis={sdChart}>{@render chartRegion()}</Frame>
      <Splitter dir="v" bind:size={sdChart} min={280} def={1050} invert={profile === 'chart-right'} storageKey="ars_sd_chart" />
      <div class="panels-col scroll">
        <Frame fid="ping" title="Задержка до биржи" bind:maxId basis={sdPing}>{@render pingPanel()}</Frame>
        <Splitter dir="h" bind:size={sdPing} min={46} def={110} storageKey="ars_sd_ping" />
        <Frame fid="diag" title="Диагностика и сверка QUIK" bind:maxId basis={sdDiag}>{@render diagPanel()}</Frame>
        <Splitter dir="h" bind:size={sdDiag} min={60} def={150} storageKey="ars_sd_diag" />
        <Frame fid="signal" title="Сигнал сейчас" bind:maxId basis={sdSig}>{@render signalPanel()}</Frame>
        <Splitter dir="h" bind:size={sdSig} min={80} def={190} storageKey="ars_sd_sig" />
        <Frame fid="trades" title={`Сделки робота (${trades.length})`} bind:maxId basis={sdTrd}>{@render tradesPanel()}</Frame>
        <Splitter dir="h" bind:size={sdTrd} min={80} def={230} storageKey="ars_sd_trd" />
        <Frame fid="equity" title="Доходность (журнал)" bind:maxId basis={sdEq}>
          <EquityChart robotId={robotId} compact />
        </Frame>
        <Splitter dir="h" bind:size={sdEq} min={120} def={220} storageKey="ars_sd_eq" />
        <Frame fid="logic" title="Логика стратегии" bind:maxId>
          {#snippet head()}<button class="pe-btn hist" onclick={toggleHistory} title="все сохранённые прогоны перебора параметров этой стратегии">История прогонов{#if strategyCampaigns.length} ({strategyCampaigns.length}){/if}</button>{/snippet}
          {@render logicPanel()}
        </Frame>
      </div>
    </div>
  {/if}
</div>

<style>
  .ars { display: flex; flex-direction: column; height: 100vh; background: #0a0a12; color: #ccc; overflow: hidden; position: relative; }
  .ars-head { display: flex; align-items: center; gap: 8px; padding: 8px 14px; border-bottom: 1px solid #22224a; flex-wrap: wrap; flex-shrink: 0; }
  .ars-icon { font-size: 16px; }
  .ars-name { font-size: 14px; font-weight: 600; color: #eee; font-family: monospace; }
  .ars-rename { background: none; border: none; color: #667; cursor: pointer; font-size: 12px; padding: 0 2px; }
  .ars-rename:hover { color: #6aa8ff; }
  .badge { font-size: 10px; padding: 2px 8px; border-radius: 3px; background: #16162c; border: 1px solid #2d2d4a; color: #99a; }
  .badge.real { background: #2a0a0a; border-color: #f44336; color: #ff6b5e; font-weight: 700; }
  .badge.sym { color: #4caf50; font-family: monospace; }
  .badge.ok { color: #00e676; border-color: #00e67655; }
  .badge.warn { color: #ffb300; border-color: #ffb30055; }
  .badge.pos.long { color: #00e676; } .badge.pos.short { color: #ff5c8a; }
  .badge.pnl.up { color: #00e676; } .badge.pnl.dn { color: #f44336; }
  .badge.dim { color: #667; }
  .feed-warn { background: #1a1000; border-bottom: 1px solid #ff980044; color: #ffb74d; font-size: 11px; padding: 5px 14px; flex-shrink: 0; }
  .feed-warn.calm { background: #0e1a12; border-bottom-color: #4caf5044; color: #81c784; }
  .ars-chart { min-height: 0; min-width: 0; display: flex; width: 100%; height: 100%; }
  .ars-chart-body { flex: 1; min-width: 0; min-height: 0; }
  .ars-lat { min-height: 0; height: 100%; width: 100%; border-top: 1px solid #1a1a2e; overflow: hidden; }

  /* QUIK-link diagnostics + recon */
  /* shrinkable + self-scrolling so a short viewport clips THIS row, never the
     bottom signal/orders/fills panels (root .ars is overflow:hidden) */
  .ars-diag-row { min-height: 0; height: 100%; width: 100%; display: flex; gap: 1px; border-top: 1px solid #22224a; background: #14142a; overflow: auto; }
  .ars-diag-row.col { flex-direction: column; }
  .diag-box { flex: 1; min-width: 0; padding: 8px 12px; background: #0a0a15; overflow-y: auto; }
  .diag-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 5px 14px; }
  .dg { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; border-bottom: 1px dotted #17172e; padding-bottom: 2px; }
  .dgk { font-size: 10px; color: #667; }
  .dgv { font-size: 11px; color: #cfd; font-family: monospace; }
  .dg.ok .dgv { color: #4caf50; }
  .dg.bad .dgv { color: #ff5252; font-weight: 700; }
  .dg.warn .dgv { color: #ffb300; }
  .feed-row { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
  .feed-chip { font-size: 10px; font-family: monospace; color: #8a8; background: #0e1a0e; border: 1px solid #1e3a1e; border-radius: 3px; padding: 1px 6px; }
  .feed-chip.cur { border-color: #4caf5088; color: #7fe; }
  .feed-chip.stale { color: #ff9800; background: #1a1200; border-color: #ff980044; }

  .recon-box.reconok { box-shadow: inset 3px 0 0 #4caf50; }
  .recon-box.mismatch { box-shadow: inset 3px 0 0 #ff5252; }
  .recon-state { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 3px; margin-left: 8px; letter-spacing: 0.5px; }
  .recon-state.ok { color: #4caf50; background: #11271a; }
  .recon-state.bad { color: #ff5252; background: #2a1414; }
  .recon-line { font-size: 11px; color: #bcd; display: flex; flex-wrap: wrap; gap: 12px; margin-top: 5px; }
  .recon-line b { color: #fff; font-family: monospace; }
  .recon-line.dim { color: #667; }
  .rok { color: #4caf50; }
  .rok.bad { color: #ff5252; font-weight: 700; }
  .recon-line.manual { margin-top: 8px; padding-top: 6px; border-top: 1px dotted #22224a; }
  .mlabel { color: #889; }
  .mchip { font-size: 10px; font-family: monospace; color: #b388ff; background: #14102a; border: 1px solid #2a1f4a; border-radius: 3px; padding: 1px 6px; }

  /* ── layout profiles: draggable frame grid (stack / chart-left / chart-right) ── */
  /* position:relative so a maximized Frame (position:absolute; inset:0) fills the
     work area, not the whole viewport (it must not cover the header). */
  .work { flex: 1; min-height: 0; min-width: 0; display: flex; position: relative; }
  .tnote { padding: 2px 6px 4px; }
  .work.stack { flex-direction: column; }
  .work.side { flex-direction: row; }
  .work.side.reverse { flex-direction: row-reverse; }
  .panels-col { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; }
  .panels-col.scroll { overflow-y: auto; overflow-x: hidden; }
  .bottom-row { flex: 1; min-height: 0; min-width: 0; display: flex; border-top: 1px solid #22224a; }
  .lay-switch { margin-left: auto; display: flex; gap: 2px; }
  .lay-switch button { background: #16162c; border: 1px solid #2d2d4a; color: #99a; font-size: 13px; line-height: 1; padding: 2px 8px; border-radius: 3px; cursor: pointer; }
  .lay-switch button:hover { color: #fff; border-color: #4d4d7a; }
  .lay-switch button.on { background: #0e2a18; border-color: #2e7d32; color: #66bb6a; }

  /* params editor */
  .rc-btn { margin-left: 6px; font-size: 10px; font-weight: 600; padding: 3px 10px; border-radius: 3px; cursor: pointer;
    background: #2a1e0a; border: 1px solid #b8860b; color: #ffca7a; }
  .rc-btn:hover:not(:disabled) { background: #352712; }
  .rc-btn:disabled { opacity: .5; cursor: default; }
  .rc-btn.danger { background: #2a0a0a; border: 1px solid #f44336; color: #ff8a80; }
  .rc-btn.danger:hover:not(:disabled) { background: #3a0e0e; }
  .rc-btn.danger:disabled { opacity: .5; cursor: default; }
  .rc-btn.go { background: #0e2a18; border: 1px solid #2e7d32; color: #66bb6a; }
  .rc-btn.go:hover { background: #123520; }
  .rc-msg { font-size: 10px; color: #8bc34a; margin-left: 6px; }
  .pe-btn { margin-left: 8px; font-size: 10px; padding: 2px 10px; background: #16162c; border: 1px solid #2d2d4a; color: #aab; border-radius: 3px; cursor: pointer; }
  .pe-btn:hover { color: #fff; border-color: #4d4d7a; }
  .pe-btn.save { background: #0e2a18; border-color: #2e7d32; color: #66bb6a; font-weight: 600; }
  .pe-btn.save:hover:not(:disabled) { background: #12351f; }
  .pe-btn:disabled { opacity: 0.5; cursor: default; }
  .pe-btn.hist { border-color: #2d4a7a; color: #7ab8ff; }
  .pe-btn.hist:hover { border-color: #4a6aaa; color: #aad4ff; }
  .hist-box { margin: 8px 0; border: 1px solid #1a2a44; border-radius: 6px; background: #0a1120;
    padding: 6px 8px; max-height: 200px; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
  .hist-empty { font-size: 11px; color: #667; font-style: italic; padding: 4px; }
  .hist-row { display: flex; flex-direction: column; gap: 1px; padding: 5px 6px; border-radius: 4px;
    text-decoration: none; border-top: 1px solid #111a2c; }
  .hist-row:first-child { border-top: none; }
  .hist-row:hover { background: #0f1830; }
  .hist-id { font-size: 11px; color: #7ab8ff; }
  .hist-meta { font-size: 10px; color: #789; }
  .pe-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 14px; }
  .pe-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 11px; color: #99a; }
  .pe-row.spec span { color: #ffb300; }
  .pe-in { width: 90px; background: #080810; border: 1px solid #2d2d4a; color: #dde; font-size: 11px; padding: 3px 6px; border-radius: 3px; text-align: right; }
  .pe-in:focus { outline: none; border-color: #4caf50; }
  .pe-in:disabled { opacity: 0.5; }
  /* Кнопки формы ПРИБИТЫ к низу панели: список параметров длиннее окна фрейма, и
     «Сохранить» иначе не видно — оператор менял значения и не понимал, чем
     подтвердить. Фон непрозрачный, чтобы текст не просвечивал под кнопками. */
  .pe-actions { position: sticky; bottom: 0; z-index: 2; display: flex; align-items: center;
    justify-content: flex-end; gap: 8px; margin-top: 10px; padding: 8px 0 2px;
    background: linear-gradient(to bottom, #0a0a1500, #0a0a15 22%); }
  .pe-hint { margin-right: auto; font-size: 10px; color: #667; }
  .pe-msg { margin-top: 8px; font-size: 11px; color: #8bc34a; }
  /* height:100% обязателен: .fbody фрейма — блок с заданной высотой, и без этого
     .panel вырастал под контент (1525 px в окне 330), сам не прокручивался, а
     прокрутку брал на себя .fbody. Из-за этого sticky-кнопки внизу формы не
     работали, и «Сохранить» уезжала на полтора экрана вниз (найдено 29.07). */
  .panel { flex: 1; min-width: 0; height: 100%; overflow-y: auto; padding: 10px 12px; border-right: 1px solid #1a1a2e; }
  /* Trades: an explicit framed table, visible even when empty */
  .trades-frame { border: 1px solid #2a2a52; border-radius: 6px; margin: 4px; background: #0a0a15; }
  .pt-note { font-size: 10px; color: #556; text-transform: none; letter-spacing: 0; margin-left: 8px; }
  .empty-row td { color: #556; font-style: italic; padding: 14px 8px; text-align: center; }
  .act { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; letter-spacing: .3px;
    font-family: "SF Mono", Consolas, monospace; white-space: nowrap; }
  .act.a-open { background: #10233f; color: #7ab8ff; }   /* OPEN — вход */
  .act.a-avg  { background: #2a2410; color: #e0c060; }   /* AVG — усреднение против движения */
  .act.a-enf  { background: #241a3a; color: #b98cff; }   /* ENF — усиление по движению */
  .act.a-tp   { background: #0d2a16; color: #35d07f; }   /* закрытие в плюс */
  .act.a-sl   { background: #2a0d0d; color: #ff6b5e; }   /* закрытие в минус */
  .confirm { font-size: 10px; padding: 1px 6px; border-radius: 3px; }
  .confirm.paper { background: #1a2a4a; color: #7ab8ff; }
  .confirm.quik { background: #0d2a16; color: #35d07f; }
  .confirm.none { background: #2a1a0d; color: #ffb35c; }
  .panel:last-child { border-right: none; }
  .p-title { font-size: 10px; color: #667; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 7px; }
  .p-title.sub { margin-top: 12px; }
  .wait { font-size: 12px; line-height: 1.5; color: #cfd4ff; background: #12122a; border: 1px solid #2d2d5a; border-radius: 4px; padding: 8px 10px; margin-bottom: 8px; }
  .wait.sig { color: #00e676; border-color: #00e67666; background: #0a1a0d; font-weight: 600; }
  .fstats { border: 1px solid #3a3a6a; border-radius: 4px; padding: 6px 8px; margin-bottom: 8px; background: #101024; }
  .fs-est { font-size: 9px; color: #7a7a9a; border: 1px solid #33335a; border-radius: 3px; padding: 0 4px; margin-left: 4px; }
  .fs-head { font-size: 10px; letter-spacing: .06em; text-transform: uppercase; color: #8a8ab8; margin-bottom: 4px; }
  .fstats .kv b.neg { color: #ff5252; }
  .ffx { margin-top: 6px; border-top: 1px dashed #2a2a52; padding-top: 6px; }
  .ffx-btn { font-size: 10px; padding: 3px 8px; background: #1b1b3a; color: #b9b9e6;
    border: 1px solid #3a3a6a; border-radius: 3px; cursor: pointer; }
  .ffx-btn:hover:not(:disabled) { background: #24244a; }
  .ffx-btn:disabled { opacity: .55; cursor: default; }
  .ffx-run { font-size: 10px; color: #8a8ab8; margin-top: 4px; }
  .ffx-err { font-size: 10px; color: #ff5252; margin-top: 4px; }
  .ffx-res { margin-top: 4px; }
  .ffx-note { font-size: 9px; color: #6a6a8a; margin-top: 3px; }
  .ffx-chart { width: 100%; height: 90px; display: block; margin-top: 6px;
    background: #0b0b18; border: 1px solid #23234a; border-radius: 3px; }
  .ffx-chart polyline { fill: none; vector-effect: non-scaling-stroke; stroke-width: 1.2; }
  .ffx-chart .c-on { stroke: #2ee6a6; }
  .ffx-chart .c-off { stroke: #7a7ad0; stroke-dasharray: 3 2; }
  .ffx-legend { display: flex; gap: 10px; flex-wrap: wrap; font-size: 9px; margin-top: 2px; }
  .ffx-legend .on { color: #2ee6a6; } .ffx-legend .off { color: #7a7ad0; }
  .ffx-legend .sc { color: #6a6a8a; }
  .kv-grid { display: flex; flex-direction: column; gap: 3px; }
  .kv { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; padding: 3px 6px; background: #0e0e1c; border-radius: 3px; }
  .kv span { color: #889; }
  .kv b { color: #ccc; } .kv b.yes { color: #00e676; }
  .mono { font-family: monospace; }
  .plan-row { display: flex; align-items: baseline; gap: 8px; font-size: 11px; padding: 4px 6px; border-left: 2px dotted #555; margin-bottom: 3px; background: #0e0e1c; }
  .plan-row.live { border-left-style: solid; }
  .plan-row.buy b { color: #2ee6a6; } .plan-row.sell b { color: #ff5c8a; }
  .plan-row .why { color: #778; font-size: 10px; }
  .plan-row.blocked { opacity: 0.6; border-left-color: #ffb300; }
  .plan-row.blocked .why { color: #ffb300; }
  .blocked-note { font-size: 10px; color: #ffb300; background: #1d1708; border-left: 2px solid #ffb300;
    padding: 4px 6px; margin-bottom: 4px; }
  .hist-scroll { overflow-y: auto; max-height: 100%; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th { text-align: left; color: #556; font-size: 10px; text-transform: uppercase; padding: 3px 6px; position: sticky; top: 0; background: #0a0a12; }
  td { padding: 3px 6px; border-top: 1px solid #14142a; }
  td.buy { color: #2ee6a6; } td.sell { color: #ff5c8a; }
  tr.rej { opacity: 0.55; }
  td.id { color: #667; font-size: 10px; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .st { font-size: 10px; padding: 1px 5px; border-radius: 3px; background: #16162c; }
  .st-filled, .st-paper { color: #00e676; } .st-rejected { color: #f44336; } .st-skipped { color: #ffb300; }
  .comm-cell { color: #e0956b; }
  .hist-tot td { border-top: 1px solid #2d2d4a; padding-top: 5px; font-weight: 600; }
  .tot-lbl { text-align: right; color: #9ab; }
  .empty { color: #556; font-size: 11px; padding: 6px 0; }
  .desc { font-size: 11px; line-height: 1.55; color: #aab; white-space: pre-wrap; max-height: 180px; overflow-y: auto; }
  .where { font-size: 10px; font-weight: 400; color: #8a7a3a; background: #2a240f;
    border: 1px solid #4a3f18; border-radius: 3px; padding: 1px 6px; margin-left: 8px; }
  .where.live-where { color: #4caf50; background: #10240f; border-color: #24501e; }
  .strat-longread { display: inline-block; margin: 6px 0 2px; font-size: 11px; color: #6aa8ff;
    text-decoration: none; border-bottom: 1px dotted #6aa8ff55; }
  .strat-longread:hover { color: #9cc6ff; border-bottom-color: #9cc6ff; }
</style>
