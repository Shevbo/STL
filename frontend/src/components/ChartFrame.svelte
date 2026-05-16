<!-- frontend/src/components/ChartFrame.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import type { OhlcBar, TradeMarker, BacktestResult } from '$lib/types';

  let { robotName, symbol, ohlc = [], markers = [], backtest = null }: {
    robotName: string;
    symbol: string;
    ohlc?: OhlcBar[];
    markers?: TradeMarker[];
    backtest?: BacktestResult | null;
  } = $props();

  let tickEl: HTMLDivElement;
  let ohlcEl: HTMLDivElement;

  const MAX_TICKS = 500;
  let tickTimes: number[] = [];
  let tickBids: (number | null)[] = [];
  let tickAsks: (number | null)[] = [];

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let uplotInst: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvChart: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvCandle: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvEquity: any = null;

  let quote = $derived(quotesStore.get(symbol));

  $effect(() => {
    if (!quote || !uplotInst) return;
    const t = Math.floor(Date.parse(quote.timestamp) / 1000);
    if (tickTimes.length >= MAX_TICKS) {
      tickTimes.shift(); tickBids.shift(); tickAsks.shift();
    }
    tickTimes.push(t);
    tickBids.push(quote.bid);
    tickAsks.push(quote.ask);
    uplotInst.setData([tickTimes, tickBids, tickAsks]);
  });

  $effect(() => {
    if (!tvCandle || !ohlc.length) return;
    tvCandle.setData(ohlc.map((b: OhlcBar) => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    })));
  });

  $effect(() => {
    if (!tvCandle) return;
    tvCandle.setMarkers(markers);
  });

  $effect(() => {
    if (!tvEquity) return;
    if (backtest?.equityCurve.length) {
      tvEquity.setData(backtest.equityCurve);
      tvEquity.applyOptions({ visible: true });
    } else {
      tvEquity.applyOptions({ visible: false });
    }
  });

  const TICK_H = 80;

  onMount(async () => {
    const uPlotMod = await import('uplot');
    const UPlot = uPlotMod.default;

    uplotInst = new UPlot(
      {
        width: tickEl.clientWidth || 400,
        height: TICK_H,
        series: [
          {},
          { label: 'Bid', stroke: '#4caf50', width: 1 },
          { label: 'Ask', stroke: '#f44336', width: 1 },
        ],
        axes: [{ show: false }, { show: true, size: 50, gap: 0 }],
        legend: { show: false },
        padding: [4, 0, 0, 0],
      } as Parameters<typeof UPlot>[0],
      [[], [], []] as unknown[][],
      tickEl,
    );

    const { createChart, CandlestickSeries, LineSeries } = await import('lightweight-charts');
    const chartH = Math.max((ohlcEl.clientHeight || 320) - TICK_H, 100);
    tvChart = createChart(ohlcEl, {
      width: ohlcEl.clientWidth || 400,
      height: chartH,
      layout: { background: { color: '#0f0f1e' }, textColor: '#888' },
      grid: { vertLines: { color: '#1e1e3a' }, horzLines: { color: '#1e1e3a' } },
      timeScale: { borderColor: '#2d2d4a' },
      crosshair: { mode: 1 },
    });
    tvCandle = tvChart.addSeries(CandlestickSeries, {
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    });
    tvEquity = tvChart.addSeries(LineSeries, {
      color: '#3d5af1', lineWidth: 1, visible: false,
    });
  });

  onDestroy(() => {
    uplotInst?.destroy();
    tvChart?.remove();
  });
</script>

<div class="frame">
  <div class="frame-label">{robotName} · {symbol}</div>
  <div class="chart-area" bind:this={ohlcEl}>
    <div class="tick-strip" bind:this={tickEl}></div>
  </div>
</div>

<style>
  .frame {
    display: flex; flex-direction: column;
    min-height: 320px; border-bottom: 1px solid #2d2d4a;
  }
  .frame-label {
    padding: 3px 8px; font-size: 11px; color: #666;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .chart-area { flex: 1; position: relative; overflow: hidden; }
  .tick-strip { position: absolute; bottom: 0; left: 0; right: 0; z-index: 1; }
</style>
