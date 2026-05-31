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
  import { toFills, replay, computeStats } from '../../lib/lab-analytics';
  import BacktestChart from './BacktestChart.svelte';

  let { robotId, onClose }: { robotId: string; onClose: () => void } = $props();

  let loading = $state(true);
  let error = $state('');
  let live = $state<any>(null);

  const INITIAL_EQUITY = 100000;
  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);

  // Fills that actually changed the position (exclude rejected/skipped).
  let chartFills = $derived(
    live ? toFills((live.trades ?? []).filter((t: any) => EXECUTED.has(t.status))) : []
  );
  let replayed = $derived(replay(chartFills));
  let pv = $derived(live?.point_value ?? 1);

  // Ruble equity curve from realized round-trip PnL (points × point_value).
  let equityCurve = $derived.by(() => {
    const rts = replayed.roundTrips;
    if (!chartFills.length) return [];
    const pts: any[] = [{ time: chartFills[0].time, equity: INITIAL_EQUITY }];
    let cum = 0;
    for (const r of rts) { cum += r.pnl; pts.push({ time: r.tOut, equity: INITIAL_EQUITY + cum * pv }); }
    return pts;
  });

  // Synthetic "result" so BacktestChart renders candles + markers + connectors + equity.
  let chartResult = $derived(
    live ? { trades: chartFills, equity_curve: equityCurve, params: live.robot?.params_json ?? {} } : null
  );

  // Current result summary (rubles).
  let summary = $derived.by(() => {
    const s = computeStats(chartFills, replayed.roundTrips, equityCurve);
    let signed = 0;
    for (const f of chartFills) signed += f.side === 'buy' ? f.qty : -f.qty;
    const wins = replayed.roundTrips.filter(r => r.pnl > 0).length;
    const net = equityCurve.length ? equityCurve[equityCurve.length - 1].equity - equityCurve[0].equity : 0;
    return {
      net,
      retPct: (net / INITIAL_EQUITY) * 100,
      position: signed,
      roundTrips: s.roundTrips,
      longRT: s.longRT,
      shortRT: s.shortRT,
      winRate: s.roundTrips ? (wins / s.roundTrips) * 100 : 0,
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
        <div class="chart-wrap">
          <BacktestChart
            result={chartResult}
            symbol={live.symbol}
            dateFrom={live.date_from}
            dateTo={live.date_to}
            pointValue={pv}
          />
        </div>

        <!-- bottom: params + current result (left) | trade history (right) -->
        <div class="win-bottom">
          <div class="panel left">
            <div class="panel-title">Параметры</div>
            <div class="kv-grid">
              {#each Object.entries(live.robot?.params_json ?? {}) as [k, v]}
                <div class="kv"><span class="k">{k}</span><span class="v">{v}</span></div>
              {/each}
              <div class="kv"><span class="k">ГО / контракт</span>
                <span class="v">{live.initial_margin != null ? Math.round(live.initial_margin).toLocaleString('ru-RU') + ' ₽' : '—'}</span></div>
              <div class="kv"><span class="k">пункт = ₽</span><span class="v">{pv}</span></div>
            </div>

            <div class="panel-title res-title">Текущий результат</div>
            <div class="result-grid">
              <div class="r-row"><span>Доход</span>
                <b class:pos={summary.net > 0} class:neg={summary.net < 0}>{fmtMoney(summary.net)} ₽</b>
                <span class="sub">({summary.retPct >= 0 ? '+' : ''}{summary.retPct.toFixed(2)}%)</span></div>
              <div class="r-row"><span>Позиция сейчас</span>
                <b class:pos={summary.position > 0} class:neg={summary.position < 0}>
                  {summary.position > 0 ? '+' : ''}{summary.position} конт.</b></div>
              <div class="r-row"><span>Сделок (круг)</span><b>{summary.roundTrips}</b>
                <span class="sub">L {summary.longRT} / S {summary.shortRT}</span></div>
              <div class="r-row"><span>Win rate</span><b>{summary.winRate.toFixed(0)}%</b></div>
              <div class="r-row"><span>Всего заявок</span><b>{summary.orders}</b></div>
            </div>
            <div class="basis">Доход от первоначальных инвестиций {INITIAL_EQUITY.toLocaleString('ru-RU')} ₽</div>
          </div>

          <div class="panel right">
            <div class="panel-title">История сделок ({(live.trades ?? []).length})</div>
            <div class="history-scroll">
              {#if (live.trades ?? []).length === 0}
                <div class="empty">Сделок пока нет. Робот ждёт сигнала.</div>
              {:else}
                <table>
                  <thead>
                    <tr><th>Время (МСК)</th><th>Сторона</th><th>Кол-во</th><th>Цена</th><th>Статус</th></tr>
                  </thead>
                  <tbody>
                    {#each [...live.trades].reverse() as t}
                      <tr class:rejected={t.status === 'rejected' || t.status === 'skipped'}>
                        <td class="mono">{fmtTime(t.iso)}</td>
                        <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>
                          {t.side === 'buy' ? '▲ buy' : '▼ sell'}</td>
                        <td class="mono">{t.qty}</td>
                        <td class="mono">{Math.round(t.price).toLocaleString('ru-RU')}</td>
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
  .chart-wrap { flex: 1 1 62%; min-height: 0; border-bottom: 1px solid #1a1a2e; }

  .win-bottom { flex: 0 0 34%; display: flex; min-height: 0; }
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
  .kv .k { font-size: 11px; color: #888; }
  .kv .v { font-size: 11px; color: #4caf50; font-family: monospace; }

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
  .st-badge { font-size: 9px; padding: 1px 6px; border-radius: 2px; background: #1a1a2e; color: #888; }
  .st-paper { background: #1a2a1a; color: #4caf50; }
  .st-rejected, .st-skipped { background: #2a1414; color: #ff6b6b; }
  .st-submitted, .st-filled, .st-executed { background: #14222a; color: #6aa8ff; }
  .empty { padding: 24px; text-align: center; color: #555; font-size: 12px; }
</style>
