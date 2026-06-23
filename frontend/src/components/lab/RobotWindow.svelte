<!-- RobotWindow.svelte
     Modal opened on double-click of a robot. One screen with:
       • параметры        — robot params + paper/real badge
       • график           — instrument candles (BacktestChart)
       • заявки на графике — order markers (executed plot; rejected/skipped in table)
       • сделки на графике — fill markers + open→close connectors (BacktestChart)
       • текущий результат — ruble PnL / position / win-rate summary + chart overlay
       • история сделок    — full live_trades table (all orders incl. rejected)
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills, replay, computeStats, tradeEvents } from '../../lib/lab-analytics';
  import BacktestChart from './BacktestChart.svelte';

  let { robotId, onClose }: { robotId: string; onClose: () => void } = $props();

  let loading = $state(true);
  let error = $state('');
  let live = $state<any>(null);

  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);
  // Candle epochs carry Moscow wall-clock stamped as UTC (ISS convention), but
  // live_trades.timestamp is real UTC. Shift fill times +3h onto the candle's
  // axis so markers/equity land on the right candle. History table keeps the
  // real timestamp (formatted to MSK separately) — this offset is chart-only.
  const MSK_OFFSET = 3 * 3600;

  // Resizable frames: chart vs bottom (vertical), left panel vs trades (horizontal).
  let chartFrac = $state(0.62);          // chart height as fraction of the body
  let leftW = $state(320);               // left panel width in px
  let drag: { kind: 'chart' | 'left'; x: number; y: number; frac: number; w: number; body: HTMLElement | null } | null = null;
  function startDrag(kind: 'chart' | 'left', e: PointerEvent) {
    const body = (e.currentTarget as HTMLElement).closest('.win-body') as HTMLElement | null;
    drag = { kind, x: e.clientX, y: e.clientY, frac: chartFrac, w: leftW, body };
    window.addEventListener('pointermove', onDragMove);
    window.addEventListener('pointerup', onDragUp);
    e.preventDefault();
  }
  function onDragMove(e: PointerEvent) {
    if (!drag) return;
    if (drag.kind === 'chart' && drag.body) {
      const h = drag.body.clientHeight || 1;
      chartFrac = Math.min(0.85, Math.max(0.25, drag.frac + (e.clientY - drag.y) / h));
    } else if (drag.kind === 'left') {
      leftW = Math.min(560, Math.max(220, drag.w + (e.clientX - drag.x)));
    }
  }
  function onDragUp() {
    drag = null;
    window.removeEventListener('pointermove', onDragMove);
    window.removeEventListener('pointerup', onDragUp);
  }

  // Fills that actually changed the position (exclude rejected/skipped).
  let chartFills = $derived(
    live
      ? toFills((live.trades ?? []).filter((t: any) => EXECUTED.has(t.status)))
          .map((f: any) => ({ ...f, time: f.time + MSK_OFFSET }))
      : []
  );
  let replayed = $derived(replay(chartFills));
  let pv = $derived(live?.point_value ?? 1);

  // Param key → schema entry (label, hint, desc) for the (i) popovers.
  let schemaByKey = $derived.by(() => {
    const m: Record<string, any> = {};
    for (const p of (live?.strategy?.params_schema ?? [])) m[p.key] = p;
    return m;
  });
  let openInfo = $state<string | null>(null);
  let hoverInfo = $state<string | null>(null);
  // Net per-close events (rubles, commission deducted) — single source for money.
  // Live = MAKER model (limit orders rest in book): only broker fee, no exchange fee.
  let events = $derived(tradeEvents(chartFills, 60, pv, live?.symbol ?? '', false));
  let closes = $derived(events.filter(e => e.close).map(e => e.close!));

  // Map each executed fill back to its lifecycle event so the history table can
  // show the trade TYPE (TP/SL/AVG/вход/реверс) and, on TP/SL, the round-trip P&L.
  // chartFills are shifted +MSK_OFFSET, so events.rawTime == rawTrade.time + offset.
  let eventByKey = $derived.by(() => {
    const m = new Map<string, (typeof events)[number]>();
    for (const e of events) m.set(`${e.rawTime}_${e.side}_${e.qty}_${e.price}`, e);
    return m;
  });
  function tradeEvent(t: any) {
    if (t.status === 'rejected' || t.status === 'skipped') return null;
    return eventByKey.get(`${t.time + MSK_OFFSET}_${t.side}_${Number(t.qty) || 1}_${t.price}`) ?? null;
  }
  // Type badge: TP/SL (closing fill, profit/loss), AVG (averaging), вход (open),
  // реверс (flip-through-zero open leg without its own close), — for rejected.
  function tradeTypeLabel(ev: any): { text: string; cls: string } {
    if (!ev) return { text: '—', cls: 'tt-none' };
    if (ev.close) return { text: ev.close.exit, cls: ev.close.exit === 'TP' ? 'tt-tp' : 'tt-sl' };
    if (ev.kind === 'average') return { text: 'AVG', cls: 'tt-avg' };
    if (ev.kind === 'reverse') return { text: 'реверс', cls: 'tt-rev' };
    return { text: 'вход', cls: 'tt-open' };
  }

  // Cumulative realized P&L curve in rubles (NET of commission), starting at 0 —
  // there is no start capital; the curve is running profit/loss, not account equity.
  let equityCurve = $derived.by(() => {
    if (!chartFills.length) return [];
    const pts: any[] = [{ time: chartFills[0].time, equity: 0 }];
    let cum = 0;
    for (const e of events) {
      if (e.close) { cum += e.close.pnl; pts.push({ time: e.rawTime, equity: cum }); }
    }
    return pts;
  });

  // Synthetic "result" so BacktestChart renders candles + markers + connectors + equity.
  let chartResult = $derived(
    live ? { trades: chartFills, equity_curve: equityCurve, params: live.robot?.params_json ?? {} } : null
  );

  // Current result summary (rubles, net of commission).
  let summary = $derived.by(() => {
    const s = computeStats(chartFills, replayed.roundTrips, equityCurve);
    let signed = 0;
    for (const f of chartFills) signed += f.side === 'buy' ? f.qty : -f.qty;
    const wins = closes.filter(c => c.pnl > 0).length;
    const net = equityCurve.length ? equityCurve[equityCurve.length - 1].equity - equityCurve[0].equity : 0;
    // ГО (margin at risk) = per-contract initial margin × peak contracts held.
    // P&L % is return on THIS, not on any fictional start capital.
    const go = (live?.initial_margin ?? 0) * (s.maxAbsPos || 0);
    return {
      net,
      go,
      retPct: go > 0 ? (net / go) * 100 : 0,
      position: signed,
      roundTrips: closes.length,
      longRT: s.longRT,
      shortRT: s.shortRT,
      winRate: closes.length ? (wins / closes.length) * 100 : 0,
      orders: (live?.trades ?? []).length,
    };
  });

  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });

  async function load() {
    loading = true; error = '';
    try {
      const res = await fetchWithAuth(`/api/v1/robots/${robotId}/live`);
      if (!res.ok) throw new Error(await res.text());
      live = await res.json();
    } catch (e) { error = String(e); }
    loading = false;
  }

  function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
  onMount(load);
