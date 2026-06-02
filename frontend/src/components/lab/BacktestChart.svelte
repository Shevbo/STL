<!-- BacktestChart.svelte
     - candles + per-candle aggregated trade markers
     - dashed open→close connectors: green = long, red = short
     - top-right stats overlay (incl. ГО from instrument meta)
     - bottom "График доходности робота" (baseline equity), time-locked to candles
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills, replay, computeStats, priceMarkers, buildConnectors } from '../../lib/lab-analytics';

  let {
    result, symbol, strategy = null, dateFrom, dateTo, pointValue = 1, defaultInterval = 60,
  }: {
    result: any; symbol: string; strategy?: any; dateFrom: string; dateTo: string;
    pointValue?: number; defaultInterval?: number;
  } = $props();

  let containerEl: HTMLDivElement;
  let candleEl: HTMLDivElement;
  let equityEl: HTMLDivElement;

  let tvCandle: any = null, tvEquity: any = null;
  let candleSeries: any = null, volumeSeries: any = null;
  let longSeries: any = null, shortSeries: any = null, equitySeries: any = null;
  let buyMarkSeries: any = null, sellMarkSeries: any = null;  // hidden price anchors for trade triangles
  let syncing = false, syncReady = false;

  let loading = $state(true);
  let error = $state('');
  let stats = $state<any>(null);
  let crossLabel = $state('');
  let resampleMin = $state(defaultInterval);
  let margin = $state<number | null>(null);  // initial margin per contract (₽)

  const INTERVALS = [
    { label: '1м', v: 1 }, { label: '5м', v: 5 }, { label: '15м', v: 15 },
    { label: '30м', v: 30 }, { label: '1ч', v: 60 }, { label: '2ч', v: 120 },
    { label: '4ч', v: 240 }, { label: '12ч', v: 720 }, { label: '1д', v: 1440 },
  ];
  function pickInterval(v: number) { if (v !== resampleMin) { resampleMin = v; loadData(); } }

  let params = $derived(
    typeof result?.params === 'object' ? result.params
      : (typeof result?.params === 'string' ? JSON.parse(result.params) : {})
  );

  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });

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
      grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 2 },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d2d4a', minimumWidth: 84 },
    };

    // Top chart: hide its time axis — the single shared axis lives on the equity chart below.
    tvCandle = createChart(candleEl, {
      ...chartOpts,
      timeScale: { ...chartOpts.timeScale, visible: false },
      width: candleEl.clientWidth || 600, height: candleEl.clientHeight || 280,
    });
    candleSeries = tvCandle.addCandlestickSeries({
      upColor: '#26a65b', downColor: '#c0392b',
      borderUpColor: '#26a65b', borderDownColor: '#c0392b',
      wickUpColor: '#26a65b', wickDownColor: '#c0392b',
    });
    volumeSeries = tvCandle.addHistogramSeries({ priceScaleId: 'vol', color: '#4caf5020', priceFormat: { type: 'volume' } });
    tvCandle.priceScale('vol').applyOptions({ scaleMargins: { top: 0.88, bottom: 0 } });

    // dashed open→close connectors (separate series per direction = per-color)
    longSeries = tvCandle.addLineSeries({
      color: '#4caf50', lineWidth: 1, lineStyle: LineStyle.Dashed,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    shortSeries = tvCandle.addLineSeries({
      color: '#f44336', lineWidth: 1, lineStyle: LineStyle.Dashed,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });

    // Invisible anchor series carrying trade prices. `inBar` markers attached to
    // these render exactly at the fill price (QUIK-style point), not below/above
    // the candle. lineWidth/visible 0 so only the triangles show.
    const markAnchor = {
      lineVisible: false, pointMarkersVisible: false,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    };
    buyMarkSeries = tvCandle.addLineSeries(markAnchor);
    sellMarkSeries = tvCandle.addLineSeries(markAnchor);

    tvEquity = createChart(equityEl, { ...chartOpts, width: equityEl.clientWidth || 600, height: equityEl.clientHeight || 150 });
    equitySeries = tvEquity.addBaselineSeries({
      baseValue: { type: 'price', price: 100000 },
      topLineColor: '#4caf50', topFillColor1: '#4caf5040', topFillColor2: '#4caf5008',
      bottomLineColor: '#f44336', bottomFillColor1: '#f4433608', bottomFillColor2: '#f4433640',
      lineWidth: 1, priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });

    // Bar epochs carry Moscow wall-clock already (ISS times stamped as UTC by the
    // loader), so the axis renders correct MSK numbers in UTC. Format the crosshair
    // in UTC too — using Europe/Moscow here would add +3h and desync from the axis.
    const fmtFull = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', {
      timeZone: 'UTC', day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit',
    });
    const onCross = (p: any) => { crossLabel = (p && p.time) ? fmtFull(p.time) : ''; };
    tvCandle.subscribeCrosshairMove(onCross);
    tvEquity.subscribeCrosshairMove(onCross);

    // lock both time axes together by TIME range
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

    await loadMeta();
    await loadData();
  });

  onDestroy(() => { tvCandle?.remove(); tvEquity?.remove(); });

  async function loadData() {
    loading = true; error = ''; syncReady = false;
    try {
      const res = await fetchWithAuth(
        `/api/v1/market/bars?symbol=${encodeURIComponent(symbol)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&resample_min=${resampleMin}`
      );
      if (!res.ok) throw new Error(await res.text());
      const bars: any[] = await res.json();
      if (!bars.length) { error = `Нет данных для ${symbol}. Загрузите через "Load from ISS".`; loading = false; return; }

      candleSeries.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
      volumeSeries.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? '#26a65b30' : '#c0392b30' })));

      const fills = toFills(result?.trades);
      const { roundTrips } = replay(fills);

      // one triangle per fill, placed at the exact trade price via hidden anchor
      // series (buy = green ▲, sell = red ▼). Bucketed to candle time.
      const pm = priceMarkers(fills, resampleMin * 60);
      buyMarkSeries.setData(pm.buy.points);
      sellMarkSeries.setData(pm.sell.points);
      buyMarkSeries.setMarkers(pm.buy.markers);
      sellMarkSeries.setMarkers(pm.sell.markers);

      // dashed open→close connectors
      longSeries.setData(buildConnectors(roundTrips, 'long'));
      shortSeries.setData(buildConnectors(roundTrips, 'short'));

      // equity baseline
      const eq: any[] = Array.isArray(result?.equity_curve)
        ? result.equity_curve
        : (typeof result?.equity_curve === 'string' ? JSON.parse(result.equity_curve) : []);
      if (eq.length) {
        equitySeries.applyOptions({ baseValue: { type: 'price', price: eq[0].equity } });
        equitySeries.setData(eq.map(p => ({ time: p.time, value: p.equity })));
      }

      stats = computeStats(fills, roundTrips, eq);
      // Per-round-trip stats come back in index points. For a live robot we get a
      // point_value so the overlay reads in rubles; backtests pass 1 (unchanged).
      if (pointValue !== 1 && stats) {
        stats = {
          ...stats,
          avgPerTrade: stats.avgPerTrade * pointValue,
          maxProfit: stats.maxProfit * pointValue,
          maxLoss: stats.maxLoss * pointValue,
        };
      }

      // Lock BOTH charts to the SAME time window = candle span. Prevents the
      // equity chart from "detaching" when the interval/zoom changes.
      const tFrom = bars[0].time, tTo = bars[bars.length - 1].time;
      syncing = true;
      try {
        tvCandle.timeScale().setVisibleRange({ from: tFrom, to: tTo });
        tvEquity.timeScale().setVisibleRange({ from: tFrom, to: tTo });
      } finally { syncing = false; }
      syncReady = true;
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  $effect(() => { if (result && candleSeries) loadData(); });
</script>

