<!-- AgentRobotScreen: full-page showcase of one AGENT-HOSTED robot ("все кишки").
     URL: /?agent_robot=<robot_id>[&agent=<agent_id>]
     Live chart (BacktestChart: candles + fills + working/planned order lines),
     signal internals (what the robot waits for, computed FVG features), planned
     orders, order history incl. rejected/skipped, latency pane. Polls the STL
     mirror every 5s; the agent's local state is the source of truth. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills } from '../../lib/lab-analytics';
  import BacktestChart from './BacktestChart.svelte';
  import LatencyPane from './LatencyPane.svelte';
  import AgentBookPane from './AgentBookPane.svelte';

  let { robotId, agentId = null }: { robotId: string; agentId?: string | null } = $props();

  const MSK_OFFSET = 3 * 3600;
  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);

  let report = $state<any>(null);
  let strategyDesc = $state<string>('');
  let error = $state('');

  const robot = $derived((report?.robots ?? []).find((r: any) => r.robot_id === robotId) ?? null);
  const signal = $derived.by(() => {
    try { return robot?.signal_json ? JSON.parse(robot.signal_json) : null; } catch { return null; }
  });
  const params = $derived.by(() => {
    try { return robot?.params_json ? JSON.parse(robot.params_json) : {}; } catch { return {}; }
  });
  const symbol = $derived(robot?.symbol || 'RIU6');
  const position = $derived(Number(robot?.position ?? 0));
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
  const chartFills = $derived(
    toFills(trades.filter((t: any) => EXECUTED.has(t.status)))
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

  async function load() {
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
      const res = await fetchWithAuth(`/api/v1/quik/robots-mirror${q}`);
      if (!res.ok) { error = `mirror: HTTP ${res.status}`; return; }
      report = await res.json();
      error = '';
    } catch (e) { error = String(e); }
  }

  async function loadDesc() {
    try {
      const res = await fetchWithAuth('/api/v1/strategies');
      if (!res.ok) return;
      const list = await res.json();
      const sid = robot?.strategy_id || 'fvg';
      const hit = (list ?? []).find((s: any) => s.id === sid);
      if (hit?.description) strategyDesc = hit.description;
    } catch { /* description is optional */ }
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(async () => {
    await load();
    await loadDesc();
    timer = setInterval(load, 5000);
  });
  onDestroy(() => { if (timer) clearInterval(timer); });
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
      <span class="badge pnl" class:up={Number(robot.realized_pnl ?? 0) > 0} class:dn={Number(robot.realized_pnl ?? 0) < 0}>
        P&L {Math.round(Number(robot.realized_pnl ?? 0)).toLocaleString('ru-RU')} ₽</span>
    {:else if report}
      <span class="badge warn">робот {robotId} не найден на агенте</span>
    {/if}
    {#if error}<span class="badge warn">{error}</span>{/if}
  </div>

  {#if robot && !robot.bars_count}
    <div class="feed-warn">Нет закрытых баров — поток тиков из QUIK не идёт (проверь DDE-вывод таблицы текущих торгов).</div>
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
      live={15}
      taker={false}
      openOrders={openOrders}
      plannedOrders={plannedOrders}
    />
    {/if}
    </div>
  </div>

  <div class="ars-lat"><LatencyPane minutes={360} /></div>

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

    <div class="panel">
      <div class="p-title">История заявок ({trades.length})</div>
      <div class="hist-scroll">
        {#if trades.length === 0}
          <div class="empty">Заявок пока не было.</div>
        {:else}
          <table>
            <thead><tr><th>Время (МСК)</th><th>Сторона</th><th>Кол-во</th><th>Цена</th><th>Статус</th><th>ID</th></tr></thead>
            <tbody>
              {#each [...trades].reverse() as t}
                <tr class:rej={t.status === 'rejected' || t.status === 'skipped'}>
                  <td class="mono">{fmtMskTime(t.time * 1000)}</td>
                  <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>{t.side === 'buy' ? '▲ buy' : '▼ sell'}</td>
                  <td class="mono">{t.qty}</td>
                  <td class="mono">{Math.round(t.price).toLocaleString('ru-RU')}</td>
                  <td><span class="st st-{t.status}">{t.status}</span></td>
                  <td class="mono id">{t.order_id}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    </div>

    <div class="panel">
      <div class="p-title">Логика стратегии</div>
      <div class="desc">{strategyDesc || 'Fair Value Gap (ICT) — вход по 3-барному ценовому разрыву с подтверждением телом свечи.'}</div>
      <div class="p-title sub">Параметры</div>
      <div class="kv-grid">
        {#each Object.entries(params) as [k, v]}
          <div class="kv"><span>{k}</span><b class="mono">{v}</b></div>
        {/each}
        <div class="kv"><span>max позиция</span><b class="mono">{robot?.max_position ?? '—'}</b></div>
      </div>
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
  .ars-chart { flex: 1 1 52%; min-height: 0; display: flex; }
  .ars-chart-body { flex: 1; min-width: 0; min-height: 0; }
  .ars-lat { flex: 0 0 120px; min-height: 0; border-top: 1px solid #1a1a2e; }
  .ars-bottom { flex: 0 0 30%; min-height: 180px; display: flex; border-top: 1px solid #22224a; overflow: hidden; }
  .panel { flex: 1; min-width: 0; overflow-y: auto; padding: 10px 12px; border-right: 1px solid #1a1a2e; }
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
  th { text-align: left; color: #556; font-size: 9px; text-transform: uppercase; padding: 3px 6px; position: sticky; top: 0; background: #0a0a12; }
  td { padding: 3px 6px; border-top: 1px solid #14142a; }
  td.buy { color: #2ee6a6; } td.sell { color: #ff5c8a; }
  tr.rej { opacity: 0.55; }
  td.id { color: #667; font-size: 9px; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .st { font-size: 9px; padding: 1px 5px; border-radius: 3px; background: #16162c; }
  .st-filled, .st-paper { color: #00e676; } .st-rejected { color: #f44336; } .st-skipped { color: #ffb300; }
  .empty { color: #556; font-size: 11px; padding: 6px 0; }
  .desc { font-size: 11px; line-height: 1.55; color: #aab; white-space: pre-wrap; max-height: 180px; overflow-y: auto; }
</style>