</script>

<svelte:window onkeydown={onKey} />

<div class="overlay" role="presentation" onclick={onClose} onkeydown={(e) => e.key === 'Escape' && onClose()}>
  <!-- stop propagation so clicks inside the window don't close it -->
  <div class="window" role="dialog" aria-modal="true" tabindex="-1"
       onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>

    <div class="win-header">
      <span class="win-icon">🤖</span>
      <span class="win-name">{live?.robot?.name ?? 'Робот'}</span>
      {#if live}
        <span class="badge" class:real={!live.paper}>{live.paper ? 'PAPER' : 'РЕАЛ'}</span>
        <span class="badge sym">{live.symbol}</span>
        <span class="badge dim">{live.robot?.deployed ? 'LIVE' : 'остановлен'}</span>
        <span class="badge dim">окно {live.robot?.schedule}</span>
      {/if}
      <button class="close" onclick={onClose} title="Закрыть (Esc)">✕</button>
    </div>

    {#if loading}
      <div class="state">Загрузка…</div>
    {:else if error}
      <div class="state err">{error}</div>
    {:else if live}
      <div class="win-body">
        <!-- chart: candles + order/trade markers + equity + result overlay -->
        <div class="chart-wrap" style="flex: 0 0 {chartFrac * 100}%">
          <BacktestChart
            result={chartResult}
            symbol={live.chart_symbol ?? live.symbol}
            dateFrom={live.date_from}
            dateTo={live.date_to}
            pointValue={pv}
            defaultInterval={5}
            taker={false}
            openOrders={live.open_orders ?? []}
            plannedOrders={live.planned_orders ?? []}
          />
        </div>

        <!-- drag handle: resize chart vs tables -->
        <div class="rw-hsplit" title="Потяните — высота графика" onpointerdown={(e) => startDrag('chart', e)}></div>

        <!-- bottom: params + current result (left) | trade history (right) -->
        <div class="win-bottom">
          <div class="panel left" style="flex: 0 0 {leftW}px">
            {#if live.strategy}
              <div class="panel-title">О стратегии</div>
              <div class="about-box">
                <div class="about-name">{live.strategy.name}</div>
                {#if live.strategy.description}<div class="about-desc">{live.strategy.description}</div>{/if}
                {#if live.strategy.source}
                  <a class="about-link" href={live.strategy.source} target="_blank" rel="noopener">Подробное описание робота ↗</a>
                {/if}
              </div>
            {/if}

            <div class="panel-title">Параметры</div>
            <div class="kv-grid">
              {#each Object.entries(live.robot?.params_json ?? {}) as [k, v]}
                {@const sp = schemaByKey[k]}
                <div class="kv">
                  <span class="k">
                    {sp?.label ?? k}
                    {#if sp?.desc || sp?.hint}
                      <span class="kv-i" role="button" tabindex="0" aria-label="Описание"
                        onclick={() => openInfo = openInfo === k ? null : k}
                        onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (openInfo = openInfo === k ? null : k)}
                        onmouseenter={() => hoverInfo = k} onmouseleave={() => hoverInfo = null}
                      >ⓘ</span>
                    {/if}
                    {#if (sp?.desc || sp?.hint) && (openInfo === k || hoverInfo === k)}
                      <div class="kv-popover">
                        <div class="pp-title">{sp.label ?? k}</div>
                        <div class="pp-body">{sp.desc || sp.hint}</div>
                      </div>
                    {/if}
                  </span>
                  <span class="v">{v}</span>
                </div>
              {/each}
              <div class="kv"><span class="k">ГО / контракт</span>
                <span class="v">{live.initial_margin != null ? Math.round(live.initial_margin).toLocaleString('ru-RU') + ' ₽' : '—'}</span></div>
              <div class="kv"><span class="k">пункт = ₽</span><span class="v">{pv}</span></div>
              <div class="kv"><span class="k">комиссия</span><span class="v">4 ₽ / заявка (тейкер)</span></div>
            </div>

            <div class="panel-title res-title">Текущий результат</div>
            <div class="result-grid">
              <div class="r-row"><span>Доход</span>
                <b class:pos={summary.net > 0} class:neg={summary.net < 0}>{fmtMoney(summary.net)} ₽</b>
                <span class="sub">({summary.retPct >= 0 ? '+' : ''}{summary.retPct.toFixed(2)}% от ГО)</span></div>
              <div class="r-row"><span>ГО (макс. задейств.)</span>
                <b>{summary.go > 0 ? Math.round(summary.go).toLocaleString('ru-RU') + ' ₽' : '—'}</b></div>
              <div class="r-row"><span>Позиция сейчас</span>
                <b class:pos={summary.position > 0} class:neg={summary.position < 0}>
                  {summary.position > 0 ? '+' : ''}{summary.position} конт.</b></div>
              <div class="r-row"><span>Сделок (круг)</span><b>{summary.roundTrips}</b>
                <span class="sub">L {summary.longRT} / S {summary.shortRT}</span></div>
              <div class="r-row"><span>Win rate</span><b>{summary.winRate.toFixed(0)}%</b></div>
              <div class="r-row"><span>Всего заявок</span><b>{summary.orders}</b></div>
            </div>
            <div class="basis">P&L % — доход относительно макс. задействованного ГО (ГО/контракт × пик контрактов)</div>
          </div>

          <!-- drag handle: resize left panel vs trades -->
          <div class="rw-vsplit" title="Потяните — ширина панели" onpointerdown={(e) => startDrag('left', e)}></div>

          <div class="panel right">
            <div class="panel-title">История сделок ({(live.trades ?? []).length})</div>
            <div class="history-scroll">
              {#if (live.trades ?? []).length === 0}
                <div class="empty">Сделок пока нет. Робот ждёт сигнала.</div>
              {:else}
                <table>
                  <thead>
                    <tr><th>Время (МСК)</th><th>Тип</th><th>Сторона</th><th>Кол-во</th><th>Цена</th><th class="num">Фин. рез</th><th>Статус</th></tr>
                  </thead>
                  <tbody>
                    {#each [...live.trades].reverse() as t}
                      {@const ev = tradeEvent(t)}
                      {@const tt = tradeTypeLabel(ev)}
                      <tr class:rejected={t.status === 'rejected' || t.status === 'skipped'}>
                        <td class="mono">{fmtTime(t.iso)}</td>
                        <td><span class="tt-badge {tt.cls}">{tt.text}</span></td>
                        <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>
                          {t.side === 'buy' ? '▲ buy' : '▼ sell'}</td>
                        <td class="mono">{t.qty}</td>
                        <td class="mono">{Math.round(t.price).toLocaleString('ru-RU')}</td>
                        <td class="num mono" class:pos={ev?.close && ev.close.pnl > 0} class:neg={ev?.close && ev.close.pnl < 0}>
                          {ev?.close ? fmtMoney(ev.close.pnl) + ' ₽' : '—'}</td>
                        <td><span class="st-badge st-{t.status}">{t.status}</span></td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
            </div>
          </div>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 2000;
    background: #000000cc; display: flex; align-items: center; justify-content: center;
    backdrop-filter: blur(2px);
  }
  .window {
    width: 92vw; height: 90vh; background: #0a0a15;
    border: 1px solid #2d2d4a; border-radius: 8px;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 12px 48px #000000aa;
  }

  .win-header {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 14px; background: #12121f; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .win-icon { font-size: 15px; }
  .win-name { font-size: 14px; color: #4caf50; font-weight: 600; }
  .badge {
    font-size: 10px; padding: 2px 7px; border-radius: 3px;
    background: #1a3a1a; color: #4caf50; border: 1px solid #4caf5066;
  }
  .badge.real { background: #3a1010; color: #ff6b6b; border-color: #ff6b6b66; }
  .badge.sym { background: #1a1a2e; color: #6aa8ff; border-color: #6aa8ff44; }
  .badge.dim { background: #14141f; color: #777; border-color: #2d2d4a; }
  .close {
    margin-left: auto; background: none; border: none; color: #888;
    font-size: 16px; cursor: pointer; padding: 0 4px;
  }
  .close:hover { color: #f44336; }

  .state { flex: 1; display: flex; align-items: center; justify-content: center; color: #666; font-size: 13px; }
  .state.err { color: #f4433699; padding: 20px; text-align: center; }

  .win-body { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  .chart-wrap { flex: 0 0 62%; min-height: 0; }

  .win-bottom { flex: 1 1 0; display: flex; min-height: 0; }

  /* Resize handles */
  .rw-hsplit { flex: 0 0 6px; cursor: row-resize; background: #1a1a2e; border-top: 1px solid #0a0a15; border-bottom: 1px solid #0a0a15; }
  .rw-hsplit:hover { background: #2d4a2d; }
  .rw-vsplit { flex: 0 0 6px; cursor: col-resize; background: #1a1a2e; align-self: stretch; }
  .rw-vsplit:hover { background: #2d4a2d; }
  .panel { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .panel.left { flex: 0 0 320px; border-right: 1px solid #1a1a2e; padding: 10px 12px; overflow-y: auto; }
  .panel.right { flex: 1; min-width: 0; }

  .panel-title {
    font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 0.5px;
    margin-bottom: 6px;
  }
  .res-title { margin-top: 14px; }

  .kv-grid { display: flex; flex-direction: column; gap: 3px; }
  .kv { display: flex; justify-content: space-between; padding: 3px 7px; background: #0f0f1e; border-radius: 3px; }
  .kv .k { font-size: 11px; color: #888; position: relative; }
  .kv .v { font-size: 11px; color: #4caf50; font-family: monospace; }

  .about-box { background: #0a1a0a; border: 1px solid #1e3a1e; border-radius: 4px; padding: 8px 10px; margin-bottom: 12px; }
  .about-name { font-size: 12px; color: #4caf50; font-weight: 600; margin-bottom: 4px; }
  .about-desc { font-size: 10px; color: #999; line-height: 1.5; margin-bottom: 6px; }
  .about-link { font-size: 10px; color: #6aa8ff; text-decoration: none; }
  .about-link:hover { text-decoration: underline; }

  .kv-i {
    display: inline-flex; align-items: center; justify-content: center;
    width: 13px; height: 13px; margin-left: 4px; border-radius: 50%;
    font-size: 9px; color: #6aa8ff; border: 1px solid #6aa8ff66; cursor: help; user-select: none;
  }
  .kv-i:hover { background: #6aa8ff22; }
  .kv-popover {
    position: absolute; left: 0; top: 100%; margin-top: 4px; z-index: 30;
    width: 230px; background: #12121f; border: 1px solid #3d3d5a; border-radius: 4px;
    padding: 7px 9px; box-shadow: 0 4px 16px #000000aa;
  }
  .pp-title { font-size: 11px; color: #fff; font-weight: 600; margin-bottom: 3px; }
  .pp-body { font-size: 10px; color: #bbb; line-height: 1.5; }

  .result-grid { display: flex; flex-direction: column; gap: 4px; }
  .r-row { display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: #999; }
  .r-row span:first-child { flex: 1; }
  .r-row b { color: #ccc; font-size: 12px; font-family: monospace; }
  .r-row .sub { color: #666; font-size: 10px; flex: none; }
  .basis { margin-top: 8px; font-size: 9px; color: #555; font-style: italic; }
  .pos { color: #4caf50 !important; } .neg { color: #f44336 !important; }

  .history-scroll { flex: 1; overflow-y: auto; min-height: 0; }
  table { width: 100%; border-collapse: collapse; }
  thead th {
    position: sticky; top: 0; background: #12121f; z-index: 1;
    font-size: 10px; color: #666; text-align: left; padding: 5px 10px; font-weight: 500;
    border-bottom: 1px solid #2d2d4a;
  }
  tbody td { font-size: 11px; color: #bbb; padding: 4px 10px; border-bottom: 1px solid #14141f; }
  tbody tr:hover { background: #12121f; }
  tbody tr.rejected td { opacity: 0.5; }
  .mono { font-family: monospace; }
  td.buy { color: #4caf50; } td.sell { color: #f44336; }
  thead th.num, tbody td.num { text-align: right; }
  .tt-badge { font-size: 9px; padding: 1px 6px; border-radius: 2px; background: #1a1a2e; color: #888; font-weight: 600; }
  .tt-tp { background: #11271a; color: #4caf50; }
  .tt-sl { background: #2a1414; color: #ff6b6b; }
  .tt-avg { background: #2a2410; color: #ffb300; }
  .tt-rev { background: #1a1430; color: #b388ff; }
  .tt-open { background: #14222a; color: #6aa8ff; }
  .tt-none { background: transparent; color: #555; }
  .st-badge { font-size: 9px; padding: 1px 6px; border-radius: 2px; background: #1a1a2e; color: #888; }
  .st-paper { background: #1a2a1a; color: #4caf50; }
  .st-rejected, .st-skipped { background: #2a1414; color: #ff6b6b; }
  .st-submitted, .st-filled, .st-executed { background: #14222a; color: #6aa8ff; }
  .empty { padding: 24px; text-align: center; color: #555; font-size: 12px; }
</style>
