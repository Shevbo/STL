<!-- BacktestChart.svelte
     Two stacked lightweight-charts panels:
       top    — instrument candles + trade markers (buy ▲ / sell ▼)
       bottom — equity curve (area)
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let {
    result,          // backtest_result row: {params, trades, equity_curve, ...}
    symbol,
    dateFrom,
    dateTo,
  }: {
    result: any;
    symbol: string;
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
  let loading = $state(true);
  let error = $state('');

  // ── chart init ─────────────────────────────────────────────────────
  onMount(async () => {
    const { createChart } = await import('lightweight-charts');

    const chartOpts = {
      layout: { background: { color: '#0a0a15' }, textColor: '#666' },
      grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 5 },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d2d4a' },
    };

    // ── candle chart ──────────────────────────────────────────────────
    tvCandle = createChart(candleEl, {
      ...chartOpts,
      width: candleEl.clientWidth || 600,
      height: candleEl.clientHeight || 280,
    });
    tvCandle.timeScale().applyOptions({ timeVisible: true } as any);

    candleSeries = tvCandle.addCandlestickSeries({
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    });

    volumeSeries = tvCandle.addHistogramSeries({
      priceScaleId: 'vol',
      color: '#4caf5030',
      priceFormat: { type: 'volume' },
    });
    tvCandle.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    // ── equity chart ──────────────────────────────────────────────────
    tvEquity = createChart(equityEl, {
      ...chartOpts,
      width: equityEl.clientWidth || 600,
      height: equityEl.clientHeight || 160,
    });
    tvEquity.timeScale().applyOptions({ timeVisible: true } as any);

    equitySeries = tvEquity.addAreaSeries({
      lineColor: '#4caf50',
      topColor: '#4caf5030',
      bottomColor: '#4caf5000',
      lineWidth: 1,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });

    // ── resize ────────────────────────────────────────────────────────
    const ro = new ResizeObserver(() => {
      tvCandle?.applyOptions({ width: candleEl.clientWidth, height: candleEl.clientHeight });
      tvEquity?.applyOptions({ width: equityEl.clientWidth, height: equityEl.clientHeight });
    });
    ro.observe(containerEl);

    await loadData();
  });

  onDestroy(() => {
    tvCandle?.remove();
    tvEquity?.remove();
  });

  // ── data loading ─────────────────────────────────────────────────────
  async function loadData() {
    loading = true; error = '';
    try {
      // Determine best resample: 60min for > 30 days, 5min for shorter
      const daySpan = (new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000;
      const resample = daySpan > 30 ? 60 : 5;

      const res = await fetchWithAuth(
        `/api/v1/market/bars?symbol=${encodeURIComponent(symbol)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&resample_min=${resample}`
      );
      if (!res.ok) throw new Error(await res.text());
      const bars: any[] = await res.json();

      if (!bars.length) {
        error = `No cached bars for ${symbol}. Load data first in "Market Data" section.`;
        loading = false;
        return;
      }

      // ── set candle data ─────────────────────────────────────────────
      candleSeries.setData(bars.map(b => ({
        time: b.time,
        open: b.open, high: b.high, low: b.low, close: b.close,
      })));
      volumeSeries.setData(bars.map(b => ({
        time: b.time, value: b.volume,
        color: b.close >= b.open ? '#4caf5030' : '#f4433630',
      })));

      // ── trade markers ───────────────────────────────────────────────
      const trades: any[] = Array.isArray(result?.trades)
        ? result.trades
        : (typeof result?.trades === 'string' ? JSON.parse(result.trades) : []);

      if (trades.length) {
        const markers = trades
          .filter((t: any) => t.time)
          .map((t: any) => ({
            time: t.time as number,
            position: (t.side === 'buy' ? 'belowBar' : 'aboveBar') as any,
            color: t.side === 'buy' ? '#4caf50' : '#f44336',
            shape: (t.side === 'buy' ? 'arrowUp' : 'arrowDown') as any,
            text: `${t.side === 'buy' ? '▲' : '▼'} ${Math.round(t.price)}`,
            size: 1,
          }))
          .sort((a: any, b: any) => (a.time as number) - (b.time as number));
        candleSeries.setMarkers(markers);
      }

      tvCandle.timeScale().fitContent();

      // ── equity curve ────────────────────────────────────────────────
      const eq: any[] = Array.isArray(result?.equity_curve)
        ? result.equity_curve
        : (typeof result?.equity_curve === 'string' ? JSON.parse(result.equity_curve) : []);

      if (eq.length) {
        equitySeries.setData(eq.map((p: any) => ({
          time: p.time,
          value: p.equity,
        })));
        tvEquity.timeScale().fitContent();
        // Sync time scales
        tvCandle.timeScale().subscribeVisibleTimeRangeChange((range: any) => {
          if (range) tvEquity.timeScale().setVisibleRange(range);
        });
        tvEquity.timeScale().subscribeVisibleTimeRangeChange((range: any) => {
          if (range) tvCandle.timeScale().setVisibleRange(range);
        });
      }

    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // Reload when result changes
  $effect(() => {
    if (result && candleSeries) loadData();
  });
</script>

<div class="backtest-chart" bind:this={containerEl}>
  {#if loading}
    <div class="overlay">Загружаем данные...</div>
  {:else if error}
    <div class="overlay error">{error}</div>
  {/if}

  <!-- Instrument + trade markers -->
  <div class="chart-label">
    {symbol}
    {#if result}
      <span class="label-stats">
        Trades: {result.total_trades ?? '?'} ·
        Win: {result.win_rate != null ? (result.win_rate * 100).toFixed(1) + '%' : '—'} ·
        Return: <span class:pos={result.total_return > 0} class:neg={result.total_return < 0}>
          {result.total_return != null ? (result.total_return * 100).toFixed(2) + '%' : '—'}
        </span>
        · Sharpe: {result.sharpe != null ? result.sharpe.toFixed(3) : '—'}
        · MaxDD: {result.max_drawdown != null ? (result.max_drawdown * 100).toFixed(2) + '%' : '—'}
      </span>
    {/if}
  </div>
  <div class="chart-panel candle-panel" bind:this={candleEl}></div>

  <!-- Equity curve -->
  <div class="chart-label equity-label">Equity Curve</div>
  <div class="chart-panel equity-panel" bind:this={equityEl}></div>
</div>

<style>
  .backtest-chart {
    display: flex; flex-direction: column; height: 100%;
    background: #0a0a15; position: relative;
  }
  .chart-label {
    padding: 3px 10px; font-size: 11px; color: #555;
    background: #0f0f1e; border-bottom: 1px solid #1a1a2e;
    flex-shrink: 0; display: flex; align-items: center; gap: 8px;
  }
  .equity-label { border-top: 1px solid #1a1a2e; }
  .label-stats { font-size: 10px; color: #666; }
  .label-stats .pos { color: #4caf50; }
  .label-stats .neg { color: #f44336; }
  .chart-panel { flex: 1; min-height: 0; }
  .candle-panel { flex: 3; }
  .equity-panel { flex: 1.4; }
  .overlay {
    position: absolute; inset: 0; display: flex;
    align-items: center; justify-content: center;
    background: #0a0a15cc; z-index: 10;
    font-size: 12px; color: #666;
  }
  .overlay.error { color: #f4433699; }
</style>
