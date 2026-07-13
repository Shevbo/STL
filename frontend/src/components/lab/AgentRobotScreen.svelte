<!-- AgentRobotScreen: full-page showcase of one AGENT-HOSTED robot ("все кишки").
     URL: /?agent_robot=<robot_id>[&agent=<agent_id>]
     Live chart (BacktestChart: candles + fills + working/planned order lines),
     signal internals (what the robot waits for, computed FVG features), planned
     orders, order history incl. rejected/skipped, latency pane. Polls the STL
     mirror every 5s; the agent's local state is the source of truth. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills, commissionFor } from '../../lib/lab-analytics';
  import BacktestChart from './BacktestChart.svelte';
  import LatencyPane from './LatencyPane.svelte';
  import AgentBookPane from './AgentBookPane.svelte';
  import ParamEditor from './ParamEditor.svelte';
  import { fetchAgentLocalStatus, type AgentLocalStatus } from '../../lib/agent-robots';

  let { robotId, agentId = null }: { robotId: string; agentId?: string | null } = $props();

  const MSK_OFFSET = 3 * 3600;
  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);

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
  const chartFills = $derived(
    toFills(trades.filter((t: any) =>
      robot?.paper ? EXECUTED.has(t.status) : t.status === 'filled'))
      .map((f: any) => ({ ...f, time: f.time + MSK_OFFSET })));

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
    const fp = JSON.stringify([symbol, fills.length,
      fills.at(-1)?.time ?? 0, fills.at(-1)?.price ?? 0, params]);
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
  let pointCoef = $state<number | null>(null);
  async function loadCoef(sym: string = symbol) {
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
      const res = await fetchWithAuth(`/api/v1/quik/params${q}`,
        { signal: AbortSignal.timeout(4000) } as any);
      if (!res.ok) return;
      const d = await res.json();
      const row = (d.rows ?? []).find((r: any) => r.code === sym);
      // Guard the async gap: only apply if the robot's symbol hasn't changed,
      // else a stale response could stamp the WRONG instrument's ₽/point.
      if (row?.coef > 0 && sym === symbol) pointCoef = Number(row.coef);
    } catch { /* optional; badge falls back to points */ }
  }
  // Re-resolve the coef when the robot's symbol becomes known/changes (on mount
  // the robot is still null, so a fixed onMount call could cache the default
  // symbol's coef for 5 min — or the wrong instrument's).
  $effect(() => { void loadCoef(symbol); });
  const pnlPoints = $derived(Number(robot?.realized_pnl ?? 0));
  const pnlRub = $derived(pointCoef ? pnlPoints * pointCoef : null);
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
  async function loadDesc(sid: string) {
    try {
      const res = await fetchWithAuth('/api/v1/strategies');
      if (!res.ok) return;
      const list = await res.json();
      const hit = (list ?? []).find((s: any) => s.id === sid);
      if (hit?.description) strategyDesc = hit.description;
      if (Array.isArray(hit?.params_schema)) paramSchema = hit.params_schema;
    } catch { /* description is optional */ }
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
    void load(); void pollTick(); void loadStatus();
    timers = [setInterval(load, 3000), setInterval(pollTick, 1000),
              setInterval(loadStatus, 4000), setInterval(loadCoef, 300_000)];
  });
  onDestroy(() => { for (const t of timers) clearInterval(t); });
</script>

