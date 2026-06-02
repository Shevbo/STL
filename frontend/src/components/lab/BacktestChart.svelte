<!-- BacktestChart.svelte
     - candles (dimmed) + per-fill trade triangles placed at the exact fill price
     - dashed connectors: green = long episode, red = short episode (open → FULL close)
     - hover tooltip on a triangle: date/time, price, type (open/average/partial/full N)
     - resting + planned order price lines
     - top-right stats overlay; bottom equity ("График доходности робота")
     - QUIK-style nav: wheel = candle-width zoom, shift+wheel / drag = horizontal pan,
       native time-scale scrollbar; interval selector pinned in a fixed header row
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import {
    toFills, replay, computeStats, tradeEvents, priceMarkers,
    positionEpisodes, buildConnectors,
  } from '../../lib/lab-analytics';

  let {
    result, symbol, strategy = null, dateFrom, dateTo, pointValue = 1, defaultInterval = 60,
    openOrders = [], plannedOrders = [],
  }: {
    result: any; symbol: string; strategy?: any; dateFrom: string; dateTo: string;
    pointValue?: number; defaultInterval?: number;
    openOrders?: Array<{ side: string; price: number; qty: number; order_id?: string }>;
    plannedOrders?: Array<{ side: string; price: number; qty: number; reason?: string }>;
  } = $props();

  // Trade triangle colors — distinct teal/rose tonality, brighter than candles.
  const BUY_COLOR = '#2ee6a6';   // teal-green
  const SELL_COLOR = '#ff5c8a';  // rose-red

  let containerEl: HTMLDivElement;
  let candleEl: HTMLDivElement;
  let equityEl: HTMLDivElement;
  let scrollTrackEl: HTMLDivElement;

  let tvCandle: any = null, tvEquity: any = null;
  let candleSeries: any = null, volumeSeries: any = null;
  let longSeries: any = null, shortSeries: any = null, equitySeries: any = null;
  let buyMarkSeries: any = null, sellMarkSeries: any = null;
  let orderPriceLines: any[] = [];
  let markIndex: Array<{ time: number; price: number; side: 'buy' | 'sell'; label: string; rawTime: number }> = [];
  let syncing = false, syncReady = false;

  let loading = $state(true);
  let error = $state('');
  let stats = $state<any>(null);
  let crossLabel = $state('');
  let resampleMin = $state(defaultInterval);
  let margin = $state<number | null>(null);
  let tip = $state<{ x: number; y: number; lines: string[] } | null>(null);

  // Custom horizontal scrollbar (lightweight-charts has no scrollbar widget).
  // Works in LOGICAL (bar-index) space — the chart pans/zooms by bar index, and
  // bars are NOT evenly spaced in time (gaps/weekends), so a time-based thumb
  // distorts the window. barCount = total bars; thumb maps over [0, barCount].
  let barCount = 0;
  let scrollThumb = $state({ left: 0, width: 100 });     // percent
  let draggingBar = false, dragStartX = 0, dragStartLeft = 0;

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
  // Bar epochs carry Moscow wall-clock stamped as UTC, so format in UTC to match axis.
  const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });

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
      grid: { vertLines: { color: '#15152470' }, horzLines: { color: '#15152470' } },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 4 },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d2d4a', minimumWidth: 84 },
      // QUIK-like interactions: wheel zooms candle width; click-drag on the chart
      // PANS horizontally (pressedMouseMove). Dragging must NOT rescale — only the
      // price axis (right edge) rescales on drag, never the chart body.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: {
        mouseWheel: true, pinch: true,
        axisPressedMouseMove: { time: false, price: true },
        axisDoubleClickReset: true,
      },
    };

    // Top chart hides its own axis — the single shared time axis lives on the
    // equity chart below; horizontal scrolling uses the custom scrollbar + drag.
    tvCandle = createChart(candleEl, {
      ...chartOpts,
      timeScale: { ...chartOpts.timeScale, visible: false },
      width: candleEl.clientWidth || 600, height: candleEl.clientHeight || 280,
    });
    // Candles dimmed further (~15% more, toward background) so triangles dominate.
    candleSeries = tvCandle.addCandlestickSeries({
      upColor: '#155a33', downColor: '#69241d',
      borderUpColor: '#1d6e40', borderDownColor: '#7d2a22',
      wickUpColor: '#1d6e40', wickDownColor: '#7d2a22',
    });
    volumeSeries = tvCandle.addHistogramSeries({ priceScaleId: 'vol', color: '#4caf5018', priceFormat: { type: 'volume' } });
    tvCandle.priceScale('vol').applyOptions({ scaleMargins: { top: 0.9, bottom: 0 } });

    longSeries = tvCandle.addLineSeries({
      color: '#2ee6a6', lineWidth: 1, lineStyle: LineStyle.Dashed,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    shortSeries = tvCandle.addLineSeries({
      color: '#ff5c8a', lineWidth: 1, lineStyle: LineStyle.Dashed,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });

    const markAnchor = {
      lineVisible: false, pointMarkersVisible: false,
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    };
    buyMarkSeries = tvCandle.addLineSeries(markAnchor);
    sellMarkSeries = tvCandle.addLineSeries(markAnchor);

    // Equity chart carries the single visible time axis (scrollbar lives here).
    tvEquity = createChart(equityEl, {
      ...chartOpts,
      width: equityEl.clientWidth || 600, height: equityEl.clientHeight || 150,
    });
    equitySeries = tvEquity.addBaselineSeries({
      baseValue: { type: 'price', price: 100000 },
      topLineColor: '#4caf50', topFillColor1: '#4caf5040', topFillColor2: '#4caf5008',
      bottomLineColor: '#f44336', bottomFillColor1: '#f4433608', bottomFillColor2: '#f4433640',
      lineWidth: 1, priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });

    // Crosshair: time label + trade tooltip if hovering near a triangle.
    const onCross = (p: any) => {
      crossLabel = (p && p.time) ? fmtTs(p.time) : '';
      hitTestTooltip(p);
    };
    tvCandle.subscribeCrosshairMove(onCross);
    tvEquity.subscribeCrosshairMove((p: any) => { crossLabel = (p && p.time) ? fmtTs(p.time) : ''; });

    // Lock both axes together by visible TIME range so they pan/zoom as one.
    const link = (from: any, to: any) =>
      from.timeScale().subscribeVisibleTimeRangeChange((r: any) => {
        if (!syncReady || syncing || !r || r.from == null || r.to == null) return;
        syncing = true;
        try { to.timeScale().setVisibleRange(r); } catch { /* transient */ } finally { syncing = false; }
      });
    link(tvCandle, tvEquity);
    link(tvEquity, tvCandle);

    // Drive the scrollbar thumb from the LOGICAL (bar-index) range — robust to
    // uneven bar spacing, unlike time coordinates.
    tvCandle.timeScale().subscribeVisibleLogicalRangeChange((lr: any) => {
      if (!syncReady || draggingBar || !lr) return;
      updateThumb(lr);
    });

    // Shift+wheel = horizontal pan (QUIK). Plain wheel is left to handleScale (zoom).
    candleEl.addEventListener('wheel', onWheelPan, { passive: false });
    equityEl.addEventListener('wheel', onWheelPan, { passive: false });

    const ro = new ResizeObserver(() => {
      tvCandle?.applyOptions({ width: candleEl.clientWidth, height: candleEl.clientHeight });
      tvEquity?.applyOptions({ width: equityEl.clientWidth, height: equityEl.clientHeight });
    });
    ro.observe(containerEl);

    await loadMeta();
    await loadData();
  });

  function onWheelPan(ev: WheelEvent) {
    if (!ev.shiftKey || !tvCandle) return;
    ev.preventDefault();
    const ts = tvCandle.timeScale();
    const pos = ts.scrollPosition();
    // 1 wheel notch ≈ 3 bars; shift sign so wheel-down scrolls forward in time.
    ts.scrollToPosition(pos + (ev.deltaY > 0 ? -3 : 3), false);
  }

  // Reflect the visible window as a thumb over [0, barCount] in LOGICAL space.
  // The logical range can extend past the data (rightOffset, partial bars), so
  // clamp into the data bounds before mapping to percent.
  function updateThumb(lr: { from: number; to: number }) {
    if (barCount <= 0) { scrollThumb = { left: 0, width: 100 }; return; }
    const from = Math.max(0, lr.from);
    const to = Math.min(barCount, lr.to);
    const left = Math.max(0, (from / barCount) * 100);
    const width = Math.min(100 - left, ((to - from) / barCount) * 100);
    scrollThumb = { left, width: Math.max(2, width) };
  }

  // Drag the scrollbar thumb → shift the visible LOGICAL range across the bars.
  function onBarDown(ev: PointerEvent) {
    draggingBar = true; dragStartX = ev.clientX; dragStartLeft = scrollThumb.left;
    try { (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId); } catch { /* no active pointer */ }
  }
  function onBarMove(ev: PointerEvent) {
    if (!draggingBar || !scrollTrackEl || !tvCandle || barCount <= 0) return;
    const trackW = scrollTrackEl.clientWidth || 1;
    const dPct = ((ev.clientX - dragStartX) / trackW) * 100;
    const newLeft = Math.max(0, Math.min(100 - scrollThumb.width, dragStartLeft + dPct));
    // Keep the current window WIDTH (zoom) constant; only move its start bar.
    const winBars = (scrollThumb.width / 100) * barCount;
    const fromBar = (newLeft / 100) * barCount;
    syncing = true;
    try {
      tvCandle.timeScale().setVisibleLogicalRange({ from: fromBar, to: fromBar + winBars });
    } catch { /* transient */ } finally { syncing = false; }
    scrollThumb = { ...scrollThumb, left: newLeft };
  }
  function onBarUp(ev: PointerEvent) {
    draggingBar = false;
    try { (ev.target as HTMLElement).releasePointerCapture?.(ev.pointerId); } catch { /* no active pointer */ }
  }

  const fmtDur = (secs: number) => {
    const m = Math.round(secs / 60);
    if (m < 60) return `${m} мин`;
    const h = Math.floor(m / 60), mm = m % 60;
    if (h < 24) return `${h} ч ${mm} мин`;
    const d = Math.floor(h / 24);
    return `${d} дн ${h % 24} ч`;
  };

  function hitTestTooltip(p: any) {
    if (!p || !p.point || p.time == null) { tip = null; return; }
    const ts = tvCandle.timeScale();
    const px = p.point.x, py = p.point.y;

    // 1) trade triangle near the cursor (x within 9px, y within 14px)?
    let best: any = null, bestDx = Infinity;
    for (const m of markIndex) {
      const mx = ts.timeToCoordinate(m.time);
      if (mx == null) continue;
      const dx = Math.abs(mx - px);
      if (dx < bestDx) { bestDx = dx; best = m; }
    }
    if (best && bestDx <= 9) {
      const my = candleSeries.priceToCoordinate(best.price);
      if (my != null && Math.abs(my - py) <= 14) {
        const lines = [
          best.label,
          `${best.side === 'buy' ? 'Покупка' : 'Продажа'} @ ${Math.round(best.price).toLocaleString('ru-RU')}`,
          fmtTs(best.rawTime),
        ];
        if (best.close) {
          lines.push(`В позиции: ${fmtDur(best.close.holdSecs)}`);
          lines.push(`Макс. контрактов: ${best.close.maxContracts}`);
          lines.push(`Фин. результат: ${fmtMoney(best.close.pnl)} ₽`);
        }
        tip = { x: ts.timeToCoordinate(best.time) ?? px, y: my, lines };
        return;
      }
    }

    // 2) order / planned price line near the cursor (y within 6px)?
    for (const li of lineIndex) {
      const ly = candleSeries.priceToCoordinate(li.price);
      if (ly != null && Math.abs(ly - py) <= 6) {
        tip = { x: px, y: ly, lines: [li.text] };
        return;
      }
    }
    tip = null;
  }

  onDestroy(() => {
    candleEl?.removeEventListener('wheel', onWheelPan);
    equityEl?.removeEventListener('wheel', onWheelPan);
    tvCandle?.remove(); tvEquity?.remove();
  });

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
      volumeSeries.setData(bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? '#26a65b20' : '#c0392b20' })));
      barCount = bars.length;

      const fills = toFills(result?.trades);
      const { roundTrips } = replay(fills);
      const events = tradeEvents(fills, resampleMin * 60, pointValue);

      // triangles at exact fill price + hover index
      const pm = priceMarkers(events, { buy: BUY_COLOR, sell: SELL_COLOR });
      buyMarkSeries.setData(pm.buy.points);
      sellMarkSeries.setData(pm.sell.points);
      buyMarkSeries.setMarkers(pm.buy.markers);
      sellMarkSeries.setMarkers(pm.sell.markers);
      markIndex = pm.index;

      // dashed connectors run open → FULL close per episode; a still-open episode
      // extends to the last bar so its (e.g. green) line is visible.
      const lastBar = bars[bars.length - 1];
      const episodes = positionEpisodes(fills, lastBar.time, lastBar.close);
      longSeries.setData(buildConnectors(episodes, 'long'));
      shortSeries.setData(buildConnectors(episodes, 'short'));

      const eq: any[] = Array.isArray(result?.equity_curve)
        ? result.equity_curve
        : (typeof result?.equity_curve === 'string' ? JSON.parse(result.equity_curve) : []);
      if (eq.length) {
        equitySeries.applyOptions({ baseValue: { type: 'price', price: eq[0].equity } });
        equitySeries.setData(eq.map(p => ({ time: p.time, value: p.equity })));
      }

      stats = computeStats(fills, roundTrips, eq);
      if (pointValue !== 1 && stats) {
        stats = {
          ...stats,
          avgPerTrade: stats.avgPerTrade * pointValue,
          maxProfit: stats.maxProfit * pointValue,
          maxLoss: stats.maxLoss * pointValue,
        };
      }

      const tFrom = bars[0].time, tTo = bars[bars.length - 1].time;
      syncing = true;
      try {
        tvCandle.timeScale().setVisibleRange({ from: tFrom, to: tTo });
        tvEquity.timeScale().setVisibleRange({ from: tFrom, to: tTo });
      } finally { syncing = false; }
      syncReady = true;
      // Initialise the thumb from the actual logical range now shown.
      const lr = tvCandle.timeScale().getVisibleLogicalRange();
      if (lr) updateThumb(lr); else scrollThumb = { left: 0, width: 100 };
      drawOrderLines();
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // Horizontal price lines: resting orders (solid) + planned algo triggers (dotted),
  // green = buy, red = sell. No on-chart titles (axis labels off) — the description
  // shows only on hover (see hitTestTooltip → lineIndex).
  let lineIndex: Array<{ price: number; text: string }> = [];
  function drawOrderLines() {
    if (!candleSeries) return;
    for (const pl of orderPriceLines) { try { candleSeries.removePriceLine(pl); } catch { /* gone */ } }
    orderPriceLines = []; lineIndex = [];
    for (const o of openOrders ?? []) {
      const buy = o.side === 'buy';
      orderPriceLines.push(candleSeries.createPriceLine({
        price: o.price, color: buy ? BUY_COLOR : SELL_COLOR, lineWidth: 2, lineStyle: 0,
        axisLabelVisible: false, title: '',
      }));
      lineIndex.push({ price: o.price, text: `Заявка ${buy ? 'BUY' : 'SELL'} ${o.qty || ''} @ ${Math.round(o.price).toLocaleString('ru-RU')}`.trim() });
    }
    for (const o of plannedOrders ?? []) {
      const buy = o.side === 'buy';
      orderPriceLines.push(candleSeries.createPriceLine({
        price: o.price, color: buy ? BUY_COLOR : SELL_COLOR, lineWidth: 1, lineStyle: 1,  // dotted = plan
        axisLabelVisible: false, title: '',
      }));
      const why = (o as any).reason ? ` — ${(o as any).reason}` : '';
      lineIndex.push({ price: o.price, text: `План ${buy ? 'BUY' : 'SELL'} ${o.qty || ''} @ ${Math.round(o.price).toLocaleString('ru-RU')}${why}`.trim() });
    }
  }

  $effect(() => { if (result && candleSeries) loadData(); });
  $effect(() => { openOrders; plannedOrders; if (candleSeries && syncReady) drawOrderLines(); });
