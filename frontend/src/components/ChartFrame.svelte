<!-- frontend/src/components/ChartFrame.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { candlesStore } from '$lib/stores/candles.svelte';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import { ordersStore } from '$lib/stores/orders.svelte';
  import { tradesStore } from '$lib/stores/trades.svelte';
  import { instrumentStore } from '$lib/stores/instrument.svelte';
  import { orderbookStore } from '$lib/stores/orderbook.svelte';

  let {
    symbol,
    onSubscribe,
  }: {
    symbol: string;
    onSubscribe?: (symbol: string, timeframe: number) => void;
  } = $props();

  // Timeframes available from Finam API
  const TIMEFRAMES: { label: string; value: number }[] = [
    { label: '1м', value: 1 },
    { label: '5м', value: 5 },
    { label: '15м', value: 9 },
    { label: '30м', value: 11 },
    { label: '1ч', value: 12 },
    { label: '2ч', value: 13 },
    { label: '4ч', value: 15 },
    { label: 'Д', value: 19 },
  ];

  let selectedTf = $state(5);
  // User-chosen symbol override; null means "follow the prop"
  let symbolOverride = $state<string | null>(null);
  let selectedSymbol = $derived(symbolOverride ?? symbol);

  let tickEl: HTMLDivElement;
  let ohlcEl: HTMLDivElement;
  let ohlcAreaEl: HTMLDivElement;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let uplotInst: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvChart: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvCandle: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvVolume: any = null;
  // price lines map: order_id → priceLine
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let orderLines = new Map<string, any>();

  // Store visible range for preserving zoom on timeframe change
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let savedVisibleRange: any = null;

  let ohlc = $derived.by(() => {
    return candlesStore.get(selectedSymbol);
  });
  let quote = $derived(quotesStore.get(selectedSymbol));
  let orders = $derived(ordersStore.forSymbol(selectedSymbol));
  let trades = $derived(tradesStore.forSymbol(selectedSymbol));
  let instruments = $derived(instrumentStore.list);

  const MAX_TICKS = 500;
  let tickTimes: number[] = [];
  let tickBids: (number | null)[] = [];
  let tickAsks: (number | null)[] = [];

  // Task 1: live tick chart + update last candle close from quote
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

  // Task 1: update last candle close price from live quote
  $effect(() => {
    if (!tvCandle || !quote || !ohlc.length) return;
    const last = ohlc[ohlc.length - 1];
    const price = quote.last || quote.bid || 0;
    if (!price) return;
    tvCandle.update({
      time: last.time as number,
      open: last.open as number,
      high: Math.max(last.high as number, price),
      low: Math.min(last.low as number, price),
      close: price,
    });
  });

  let lastOhlcLen = 0;

  // Task 2: sync OHLC + volume series
  $effect(() => {
    console.log('[Chart] $effect fired: tvCandle=', !!tvCandle, 'ohlc.length=', ohlc.length, 'lastOhlcLen=', lastOhlcLen);
    if (!tvCandle || !ohlc.length) {
      console.log('[Chart] Early return: tvCandle=', !!tvCandle, 'ohlc.length=', ohlc.length);
      return;
    }
    const bars = ohlc.map(b => ({
      time: b.time as number,
      open: b.open as number,
      high: b.high as number,
      low: b.low as number,
      close: b.close as number,
    }));
    const vols = ohlc.map(b => ({
      time: b.time as number,
      value: b.volume as number,
      color: (b.close as number) >= (b.open as number) ? '#4caf5040' : '#f4433640',
    }));
    if (ohlc.length !== lastOhlcLen) {
      console.log('[Chart] setData: bars=', bars.length, 'vols=', vols.length);
      tvCandle.setData(bars);
      tvVolume?.setData(vols);
      lastOhlcLen = ohlc.length;
      if (savedVisibleRange) {
        tvChart.timeScale().setVisibleLogicalRange(savedVisibleRange);
        savedVisibleRange = null;
      } else {
        tvChart.timeScale().fitContent();
      }
    } else {
      console.log('[Chart] update last bar only');
      tvCandle.update(bars[bars.length - 1]);
      tvVolume?.update(vols[vols.length - 1]);
    }
  });

  // Task 6: order price lines
  $effect(() => {
    if (!tvCandle) return;
    const currentIds = new Set(orders.map(o => o.order_id));
    for (const [id, line] of orderLines) {
      if (!currentIds.has(id)) {
        tvCandle.removePriceLine(line);
        orderLines.delete(id);
      }
    }
    for (const order of orders) {
      if (orderLines.has(order.order_id)) {
        orderLines.get(order.order_id)?.applyOptions({ price: order.price });
      } else {
        const line = tvCandle.createPriceLine({
          price: order.price,
          color: order.side === 'buy' ? '#4caf50' : '#f44336',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `${order.side.toUpperCase()} ${order.qty}`,
        });
        orderLines.set(order.order_id, line);
      }
    }
  });

  // Task 6: trade markers merged with any lab markers
  $effect(() => {
    if (!tvCandle) return;
    const tradeMarkers = trades.map(t => ({
      time: t.time as number,
      position: (t.side === 'buy' ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
      color: t.side === 'buy' ? '#4caf50' : '#f44336',
      shape: (t.side === 'buy' ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown',
      text: String(t.price),
    }));
    tradeMarkers.sort((a, b) => (a.time as number) - (b.time as number));
    tvCandle.setMarkers(tradeMarkers);
  });

  function changeTimeframe(tf: number) {
    if (tf === selectedTf) return;
    // Save current visible range to preserve zoom/scale
    if (tvChart) {
      savedVisibleRange = tvChart.timeScale().getVisibleLogicalRange();
    }
    selectedTf = tf;
    orderLines.forEach(line => tvCandle?.removePriceLine(line));
    orderLines.clear();
    lastOhlcLen = 0;
    onSubscribe?.(selectedSymbol, tf);
  }

  function changeSymbol(sym: string) {
    if (sym === selectedSymbol) return;
    console.log('[Chart] changeSymbol: from', selectedSymbol, 'to', sym);
    const oldSymbol = selectedSymbol;
    symbolOverride = sym;
    orderLines.forEach(line => tvCandle?.removePriceLine(line));
    orderLines.clear();
    lastOhlcLen = 0;
    // Clear chart data and orderbook immediately when switching symbols
    if (tvCandle) {
      console.log('[Chart] clearing chart data');
      tvCandle.setData([]);
      tvVolume?.setData([]);
    }
    orderbookStore.clear(oldSymbol);
    console.log('[Chart] calling onSubscribe for', sym);
    onSubscribe?.(sym, selectedTf);
  }

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

    await new Promise(r => requestAnimationFrame(r));

    const { createChart } = await import('lightweight-charts');
    const chartH = Math.max((ohlcAreaEl.clientHeight || 320) - TICK_H, 100);
    tvChart = createChart(ohlcAreaEl, {
      width: ohlcAreaEl.clientWidth || 400,
      height: chartH,
      layout: { background: { color: '#0f0f1e' }, textColor: '#888' },
      grid: { vertLines: { color: '#1e1e3a' }, horzLines: { color: '#1e1e3a' } },
      timeScale: {
        borderColor: '#2d2d4a',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 1 },
    });

    tvCandle = tvChart.addCandlestickSeries({
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    });

    tvVolume = tvChart.addHistogramSeries({
      priceScaleId: 'volume',
      priceFormat: { type: 'volume' },
      color: '#4caf5040',
    });
    tvChart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const initial = candlesStore.get(selectedSymbol);
    if (initial.length) {
      const bars = initial.map(b => ({
        time: b.time as number,
        open: b.open as number,
        high: b.high as number,
        low: b.low as number,
        close: b.close as number,
      }));
      const vols = initial.map(b => ({
        time: b.time as number,
        value: b.volume as number,
        color: (b.close as number) >= (b.open as number) ? '#4caf5040' : '#f4433640',
      }));
      tvCandle.setData(bars);
      tvVolume.setData(vols);
      lastOhlcLen = initial.length;
      tvChart.timeScale().fitContent();
    }

    const ro = new ResizeObserver(() => {
      if (!tvChart) return;
      tvChart.applyOptions({
        width: ohlcAreaEl.clientWidth,
        height: Math.max(ohlcAreaEl.clientHeight, 100),
      });
    });
    ro.observe(ohlcAreaEl);
  });

  onDestroy(() => {
    uplotInst?.destroy();
    tvChart?.remove();
  });
