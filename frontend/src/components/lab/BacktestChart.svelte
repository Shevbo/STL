<!-- BacktestChart.svelte
     Layout:
       - top bar: instrument + strategy author link + params
       - candle chart (fit to whole period) with:
           * trade markers (buy ▲ / sell ▼)
           * top-right stats overlay
           * bottom-left virtual-trades table overlay
       - bottom: "График доходности робота" (equity), time-synced with candles
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let {
    result,          // backtest_result row: {params, trades, equity_curve, ...}
    symbol,
    strategy = null, // {name, source, params_schema}
    dateFrom,
    dateTo,
  }: {
    result: any;
    symbol: string;
    strategy?: any;
    dateFrom: string;
    dateTo: string;
  } = $props();

  let containerEl: HTMLDivElement;
  let candleEl: HTMLDivElement;
  let equityEl: HTMLDivElement;

  let tvCandle: any = null;
  let tvEquity: any = null;
  let candleSeries: any = null;
  let volumeSeries: any = null;
  let equitySeries: any = null;
  let syncing = false;
  let syncReady = false;   // enable cross-chart sync only after both have data

  let loading = $state(true);
  let error = $state('');
  let stats = $state<any>(null);
  let ledger = $state<any[]>([]);

  // params as object
  let params = $derived(
    typeof result?.params === 'object' ? result.params
      : (typeof result?.params === 'string' ? JSON.parse(result.params) : {})
  );

  // ── analytics: replay fills → trade types, per-close PnL, aggregates ──
  function analyze(trades: any[], equity: any[]) {
    let pos = 0;       // signed contracts
    let avg = 0;       // avg entry price
    let maxAbsPos = 0;
    const rows: any[] = [];
    const closePnls: number[] = [];
    let longRT = 0, shortRT = 0;

    for (const t of trades) {
      const q = Number(t.qty) || 1;
      const signed = t.side === 'buy' ? q : -q;
      let type = 'open';
      let pnl: number | null = null;

      if (pos === 0) {
        type = 'open';
        avg = t.price; pos = signed;
      } else if (Math.sign(pos) === Math.sign(signed)) {
        type = 'average';
        const totalCost = avg * Math.abs(pos) + t.price * q;
        pos += signed;
        avg = totalCost / Math.abs(pos);
      } else {
        // opposite → close (maybe partial / flip)
        const dir = Math.sign(pos);       // +1 long being closed by sell
        const closeQty = Math.min(Math.abs(pos), q);
        pnl = dir > 0 ? (t.price - avg) * closeQty : (avg - t.price) * closeQty;
        closePnls.push(pnl);
        if (dir > 0) longRT++; else shortRT++;
        const leftover = q - closeQty;
        if (leftover > 0) { type = 'reverse'; pos = -dir * leftover; avg = t.price; }
        else { type = 'close'; pos += signed; if (pos === 0) avg = 0; }
      }
      maxAbsPos = Math.max(maxAbsPos, Math.abs(pos));
      rows.push({ time: t.time, side: t.side, qty: q, price: t.price, type, pnl });
    }

    // money metrics from equity curve
    let netProfit = 0, maxDDmoney = 0;
    if (equity.length) {
      netProfit = equity[equity.length - 1].equity - equity[0].equity;
      let peak = -Infinity;
      for (const p of equity) {
        if (p.equity > peak) peak = p.equity;
        const dd = peak - p.equity;
        if (dd > maxDDmoney) maxDDmoney = dd;
      }
    }
    const rt = closePnls.length;
    const sum = closePnls.reduce((a, b) => a + b, 0);
    return {
      fills: trades.length,
      roundTrips: rt,
      longRT, shortRT,
      maxAbsPos,
      avgPerTrade: rt ? sum / rt : 0,
      maxProfit: rt ? Math.max(...closePnls) : 0,
      maxLoss: rt ? Math.min(...closePnls) : 0,
      netProfit,
      maxDDmoney,
      recovery: maxDDmoney > 0 ? netProfit / maxDDmoney : null,
    };
  }

  const fmtTime = (ts: number) =>
    new Date(ts * 1000).toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow',
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  const TYPE_LABEL: Record<string, string> = {
    open: 'Открытие', average: 'Усреднение', close: 'Закрытие', reverse: 'Закрытие+Реверс',
  };
  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });

  // ── chart init ─────────────────────────────────────────────────────
  onMount(async () => {
    const { createChart } = await import('lightweight-charts');
    const chartOpts = {
      layout: { background: { color: '#0a0a15' }, textColor: '#666' },
      grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 2 },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d2d4a' },
    };

    tvCandle = createChart(candleEl, {
      ...chartOpts,
      width: candleEl.clientWidth || 600,
      height: candleEl.clientHeight || 280,
    });
    candleSeries = tvCandle.addCandlestickSeries({
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    });
    volumeSeries = tvCandle.addHistogramSeries({
      priceScaleId: 'vol', color: '#4caf5030', priceFormat: { type: 'volume' },
    });
    tvCandle.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    tvEquity = createChart(equityEl, {
      ...chartOpts,
      width: equityEl.clientWidth || 600,
      height: equityEl.clientHeight || 160,
    });
    equitySeries = tvEquity.addAreaSeries({
      lineColor: '#4caf50', topColor: '#4caf5030', bottomColor: '#4caf5000',
      lineWidth: 1, priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });

    // time-scale sync by TIME range (series have different point densities:
    // candles are hourly-resampled, equity is per-minute, so logical-index sync misaligns).
    const link = (from: any, to: any) =>
      from.timeScale().subscribeVisibleTimeRangeChange((r: any) => {
        if (!syncReady || syncing || !r || r.from == null || r.to == null) return;
        syncing = true;
        try { to.timeScale().setVisibleRange(r); } catch { /* transient */ } finally { syncing = false; }
      });
    link(tvCandle, tvEquity);
    link(tvEquity, tvCandle);

    const ro = new ResizeObserver(() => {
      tvCandle?.applyOptions({ width: candleEl.clientWidth, height: candleEl.clientHeight });
      tvEquity?.applyOptions({ width: equityEl.clientWidth, height: equityEl.clientHeight });
    });
    ro.observe(containerEl);

    await loadData();
  });

  onDestroy(() => { tvCandle?.remove(); tvEquity?.remove(); });

  // ── data loading ─────────────────────────────────────────────────────
  async function loadData() {
    loading = true; error = ''; syncReady = false;
    try {
      const daySpan = (new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000;
      const resample = daySpan > 30 ? 60 : 5;

      const res = await fetchWithAuth(
        `/api/v1/market/bars?symbol=${encodeURIComponent(symbol)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&resample_min=${resample}`
      );
      if (!res.ok) throw new Error(await res.text());
      const bars: any[] = await res.json();
      if (!bars.length) {
        error = `Нет данных для ${symbol}. Загрузите через "Load from ISS".`;
        loading = false; return;
      }

      candleSeries.setData(bars.map(b => ({
        time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
      })));
      volumeSeries.setData(bars.map(b => ({
        time: b.time, value: b.volume,
        color: b.close >= b.open ? '#4caf5030' : '#f4433630',
      })));

      const trades: any[] = Array.isArray(result?.trades)
        ? result.trades
        : (typeof result?.trades === 'string' ? JSON.parse(result.trades) : []);

      // markers
      const markers = trades.filter(t => t.time).map(t => ({
        time: t.time as number,
        position: (t.side === 'buy' ? 'belowBar' : 'aboveBar') as any,
        color: t.side === 'buy' ? '#4caf50' : '#f44336',
        shape: (t.side === 'buy' ? 'arrowUp' : 'arrowDown') as any,
        text: `${t.side === 'buy' ? '▲' : '▼'} ${Math.round(t.price)}`,
        size: 1,
      })).sort((a, b) => (a.time as number) - (b.time as number));
      candleSeries.setMarkers(markers);

      // equity
      const eq: any[] = Array.isArray(result?.equity_curve)
        ? result.equity_curve
        : (typeof result?.equity_curve === 'string' ? JSON.parse(result.equity_curve) : []);
      if (eq.length) {
        equitySeries.setData(eq.map(p => ({ time: p.time, value: p.equity })));
      }

      // analytics for stats + table
      stats = analyze(trades, eq);
      ledger = buildLedger(trades);

      // fit whole period on both (each shows its full data range)
      tvCandle.timeScale().fitContent();
      tvEquity.timeScale().fitContent();
      syncReady = true;   // enable pan/zoom sync now that both have data
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // rebuild enriched rows (same logic as analyze, returns rows)
  function buildLedger(trades: any[]) {
    let pos = 0, avg = 0;
    const rows: any[] = [];
    for (const t of trades) {
      const q = Number(t.qty) || 1;
      const signed = t.side === 'buy' ? q : -q;
      let type = 'open'; let pnl: number | null = null;
      if (pos === 0) { type = 'open'; avg = t.price; pos = signed; }
      else if (Math.sign(pos) === Math.sign(signed)) {
        type = 'average';
        const c = avg * Math.abs(pos) + t.price * q; pos += signed; avg = c / Math.abs(pos);
      } else {
        const dir = Math.sign(pos);
        const closeQty = Math.min(Math.abs(pos), q);
        pnl = dir > 0 ? (t.price - avg) * closeQty : (avg - t.price) * closeQty;
        const leftover = q - closeQty;
        if (leftover > 0) { type = 'reverse'; pos = -dir * leftover; avg = t.price; }
        else { type = 'close'; pos += signed; if (pos === 0) avg = 0; }
      }
      rows.push({ time: t.time, side: t.side, qty: q, price: t.price, type, pnl });
    }
    return rows;
  }

  $effect(() => { if (result && candleSeries) loadData(); });
</script>

<div class="bt-root" bind:this={containerEl}>
  <!-- top bar: instrument + strategy link + params -->
  <div class="bt-header">
    <span class="bt-symbol">{symbol}</span>
    {#if strategy}
      <a class="bt-strategy" href={strategy.source} target="_blank" rel="noopener">
        {strategy.name} ↗
      </a>
    {/if}
    <span class="bt-params">
      {#each Object.entries(params) as [k, v]}
        {#if k !== 'symbol'}<span class="bt-param">{k}={v}</span>{/if}
      {/each}
    </span>
  </div>

  <!-- candle area with overlays -->
  <div class="bt-candle-area">
    <div class="candle" bind:this={candleEl}></div>

    <!-- top-right stats -->
    {#if stats}
      <div class="stats-overlay">
        <div class="st-row"><span>Всего сделок</span><b>{stats.roundTrips}</b>
          <span class="st-sub">(L {stats.longRT} / S {stats.shortRT})</span></div>
        <div class="st-row"><span>Макс. позиция</span><b>{stats.maxAbsPos} конт.</b>
          <span class="st-sub">ГО: —</span></div>
        <div class="st-row"><span>Средн. на сделку</span>
          <b class:pos={stats.avgPerTrade > 0} class:neg={stats.avgPerTrade < 0}>{fmtMoney(stats.avgPerTrade)} ₽</b></div>
        <div class="st-row"><span>Макс. прибыль</span><b class="pos">{fmtMoney(stats.maxProfit)} ₽</b></div>
        <div class="st-row"><span>Макс. убыток</span><b class="neg">{fmtMoney(stats.maxLoss)} ₽</b></div>
        <div class="st-row"><span>Фактор восст.</span>
          <b>{stats.recovery != null ? stats.recovery.toFixed(2) : '—'}</b></div>
      </div>
    {/if}

    <!-- bottom-left virtual trades -->
    {#if ledger.length}
      <div class="trades-overlay">
        <div class="to-title">Виртуальные сделки ({ledger.length})</div>
        <div class="to-scroll">
          <table>
            <thead>
              <tr><th>Дата-время</th><th>Инстр.</th><th>Напр.</th><th>Кол</th><th>Цена</th><th>Тип</th><th>Фин. рез.</th></tr>
            </thead>
            <tbody>
              {#each ledger as r}
                <tr>
                  <td>{fmtTime(r.time)}</td>
                  <td>{symbol}</td>
                  <td class:buy={r.side === 'buy'} class:sell={r.side === 'sell'}>
                    {r.side === 'buy' ? 'Купить' : 'Продать'}</td>
                  <td>{r.qty}</td>
                  <td>{Math.round(r.price)}</td>
                  <td class="ttype">{TYPE_LABEL[r.type] ?? r.type}</td>
                  <td class:pos={r.pnl > 0} class:neg={r.pnl < 0}>
                    {r.pnl != null ? fmtMoney(r.pnl) : ''}</td>
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

  <!-- equity -->
  <div class="bt-equity-label">График доходности робота</div>
  <div class="equity" bind:this={equityEl}></div>
</div>

<style>
  .bt-root { display: flex; flex-direction: column; height: 100%; background: #0a0a15; }
  .bt-header {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 4px 10px; background: #0f0f1e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .bt-symbol { font-size: 13px; color: #4caf50; font-weight: 600; }
  .bt-strategy { font-size: 11px; color: #6aa8ff; text-decoration: none; }
  .bt-strategy:hover { text-decoration: underline; }
  .bt-params { display: flex; gap: 4px; flex-wrap: wrap; }
  .bt-param {
    font-size: 10px; font-family: monospace; color: #888;
    background: #1a1a2e; border-radius: 2px; padding: 1px 5px;
  }

  .bt-candle-area { position: relative; flex: 1; min-height: 0; }
  .candle { position: absolute; inset: 0; }

  .stats-overlay {
    position: absolute; top: 6px; right: 60px; z-index: 5;
    background: #0f0f1ecc; border: 1px solid #2d2d4a; border-radius: 4px;
    padding: 6px 8px; display: flex; flex-direction: column; gap: 2px;
    backdrop-filter: blur(2px); min-width: 200px;
  }
  .st-row { display: flex; align-items: baseline; gap: 6px; font-size: 10px; color: #888; }
  .st-row span:first-child { flex: 1; }
  .st-row b { color: #ccc; font-size: 11px; }
  .st-sub { color: #555; font-size: 9px; }

  .trades-overlay {
    position: absolute; left: 6px; bottom: 6px; z-index: 5;
    width: 480px; max-width: 60%; max-height: 55%;
    background: #0a0a15ee; border: 1px solid #2d2d4a; border-radius: 4px;
    display: flex; flex-direction: column; backdrop-filter: blur(2px);
  }
  .to-title {
    padding: 4px 8px; font-size: 10px; color: #666; text-transform: uppercase;
    letter-spacing: 0.5px; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .to-scroll { overflow: auto; }
  .trades-overlay table { width: 100%; border-collapse: collapse; font-size: 10px; }
  .trades-overlay th {
    position: sticky; top: 0; background: #0f0f1e; color: #555;
    text-align: left; padding: 3px 6px; white-space: nowrap; border-bottom: 1px solid #1a1a2e;
  }
  .trades-overlay td { padding: 2px 6px; border-bottom: 1px solid #12121c; color: #aaa; white-space: nowrap; }
  .trades-overlay td.buy { color: #4caf50; }
  .trades-overlay td.sell { color: #f44336; }
  .ttype { color: #888; }
  .pos { color: #4caf50; }
  .neg { color: #f44336; }

  .bt-equity-label {
    padding: 3px 10px; font-size: 10px; color: #666; text-transform: uppercase;
    letter-spacing: 0.5px; background: #0f0f1e; border-top: 1px solid #1a1a2e;
    border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .equity { flex: 0 0 26%; min-height: 0; }

  .overlay {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    background: #0a0a15cc; z-index: 10; font-size: 12px; color: #666;
  }
  .overlay.error { color: #f4433699; }
</style>
