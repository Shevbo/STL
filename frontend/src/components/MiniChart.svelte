<!-- frontend/src/components/MiniChart.svelte
  Compact single-instrument candlestick used by ChartsGrid to show every instrument
  that is in a position or has working orders, all in one frame. History via the proven
  REST path (/api/v1/chart/bars); live last-price nudges the last candle from the quote
  store. No controls, no tick strip — just a small chart + a header chip. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { quotesStore } from '$lib/stores/quotes.svelte';

  let {
    symbol,
    label = '',
    badge = '',
    badgeKind = 'neutral',
    tf = 5,
  }: {
    symbol: string;
    label?: string;
    badge?: string;
    badgeKind?: 'long' | 'short' | 'neutral';
    tf?: number;
  } = $props();

  const TF_NAMES: Record<number, string> = {
    1: 'TIME_FRAME_M1', 5: 'TIME_FRAME_M5', 15: 'TIME_FRAME_M15',
    11: 'TIME_FRAME_M15', 12: 'TIME_FRAME_M15', 19: 'TIME_FRAME_D',
  };

  let el: HTMLDivElement;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let chart: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let series: any = null;
  let ready = $state(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let bars: any[] = [];
  let lastClose = $state<number | null>(null);

  let quote = $derived(quotesStore.get(symbol));

  async function loadHistory() {
    try {
      const tfName = TF_NAMES[tf] ?? 'TIME_FRAME_M5';
      const r = await fetchWithAuth(
        `/api/v1/chart/bars?symbol=${encodeURIComponent(symbol)}&tf=${tfName}`,
      );
      if (!r.ok) return;
      const data = await r.json();
      if (!Array.isArray(data) || !data.length) return;
      bars = data.map((b: Record<string, number>) => ({
        time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
      }));
      if (series) {
        series.setData(bars);
        chart.timeScale().fitContent();
        lastClose = bars[bars.length - 1].close;
      }
    } catch { /* ignore transient */ }
  }

  // nudge the last candle's close from the live quote (same as the main chart)
  $effect(() => {
    if (!series || !quote || !bars.length) return;
    const price = quote.last || quote.bid || 0;
    if (!price) return;
    const last = bars[bars.length - 1];
    series.update({
      time: last.time, open: last.open,
      high: Math.max(last.high, price), low: Math.min(last.low, price), close: price,
    });
    lastClose = price;
  });

  onMount(async () => {
    const { createChart } = await import('lightweight-charts');
    if (!el) return;
    chart = createChart(el, {
      width: el.clientWidth || 260,
      height: el.clientHeight || 150,
      layout: { background: { color: '#0f0f1e' }, textColor: '#778' },
      grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 4 },
      rightPriceScale: { borderColor: '#2d2d4a', autoScale: true },
      crosshair: { mode: 1 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    });
    series = chart.addCandlestickSeries({
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    });
    ready = true;
    const ro = new ResizeObserver(() => {
      if (chart && el) chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    });
    ro.observe(el);
    roRef = ro;
    loadHistory();
  });

  // reload when the symbol or timeframe changes (grid can re-key, but be safe)
  let loadedKey = '';
  $effect(() => {
    const key = `${symbol}@${tf}`;
    if (!ready || key === loadedKey) return;
    loadedKey = key;
    loadHistory();
  });

  let roRef: ResizeObserver | null = null;
  onDestroy(() => { roRef?.disconnect(); chart?.remove(); });
</script>

<div class="mini">
  <div class="mini-head">
    <span class="mc-label">{label || symbol}</span>
    {#if badge}
      <span class="mc-badge" class:long={badgeKind === 'long'} class:short={badgeKind === 'short'}>{badge}</span>
    {/if}
    {#if lastClose !== null}<span class="mc-px">{lastClose.toLocaleString('ru-RU')}</span>{/if}
  </div>
  <div class="mc-chart" bind:this={el}></div>
</div>

<style>
  .mini {
    display: flex; flex-direction: column; min-width: 0; min-height: 0;
    background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; overflow: hidden;
  }
  .mini-head {
    display: flex; align-items: center; gap: 6px; flex-shrink: 0;
    padding: 2px 6px; background: #1a1a2e; border-bottom: 1px solid #2d2d4a; font-size: 11px;
  }
  .mc-label { color: #9ab; font-weight: 600; }
  .mc-badge {
    font-size: 10px; padding: 0 5px; border-radius: 3px; color: #0d0d1c; font-weight: 700;
    background: #6aa8ff;
  }
  .mc-badge.long { background: #4caf50; }
  .mc-badge.short { background: #f44336; }
  .mc-px { margin-left: auto; color: #cde; font-size: 11px; }
  .mc-chart { flex: 1; min-height: 0; position: relative; }
</style>