</script>

<div class="frame">
  <div class="frame-header">
    <!-- Task 3: instrument selector -->
    <select
      class="sym-select"
      value={selectedSymbol}
      onchange={(e) => changeSymbol((e.target as HTMLSelectElement).value)}
    >
      {#if instruments.length === 0}
        <option value={selectedSymbol}>{selectedSymbol}</option>
      {:else}
        {#each instruments as inst}
          <option value={inst.symbol}>{inst.ticker} — {inst.name}</option>
        {/each}
      {/if}
    </select>
    <!-- Task 5: timeframe selector -->
    <div class="tf-group">
      {#each TIMEFRAMES as tf}
        <button
          class="tf-btn"
          class:active={selectedTf === tf.value}
          onclick={() => changeTimeframe(tf.value)}
        >{tf.label}</button>
      {/each}
    </div>
  </div>
  <div class="chart-area">
    <div class="tick-strip" bind:this={tickEl}></div>
    <div class="ohlc-area" bind:this={ohlcAreaEl}></div>
  </div>
</div>

<style>
  .frame {
    display: flex; flex-direction: column;
    min-height: 320px; border-bottom: 1px solid #2d2d4a;
  }
  .frame-header {
    display: flex; align-items: center; gap: 8px;
    padding: 3px 8px; background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    flex-shrink: 0; flex-wrap: wrap;
  }
  .sym-select {
    background: #0f0f1e; color: #aaa; border: 1px solid #2d2d4a;
    font-size: 11px; padding: 2px 4px; border-radius: 3px;
    max-width: 180px;
  }
  .tf-group { display: flex; gap: 2px; }
  .tf-btn {
    background: transparent; color: #555; border: 1px solid transparent;
    font-size: 10px; padding: 1px 5px; border-radius: 3px; cursor: pointer;
    transition: color 0.1s;
  }
  .tf-btn:hover { color: #aaa; }
  .tf-btn.active { color: #4caf50; border-color: #4caf5066; }
  .chart-area { flex: 1; position: relative; overflow: hidden; display: flex; flex-direction: column; min-height: 0; }
  .tick-strip { flex-shrink: 0; height: 80px; position: relative; z-index: 1; }
  .ohlc-area { flex: 1; min-height: 0; position: relative; }
</style>