</script>

<div class="bt-root" bind:this={containerEl}>
  <!-- Pinned control header: never wraps, fixed position above the chart. -->
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
      <span class="lg lg-long">▲ покупка</span><span class="lg lg-short">▼ продажа</span>
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

    {#if tip}
      <div class="trade-tip" style="left:{tip.x + 12}px; top:{tip.y - 8}px;">
        {#each tip.lines as l, i}
          <div class={i === 0 ? 'tt-head' : 'tt-sub'}>{l}</div>
        {/each}
      </div>
    {/if}

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

  <!-- Custom horizontal scrollbar: drag the thumb to scroll across the data span. -->
  <div class="bt-scrollbar" bind:this={scrollTrackEl}>
    <div
      class="bt-thumb" role="scrollbar" tabindex="0" aria-controls="bt-chart" aria-valuenow={Math.round(scrollThumb.left)}
      style="left:{scrollThumb.left}%; width:{scrollThumb.width}%;"
      onpointerdown={onBarDown} onpointermove={onBarMove} onpointerup={onBarUp}
    ></div>
  </div>

  <div class="bt-hint">Колесо — масштаб свечи · Shift+колесо или перетаскивание графика — прокрутка · полоса ниже — скролл</div>
</div>

<style>
  .bt-root { display: flex; flex-direction: column; height: 100%; background: #0a0a15; }
  .bt-header {
    display: flex; align-items: center; gap: 10px; flex-wrap: nowrap; overflow: hidden;
    padding: 5px 10px; background: #0f0f1e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
    min-height: 30px;
  }
  .bt-symbol { font-size: 13px; color: #4caf50; font-weight: 600; flex-shrink: 0; }
  .bt-strategy { font-size: 11px; color: #6aa8ff; text-decoration: none; flex-shrink: 0; }
  .bt-strategy:hover { text-decoration: underline; }
  .bt-params { display: flex; gap: 4px; overflow: hidden; flex-shrink: 1; min-width: 0; }
  .bt-param { font-size: 10px; font-family: monospace; color: #888; background: #1a1a2e; border-radius: 2px; padding: 1px 5px; white-space: nowrap; }
  .bt-legend { display: flex; gap: 8px; font-size: 10px; flex-shrink: 0; }
  .lg-long { color: #2ee6a6; } .lg-short { color: #ff5c8a; }
  /* Interval selector pinned to the right, never wraps. */
  .bt-intervals { display: flex; gap: 1px; margin-left: auto; flex-shrink: 0; }
  .bt-intervals button { background: transparent; color: #555; border: 1px solid transparent; font-size: 10px; padding: 2px 7px; border-radius: 3px; cursor: pointer; }
  .bt-intervals button:hover { color: #aaa; }
  .bt-intervals button.active { color: #4caf50; border-color: #4caf5066; background: #4caf5012; }
  .bt-cross { font-size: 11px; font-family: monospace; color: #6aa8ff; padding-left: 8px; white-space: nowrap; flex-shrink: 0; }

  .bt-candle-area { position: relative; flex: 1; min-height: 0; }
  .candle { position: absolute; inset: 0; }

  .trade-tip {
    position: absolute; z-index: 8; pointer-events: none;
    background: #12121fee; border: 1px solid #3d3d5a; border-radius: 4px;
    padding: 5px 8px; font-size: 10px; white-space: nowrap;
    box-shadow: 0 4px 12px #000000aa;
  }
  .tt-head { color: #fff; font-weight: 600; margin-bottom: 2px; }
  .tt-sub { color: #aaa; font-family: monospace; }

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
  .equity { flex: 0 0 22%; min-height: 0; }

  .bt-scrollbar {
    position: relative; height: 12px; margin: 3px 10px; flex-shrink: 0;
    background: #14141f; border: 1px solid #2d2d4a; border-radius: 6px;
  }
  .bt-thumb {
    position: absolute; top: 1px; bottom: 1px; min-width: 16px;
    background: #3a3a5a; border-radius: 5px; cursor: grab;
  }
  .bt-thumb:hover { background: #4caf5066; }
  .bt-thumb:active { cursor: grabbing; background: #4caf5088; }

  .bt-hint { padding: 2px 10px; font-size: 9px; color: #555; background: #0f0f1e; border-top: 1px solid #1a1a2e; flex-shrink: 0; }

  .overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: #0a0a15cc; z-index: 10; font-size: 12px; color: #666; }
  .overlay.error { color: #f4433699; }
</style>
