<!-- frontend/src/components/ChartFrame.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
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

  // Map the dropdown tf number to the Finam TIME_FRAME_* name the backend expects.
  // Mirrors trader/api/ws_hub.py _TIMEFRAME_NAMES (REST has no M30/H*, collapses to M15/D).
  const TF_NAMES: Record<number, string> = {
    1: 'TIME_FRAME_M1',
    5: 'TIME_FRAME_M5',
    9: 'TIME_FRAME_M15',
    11: 'TIME_FRAME_M15',
    12: 'TIME_FRAME_M15',
    13: 'TIME_FRAME_M15',
    15: 'TIME_FRAME_M15',
    17: 'TIME_FRAME_D',
    19: 'TIME_FRAME_D',
    20: 'TIME_FRAME_W',
    21: 'TIME_FRAME_MN',
  };

  // Load chart history from the PROVEN Finam REST path (/api/v1/chart/bars). The
  // ws/gRPC stream returns StatusCode.INTERNAL for some instruments (e.g. GZU6@RTSX),
  // so the initial candles must not depend on it. ws 'ohlc_update' still appends live
  // bars on top. A per-(symbol,tf) guard avoids racing the same fetch twice.
  let pendingHistory = '';
  async function loadRestHistory(sym: string, tf: number) {
    const key = `${sym}@${tf}`;
    if (pendingHistory === key) return;
    pendingHistory = key;
    try {
      const tfName = TF_NAMES[tf] ?? 'TIME_FRAME_M5';
      const r = await fetchWithAuth(
        `/api/v1/chart/bars?symbol=${encodeURIComponent(sym)}&tf=${tfName}`,
      );
      if (!r.ok) return;
      const bars = await r.json();
      // Ignore a stale response if the user switched away mid-flight.
      if (sym !== selectedSymbol || tf !== selectedTf) return;
      if (Array.isArray(bars) && bars.length) {
        candlesStore.setHistory(sym, bars);
        // Draw DIRECTLY onto the series (mirrors the working MiniChart) instead of
        // relying solely on the reactive ohlc-derived → effect chain, which did not
        // repaint the main chart (it stayed blank while the identical REST data drew
        // fine in the positions/orders grid). The reactive effect still appends live
        // ws bars on top.
        if (tvCandle && sym === selectedSymbol) {
          drawBars(bars);
        }
      }
    } catch (e) {
      console.warn('[Chart] REST history load failed', e);
    } finally {
      if (pendingHistory === key) pendingHistory = '';
    }
  }

  // Paint a full bar set onto the candle + volume series and frame the last ~96 bars.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function drawBars(bars: any[]) {
    const candles = bars.map((b) => ({
      time: b.time as number, open: +b.open, high: +b.high, low: +b.low, close: +b.close,
    }));
    const vols = bars.map((b) => ({
      time: b.time as number, value: +(b.volume ?? 0),
      color: +b.close >= +b.open ? '#4caf5040' : '#f4433640',
    }));
    tvCandle.setData(candles);
    tvVolume?.setData(vols);
    lastOhlcLen = candles.length;
    const barsToShow = Math.min(candles.length, maxVisibleBars);
    tvChart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, candles.length - barsToShow), to: candles.length - 1,
    });
    scrollPos = 100;
  }

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
  // becomes true once the candlestick series exists; gates the history-load effect.
  let chartReady = $state(false);
  // price lines map: order_id → priceLine
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let orderLines = new Map<string, any>();

  // Load REST history whenever the EFFECTIVE symbol or timeframe changes — including a
  // change driven by the `symbol` PROP (robot select / activeSymbol on the main screen),
  // not just the in-component dropdown. Without this the chart went blank after a
  // prop-driven symbol switch (candlesStore.get(newSymbol) was empty and nothing
  // re-fetched it). chartReady gates it so the first run waits for the series.
  let loadedKey = '';
  $effect(() => {
    const sym = selectedSymbol;
    const tf = selectedTf;
    if (!chartReady || !tvCandle || !sym) return;
    const key = `${sym}@${tf}`;
    if (key === loadedKey) return;
    loadedKey = key;
    // clear the previous symbol's drawing so stale candles/lines never linger
    orderLines.forEach((line) => tvCandle?.removePriceLine(line));
    orderLines.clear();
    lastOhlcLen = 0;
    tvCandle.setData([]);
    tvVolume?.setData([]);
    onSubscribe?.(sym, tf);
    loadRestHistory(sym, tf);
  });


  let ohlc = $derived.by(() => {
    return candlesStore.get(selectedSymbol);
  });
  let quote = $derived(quotesStore.get(selectedSymbol));
  let orders = $derived(ordersStore.forSymbol(selectedSymbol));
  let trades = $derived(tradesStore.forSymbol(selectedSymbol));
  // Sorted alphabetically by ticker for the dropdown (list arrives ranked by turnover).
  let instruments = $derived(
    [...instrumentStore.list].sort((a, b) => a.ticker.localeCompare(b.ticker))
  );

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
  let scrollPos = $state(100);
  let maxVisibleBars = 96;

  // Task 2: sync OHLC + volume series.
  // Only a FRESH dataset (first load / symbol / timeframe change) resets the view to
  // the last N bars. New bars are applied with update() so the user's wheel zoom /
  // scroll and the auto-scaled price axis are preserved (previously every new bar did
  // setData(all) + forced the range back to the last 96, which threw away the zoom
  // and made "compress with the wheel" appear to do nothing).
  $effect(() => {
    if (!tvCandle || !ohlc.length) return;
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
    const fresh = lastOhlcLen === 0 || ohlc.length < lastOhlcLen;
    if (fresh) {
      tvCandle.setData(bars);
      tvVolume?.setData(vols);
      const barsToShow = Math.min(bars.length, maxVisibleBars);
      tvChart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, bars.length - barsToShow), to: bars.length - 1,
      });
      scrollPos = 100;
    } else if (ohlc.length === lastOhlcLen + 1) {
      tvCandle.update(bars[bars.length - 1]);      // one new bar appended
      tvVolume?.update(vols[vols.length - 1]);
    } else if (ohlc.length > lastOhlcLen) {
      tvCandle.setData(bars);                       // multi-bar catch-up; keep view
      tvVolume?.setData(vols);
    } else {
      tvCandle.update(bars[bars.length - 1]);       // same length: last bar refreshed
      tvVolume?.update(vols[vols.length - 1]);
    }
    lastOhlcLen = ohlc.length;
  });

  // Task 7: sync horizontal scrollbar with chart visible range
  $effect(() => {
    if (!tvChart) return;
    const unsubscribe = tvChart.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
      if (!range || !ohlc.length) return;
      const barsToShow = Math.min(ohlc.length, maxVisibleBars);
      const maxFrom = Math.max(0, ohlc.length - barsToShow);
      if (maxFrom === 0) {
        scrollPos = 100;
      } else {
        scrollPos = Math.min(100, (range.from / maxFrom) * 100);
      }
    });
    return () => unsubscribe?.();
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
    selectedTf = tf;
    orderLines.forEach(line => tvCandle?.removePriceLine(line));
    orderLines.clear();
    lastOhlcLen = 0;
    onSubscribe?.(selectedSymbol, tf);
    loadRestHistory(selectedSymbol, tf);   // REST history for the new timeframe
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
    // Always pull history from the proven REST path; works for every instrument
    // (incl. GZU6@RTSX) regardless of the gRPC stream's per-symbol failures.
    loadRestHistory(sym, selectedTf);
  }

  function handleScroll(event: Event) {
    if (!tvChart || !ohlc.length) return;
    const percent = Number((event.target as HTMLInputElement).value);
    const barsToShow = Math.min(ohlc.length, maxVisibleBars);
    const maxFrom = Math.max(0, ohlc.length - barsToShow);
    const from = Math.round((percent / 100) * maxFrom);
    const to = Math.min(from + barsToShow - 1, ohlc.length - 1);
    tvChart.timeScale().setVisibleLogicalRange({ from, to });
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
        rightOffset: 10,
      },
      // Auto-fit the price axis to whatever bars are in view, so zooming the time
      // axis out (wheel) extends the price scale to the older/higher candles.
      rightPriceScale: { borderColor: '#2d2d4a', autoScale: true },
      // Be explicit about wheel zoom + drag scroll (defaults, but guard against
      // any global override swallowing the wheel).
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      crosshair: { mode: 1 },
    });
    tvChart.timeScale().applyOptions({
      timeVisible: true,
    } as any);

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

    // The series exist now: let the history-load effect run for the current symbol/tf
    // (and for every later symbol/tf change, incl. prop-driven ones). The REST path is
    // the proven source of the first candles; ws appends live bars on top.
    chartReady = true;
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
    <div class="scrollbar-container">
      <input
        type="range"
        class="chart-scrollbar"
        min="0"
        max="100"
        value={scrollPos}
        onchange={handleScroll}
        oninput={handleScroll}
      />
    </div>
  </div>
</div>

<style>
  .frame {
    display: flex; flex-direction: column;
    /* Fill the whole chart container. Without flex:1 the frame stayed at its
       320px min-height, leaving a big empty gap below the chart whose top edge
       looked like a draggable border but had no handle — the real resize handle
       sat at the bottom of that gap. */
    flex: 1; min-height: 320px; border-bottom: 1px solid #2d2d4a;
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
  .scrollbar-container { flex-shrink: 0; padding: 2px 0; background: #0f0f1e; }
  .chart-scrollbar {
    width: 100%; height: 12px; cursor: pointer;
    appearance: none; background: transparent;
  }
  .chart-scrollbar::-webkit-slider-thumb {
    appearance: none;
    width: 20px; height: 12px; background: #4caf50; border-radius: 2px; cursor: pointer;
  }
  .chart-scrollbar::-moz-range-thumb {
    width: 20px; height: 12px; background: #4caf50; border-radius: 2px; cursor: pointer; border: none;
  }
  .chart-scrollbar::-webkit-slider-runnable-track {
    background: #1e1e3a; height: 4px; border-radius: 2px;
  }
  .chart-scrollbar::-moz-range-track {
    background: transparent;
  }
</style>