<div class="bt-root" bind:this={containerEl}>
  <div class="bt-header">
    <span class="bt-symbol">{symbol}</span>
    {#if strategy}
      <a class="bt-strategy" href={strategy.source} target="_blank" rel="noopener">{strategy.name} ↗</a>
    {/if}
    <span class="bt-params">
      {#each Object.entries(params) as [k, v]}
        {#if k !== 'symbol'}<span class="bt-param">{k}={v}</span>{/if}
      {/each}
    </span>
    <span class="bt-legend">
      <span class="lg lg-long">— лонг</span><span class="lg lg-short">— шорт</span>
    </span>
    <div class="bt-intervals">
      {#each INTERVALS as iv}
        <button class:active={resampleMin === iv.v} onclick={() => pickInterval(iv.v)}>{iv.label}</button>
      {/each}
    </div>
    {#if crossLabel}<span class="bt-cross">{crossLabel}</span>{/if}
  </div>

  <div class="bt-candle-area">
    <div class="candle" bind:this={candleEl}></div>

    {#if stats}
      <div class="stats-overlay">
        <div class="st-row"><span>Всего сделок</span><b>{stats.roundTrips}</b>
          <span class="st-sub">(L {stats.longRT} / S {stats.shortRT})</span></div>
        <div class="st-row"><span>Макс. позиция</span><b>{stats.maxAbsPos} конт.</b>
          <span class="st-sub">ГО: {margin != null ? fmtMoney(stats.maxAbsPos * margin).replace('+','') + ' ₽' : '—'}</span></div>
        <div class="st-row"><span>Средн. на сделку</span>
          <b class:pos={stats.avgPerTrade > 0} class:neg={stats.avgPerTrade < 0}>{fmtMoney(stats.avgPerTrade)}</b></div>
        <div class="st-row"><span>Макс. прибыль</span><b class="pos">{fmtMoney(stats.maxProfit)}</b></div>
        <div class="st-row"><span>Макс. убыток</span><b class="neg">{fmtMoney(stats.maxLoss)}</b></div>
        <div class="st-row"><span>Фактор восст.</span><b>{stats.recovery != null ? stats.recovery.toFixed(2) : '—'}</b></div>
      </div>
    {/if}

    {#if loading}<div class="overlay">Загрузка…</div>{/if}
    {#if error}<div class="overlay error">{error}</div>{/if}
  </div>

  <div class="bt-equity-label">График доходности робота</div>
  <div class="equity" bind:this={equityEl}></div>
</div>

<style>
  .bt-root { display: flex; flex-direction: column; height: 100%; background: #0a0a15; }
  .bt-header {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 4px 10px; background: #0f0f1e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .bt-symbol { font-size: 13px; color: #4caf50; font-weight: 600; }
  .bt-strategy { font-size: 11px; color: #6aa8ff; text-decoration: none; }
  .bt-strategy:hover { text-decoration: underline; }
  .bt-params { display: flex; gap: 4px; flex-wrap: wrap; }
  .bt-param { font-size: 10px; font-family: monospace; color: #888; background: #1a1a2e; border-radius: 2px; padding: 1px 5px; }
  .bt-legend { display: flex; gap: 8px; font-size: 10px; }
  .lg-long { color: #4caf50; } .lg-short { color: #f44336; }
  .bt-intervals { display: flex; gap: 1px; margin-left: auto; }
  .bt-intervals button { background: transparent; color: #555; border: 1px solid transparent; font-size: 10px; padding: 1px 6px; border-radius: 3px; cursor: pointer; }
  .bt-intervals button:hover { color: #aaa; }
  .bt-intervals button.active { color: #4caf50; border-color: #4caf5066; }
  .bt-cross { font-size: 11px; font-family: monospace; color: #6aa8ff; padding-left: 8px; white-space: nowrap; }

  .bt-candle-area { position: relative; flex: 1; min-height: 0; }
  .candle { position: absolute; inset: 0; }

  .stats-overlay {
    position: absolute; top: 6px; right: 92px; z-index: 5;
    background: #0f0f1ed9; border: 1px solid #2d2d4a; border-radius: 4px;
    padding: 6px 8px; display: flex; flex-direction: column; gap: 2px;
    backdrop-filter: blur(2px); min-width: 210px;
  }
  .st-row { display: flex; align-items: baseline; gap: 6px; font-size: 10px; color: #888; }
  .st-row span:first-child { flex: 1; }
  .st-row b { color: #ccc; font-size: 11px; }
  .st-sub { color: #555; font-size: 9px; }
  .pos { color: #4caf50; } .neg { color: #f44336; }

  .bt-equity-label {
    padding: 3px 10px; font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;
    background: #0f0f1e; border-top: 1px solid #1a1a2e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .equity { flex: 0 0 24%; min-height: 0; }

  .overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: #0a0a15cc; z-index: 10; font-size: 12px; color: #666; }
  .overlay.error { color: #f4433699; }
</style>
