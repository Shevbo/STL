<!-- frontend/src/components/MiniChart.svelte
  Compact single-instrument candlestick used by ChartsGrid to show every instrument
  that is in a position or has working orders, all in one frame. History via the proven
  REST path (/api/v1/chart/bars); live last-price nudges the last candle from the quote
  store. No controls, no tick strip — just a small chart + a header chip. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import { mskTickFormatter, mskCrosshairFormatter } from '$lib/chart-time';

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
  let loading = $state(true);   // true until the first bars are drawn (shows an overlay)
  let failed = $state(false);   // fetch returned nothing → show a retry hint
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let bars: any[] = [];
  let lastClose = $state<number | null>(null);

  let quote = $derived(quotesStore.get(symbol));

  async function loadHistory(attempt = 0) {
    loading = true; failed = false;
    try {
      const tfName = TF_NAMES[tf] ?? 'TIME_FRAME_M5';
      const r = await fetchWithAuth(
        `/api/v1/chart/bars?symbol=${encodeURIComponent(symbol)}&tf=${tfName}`,
      );
      const data = r.ok ? await r.json() : null;
      if (!Array.isArray(data) || !data.length) {
        // No data yet (slow Finam / transient): retry a couple of times, then show a hint.
        if (attempt < 3) { setTimeout(() => loadHistory(attempt + 1), 1500); return; }
        loading = false; failed = true; return;
      }
      bars = data.map((b: Record<string, number>) => ({
        time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
      }));
      if (series) {
        series.setData(bars);
        chart.timeScale().fitContent();
        lastClose = bars[bars.length - 1].close;
      }
      loading = false; failed = false;
    } catch {
      if (attempt < 3) { setTimeout(() => loadHistory(attempt + 1), 1500); return; }
      loading = false; failed = true;
    }
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
      localization: { timeFormatter: mskCrosshairFormatter },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 4, tickMarkFormatter: mskTickFormatter },
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
      if (!chart || !el) return;
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      // The chart may have been created before the grid cell had its final size (0 →
      // fallback), which framed the candles into the wrong box; reframe once sized.
      if (bars.length) chart.timeScale().fitContent();
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
  <div class="mc-chart" bind:this={el}>
    {#if loading}
      <div class="mc-ov"><span class="mc-spin"></span> загрузка…</div>
    {:else if failed}
      <button class="mc-ov mc-retry" onclick={() => loadHistory()}>нет данных · повторить</button>
    {/if}
  </div>
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
  .mc-ov {
    position: absolute; inset: 0; z-index: 2; display: flex; gap: 6px;
    align-items: center; justify-content: center; font-size: 11px; color: #778;
    background: #0f0f1ecc; border: none; font-family: inherit;
  }
  .mc-retry { cursor: pointer; color: #9ab; }
  .mc-retry:hover { color: #cde; }
  .mc-spin {
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid #2d2d4a; border-top-color: #6aa8ff; animation: mcspin 0.8s linear infinite;
  }
  @keyframes mcspin { to { transform: rotate(360deg); } }
</style>