<div class="ars">
  <div class="ars-head">
    <span class="ars-icon">🤖</span>
    <span class="ars-name">{robotId}</span>
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
              title="Реализованный P&L робота: закрытые сделки × ₽/пункт — авторитетное число самого агента (совпадает с его страницей 127.0.0.1:8071). Считается по филлам робота, БЕЗ учёта текущей позиции и БЕЗ биржевой комиссии; включает бумажный период до перевода на реал.">
          P&L {pnlRub > 0 ? '+' : ''}{Math.round(pnlRub).toLocaleString('ru-RU')} ₽</span>
        <span class="badge pnl" class:up={(pnlMargin ?? 0) > 0} class:dn={(pnlMargin ?? 0) < 0}
              title={`Если ударить по рынку и закрыть ВСЮ позицию прямо сейчас: реализованный ${Math.round(pnlRub).toLocaleString('ru-RU')} + вариац. маржа ${floatRub !== null ? (floatRub > 0 ? '+' : '') + Math.round(floatRub).toLocaleString('ru-RU') : '0'} − комиссия закрытия ${Math.round(closeComm).toLocaleString('ru-RU')} ₽.`}>
          P&L+Маржа {(pnlMargin ?? 0) > 0 ? '+' : ''}{Math.round(pnlMargin ?? pnlRub).toLocaleString('ru-RU')} ₽</span>
      {:else}
        <span class="badge pnl dim">P&L …</span>
      {/if}
      <span class="badge" class:ok={tickAge !== null && tickAge <= 10} class:warn={tickAge === null || tickAge > 10}
            title="возраст последнего тика QUIK (поток данных)">
        тик {tickAge === null ? '—' : tickAge + 'с'}</span>
      {#if !robot.paper}
        {#if robot.paused}
          <button class="rc-btn go" onclick={startRobot} title="возобновить работу робота">▶ Пуск</button>
        {:else}
          <button class="rc-btn" disabled={pausing} onclick={pauseRobot}
                  title="остановить новые входы; открытая позиция ОСТАЁТСЯ">
            {pausing ? '…' : '⏸ Пауза'}</button>
          <button class="rc-btn danger" disabled={flattening} onclick={flattenNow}
                  title="закрыть всю позицию по рынку и остановить робота">
            {flattening ? '…' : '⏻ Закрыть всё + стоп'}</button>
        {/if}
        {#if flattenMsg}<span class="rc-msg">{flattenMsg}</span>{/if}
      {/if}
    {:else if report}
      <span class="badge warn">робот {robotId} не найден на агенте</span>
    {/if}
    {#if error}<span class="badge warn">{error}</span>{/if}
  </div>

  {#if robot && !robot.bars_count}
    {#if tickAge !== null && tickAge <= 10}
      <div class="feed-warn calm">Копим первый закрытый бар после рестарта (~1 мин) — тики из QUIK идут, стратегия ждёт свечу.</div>
    {:else}
      <div class="feed-warn">Нет закрытых баров и поток тиков из QUIK не идёт — проверь QLua-скрипт shectory_trade (файловая очередь) и открытые окна QUIK.</div>
    {/if}
  {/if}

  <div class="ars-chart">
    <AgentBookPane {symbol} {agentId} depth={10} />
    <div class="ars-chart-body">
    {#if chartResult}
    <BacktestChart
      result={chartResult}
      {symbol}
      dateFrom={dateFrom}
      dateTo={dateTo}
      defaultInterval={1}
      live={20}
      liveTick={liveTick}
      pointValue={pointCoef ?? 1}
      taker={!robot?.paper}
      openOrders={openOrders}
      plannedOrders={plannedOrders}
      netOverride={robot?.paper ? null : pnlRub}
      floatRub={robot?.paper ? null : floatNetRub}
      livePosition={robot ? position : null}
    />
    {/if}
    </div>
  </div>

  <div class="ars-lat"><LatencyPane minutes={360} /></div>

  <!-- QUIK-link diagnostics + recon vs QUIK account tables (agent local status) -->
  <div class="ars-diag-row">
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

  <div class="ars-bottom">
    <div class="panel">
      <div class="p-title">Сигнал сейчас</div>
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
        <div class="p-title sub">Планируемые заявки</div>
        {#if plannedOrders.length || armedOrders.length}
          {#each plannedOrders as p}
            <div class="plan-row" class:buy={p.side === 'buy'} class:sell={p.side === 'sell'}>
              <b>{p.side === 'buy' ? '▲ BUY' : '▼ SELL'} {p.qty}</b>
              <span class="mono">@ {Math.round(p.price).toLocaleString('ru-RU')}</span>
              <span class="why">{p.reason}</span>
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
          <div class="p-title sub">Выставленные заявки</div>
          {#each openOrders as o}
            <div class="plan-row live" class:buy={o.side === 'buy'} class:sell={o.side === 'sell'}>
              <b>{o.side === 'buy' ? '▲ BUY' : '▼ SELL'} {o.qty}</b>
              <span class="mono">@ {Math.round(o.price).toLocaleString('ru-RU')}</span>
              <span class="why mono">{o.order_id}</span>
            </div>
          {/each}
        {/if}
      {:else}
        <div class="empty">Диагностика сигнала ещё не пришла.</div>
      {/if}
    </div>

    <div class="panel trades-frame">
      <div class="p-title">Сделки робота ({trades.length})
        <span class="pt-note">каждая строка = заявка робота; «Подтверждение» — ID из QUIK или paper</span></div>
      <div class="hist-scroll">
        <table>
          <thead><tr><th>Время (МСК)</th><th>Сторона</th><th>Кол-во</th><th>Цена</th><th>Статус</th><th>Подтверждение</th></tr></thead>
          <tbody>
            {#if trades.length === 0}
              <tr class="empty-row"><td colspan="6">Сделок пока нет — робот ждёт сигнала.</td></tr>
            {:else}
              {#each [...trades].reverse() as t}
                <tr class:rej={t.status === 'rejected' || t.status === 'skipped'}>
                  <td class="mono">{fmtMskTime(t.time * 1000)}</td>
                  <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>{t.side === 'buy' ? '▲ buy' : '▼ sell'}</td>
                  <td class="mono">{t.qty}</td>
                  <td class="mono">{Math.round(t.price).toLocaleString('ru-RU')}</td>
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
        </table>
      </div>
    </div>

    <div class="panel">
      <div class="p-title">Логика стратегии
        <button class="pe-btn hist" onclick={toggleHistory}
                title="все сохранённые прогоны перебора параметров этой стратегии">
          История прогонов{#if strategyCampaigns.length} ({strategyCampaigns.length}){/if}</button>
      </div>
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
          <button class="pe-btn" onclick={() => { editMode = false; saveMsg = ''; }}>Отмена</button>
          <button class="pe-btn save" disabled={saving} onclick={saveParams}>
            {saving ? 'Сохраняю…' : 'Сохранить'}</button>
        </div>
        {#if saveMsg}<div class="pe-msg">{saveMsg}</div>{/if}
      {/if}
    </div>
  </div>
</div>

<style>
  .ars { display: flex; flex-direction: column; height: 100vh; background: #0a0a12; color: #ccc; overflow: hidden; }
  .ars-head { display: flex; align-items: center; gap: 8px; padding: 8px 14px; border-bottom: 1px solid #22224a; flex-wrap: wrap; flex-shrink: 0; }
  .ars-icon { font-size: 16px; }
  .ars-name { font-size: 14px; font-weight: 600; color: #eee; font-family: monospace; }
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
  .ars-chart { flex: 1 1 52%; min-height: 0; display: flex; }
  .ars-chart-body { flex: 1; min-width: 0; min-height: 0; }
  .ars-lat { flex: 0 0 120px; min-height: 0; border-top: 1px solid #1a1a2e; }

  /* QUIK-link diagnostics + recon */
  /* shrinkable + self-scrolling so a short viewport clips THIS row, never the
     bottom signal/orders/fills panels (root .ars is overflow:hidden) */
  .ars-diag-row { flex: 0 1 auto; min-height: 0; max-height: 26%; display: flex; gap: 1px; border-top: 1px solid #22224a; background: #14142a; }
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

  .ars-bottom { flex: 0 0 30%; min-height: 180px; display: flex; border-top: 1px solid #22224a; overflow: hidden; }

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
  .pe-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
  .pe-msg { margin-top: 8px; font-size: 11px; color: #8bc34a; }
  .panel { flex: 1; min-width: 0; overflow-y: auto; padding: 10px 12px; border-right: 1px solid #1a1a2e; }
  /* Trades: an explicit framed table, visible even when empty */
  .trades-frame { border: 1px solid #2a2a52; border-radius: 6px; margin: 4px; background: #0a0a15; }
  .pt-note { font-size: 10px; color: #556; text-transform: none; letter-spacing: 0; margin-left: 8px; }
  .empty-row td { color: #556; font-style: italic; padding: 14px 8px; text-align: center; }
  .confirm { font-size: 10px; padding: 1px 6px; border-radius: 3px; }
  .confirm.paper { background: #1a2a4a; color: #7ab8ff; }
  .confirm.quik { background: #0d2a16; color: #35d07f; }
  .confirm.none { background: #2a1a0d; color: #ffb35c; }
  .panel:last-child { border-right: none; }
  .p-title { font-size: 10px; color: #667; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 7px; }
  .p-title.sub { margin-top: 12px; }
  .wait { font-size: 12px; line-height: 1.5; color: #cfd4ff; background: #12122a; border: 1px solid #2d2d5a; border-radius: 4px; padding: 8px 10px; margin-bottom: 8px; }
  .wait.sig { color: #00e676; border-color: #00e67666; background: #0a1a0d; font-weight: 600; }
  .kv-grid { display: flex; flex-direction: column; gap: 3px; }
  .kv { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; padding: 3px 6px; background: #0e0e1c; border-radius: 3px; }
  .kv span { color: #889; }
  .kv b { color: #ccc; } .kv b.yes { color: #00e676; }
  .mono { font-family: monospace; }
  .plan-row { display: flex; align-items: baseline; gap: 8px; font-size: 11px; padding: 4px 6px; border-left: 2px dotted #555; margin-bottom: 3px; background: #0e0e1c; }
  .plan-row.live { border-left-style: solid; }
  .plan-row.buy b { color: #2ee6a6; } .plan-row.sell b { color: #ff5c8a; }
  .plan-row .why { color: #778; font-size: 10px; }
  .hist-scroll { overflow-y: auto; max-height: 100%; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th { text-align: left; color: #556; font-size: 10px; text-transform: uppercase; padding: 3px 6px; position: sticky; top: 0; background: #0a0a12; }
  td { padding: 3px 6px; border-top: 1px solid #14142a; }
  td.buy { color: #2ee6a6; } td.sell { color: #ff5c8a; }
  tr.rej { opacity: 0.55; }
  td.id { color: #667; font-size: 10px; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .st { font-size: 10px; padding: 1px 5px; border-radius: 3px; background: #16162c; }
  .st-filled, .st-paper { color: #00e676; } .st-rejected { color: #f44336; } .st-skipped { color: #ffb300; }
  .empty { color: #556; font-size: 11px; padding: 6px 0; }
  .desc { font-size: 11px; line-height: 1.55; color: #aab; white-space: pre-wrap; max-height: 180px; overflow-y: auto; }
</style>
