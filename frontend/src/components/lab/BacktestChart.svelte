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
    positionEpisodes, buildConnectors, exitStats, commissionBreakdown, commissionFor,
  } from '../../lib/lab-analytics';

  let {
    result, symbol, strategy = null, dateFrom, dateTo, pointValue = 1, defaultInterval = 60,
    openOrders = [], plannedOrders = [], taker = true, runParams = {}, paramSchema = [], onRerun = null,
  }: {
    result: any; symbol: string; strategy?: any; dateFrom: string; dateTo: string;
    pointValue?: number; defaultInterval?: number;
    openOrders?: Array<{ side: string; price: number; qty: number; order_id?: string }>;
    plannedOrders?: Array<{ side: string; price: number; qty: number; reason?: string }>;
    // taker=true → backtest (exchange fee + broker); false → live (maker, broker only).
    taker?: boolean;
    // Editable params panel: current params + their schema (labels) + a re-run callback.
    runParams?: Record<string, any>;
    paramSchema?: Array<{ key: string; label?: string }>;
    onRerun?: ((p: Record<string, any>) => void) | null;
  } = $props();

  // ── Editable parameters panel (collapsed by default; edit → re-run backtest) ──
  let paramsOpen = $state(false);
  let editParams = $state<Record<string, any>>({});
  $effect(() => { editParams = { ...(params || {}) }; });   // resync when a new result loads
  const labelFor = (k: string) => paramSchema.find((s) => s.key === k)?.label || k;
  const editKeys = $derived(Object.keys(editParams).filter((k) => k !== 'symbol'));
  function applyParams() {
    if (!onRerun) return;
    const out: Record<string, any> = { ...editParams };
    for (const k of Object.keys(out)) {                     // numeric fields → numbers
      if (typeof params[k] === 'number') out[k] = Number(out[k]);
    }
    onRerun(out);
  }
  const paramsDirty = $derived(editKeys.some((k) => String(editParams[k]) !== String((params || {})[k])));

  // Trade triangle colors — distinct teal/rose tonality, brighter than candles.
  const BUY_COLOR = '#2ee6a6';   // teal-green (entry / averaging, buy side)
  const SELL_COLOR = '#ff5c8a';  // rose-red (entry / averaging, sell side)
  const TP_COLOR = '#19e36a';    // bright green — closing fill in profit (take-profit)
  const SL_COLOR = '#ff3b3b';    // bright red — closing fill in loss (stop-loss)

  let containerEl: HTMLDivElement;
  let candleEl: HTMLDivElement;
  let equityEl: HTMLDivElement;
  let scrollTrackEl: HTMLDivElement;
  let roRef: ResizeObserver | null = null;

  let tvCandle: any = null, tvEquity: any = null;
  let candleSeries: any = null, volumeSeries: any = null;
  let longSeries: any = null, shortSeries: any = null, equitySeries: any = null;
  let buyMarkSeries: any = null, sellMarkSeries: any = null;
  let orderPriceLines: any[] = [];
  let markIndex: Array<{ time: number; price: number; side: 'buy' | 'sell'; label: string; rawTime: number; close?: any }> = [];
  let syncReady = false;

  let loading = $state(true);
  let error = $state('');
  let stats = $state<any>(null);
  let exits = $state<any>(null);   // TP/SL exit analytics
  let commission = $state<any>(null);   // broker/exchange commission breakdown
  let netResult = $state(0);            // Σ realized close PnL (₽, net of commission)
  let statsExpanded = $state(false);    // report collapsed to 2 lines by default
  let showTrades = $state(false);       // trades-table overlay
  let tradeRows = $state<any[]>([]);    // per-trade rows for the table
  let crossLabel = $state('');
  let resampleMin = $state(defaultInterval);
  let margin = $state<number | null>(null);
  let tip = $state<{ x: number; y: number; head?: string; headKind?: 'tp' | 'sl' | 'neutral'; lines: string[] } | null>(null);

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
    (result?.params && typeof result.params === 'object') ? result.params
      : (typeof result?.params === 'string' ? JSON.parse(result.params)
         : (runParams || {}))
  );

  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  const fmtRub = (v: number) => Math.round(v).toLocaleString('ru-RU') + ' ₽';
  // Commission for one fill, using this chart's instrument + taker/maker mode.
  const commissionForFill = (price: number, qty: number) => commissionFor(symbol, price, qty, pointValue, taker);
  const KIND_RU: Record<string, string> = {
    open: 'Открытие', average: 'Усреднение', partial: 'Част. закрытие',
    full: 'Полн. закрытие', reverse: 'Реверс',
  };
  // Bar epochs carry Moscow wall-clock stamped as UTC, so format in UTC to match axis.
  const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const fmtDay = (ts: number) => new Date(ts * 1000).toLocaleDateString('ru-RU', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', year: '2-digit',
  });
  let periodLabel = $state('');   // actual loaded data span, shown in the header

  // Reset zoom to show the WHOLE test period (all bars) on screen.
  function fitAll() { try { tvCandle?.timeScale().fitContent(); } catch { /* not ready */ } }

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
      // fixLeftEdge/fixRightEdge clamp panning+zoom to the data so there are never
      // empty gaps on the left/right when you zoom out — data always fills the view.
      timeScale: {
        borderColor: '#2d2d4a', timeVisible: true, rightOffset: 0,
        fixLeftEdge: true, fixRightEdge: true,
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d2d4a', minimumWidth: 84 },
      // QUIK-like: wheel zooms candle width; click-drag pans horizontally. Only the
      // price axis rescales on drag (never the chart body / time axis).
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: {
        mouseWheel: true, pinch: true,
        axisPressedMouseMove: { time: false, price: true },
        axisDoubleClickReset: true,
      },
    };

    // Top chart is the ONLY interactive one. Its axis is hidden — the visible time
    // axis lives on the equity chart below, which mirrors the candle range one-way.
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

    // Equity chart shows the visible time axis but is NON-interactive: it only
    // mirrors the candle chart's range (one-way). Making it interactive created a
    // two-way sync loop that drifted the candle width while dragging.
    tvEquity = createChart(equityEl, {
      ...chartOpts,
      handleScroll: false, handleScale: false,
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

    // ONE-WAY sync: equity mirrors the candle chart's logical range. Logical (not
    // time) range avoids drift from uneven bar spacing, and one-way avoids the
    // feedback loop that rescaled candle width during a drag.
    tvCandle.timeScale().subscribeVisibleLogicalRangeChange((lr: any) => {
      if (!lr) return;
      try { tvEquity.timeScale().setVisibleLogicalRange(lr); } catch { /* transient */ }
      if (syncReady && !draggingBar) updateThumb(lr);
    });

    // Shift+wheel = horizontal pan (QUIK). Plain wheel is left to handleScale (zoom).
    candleEl.addEventListener('wheel', onWheelPan, { passive: false });
    equityEl.addEventListener('wheel', onWheelPan, { passive: false });

    const ro = new ResizeObserver(() => {
      // Elements may be gone if the window closed mid-resize — guard against null.
      if (candleEl) tvCandle?.applyOptions({ width: candleEl.clientWidth, height: candleEl.clientHeight });
      if (equityEl) tvEquity?.applyOptions({ width: equityEl.clientWidth, height: equityEl.clientHeight });
    });
    ro.observe(containerEl);
    roRef = ro;

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
    try {
      tvCandle.timeScale().setVisibleLogicalRange({ from: fromBar, to: fromBar + winBars });
    } catch { /* transient */ }
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
        let head: string, headKind: 'tp' | 'sl' | 'neutral' = 'neutral';
        const lines: string[] = [];
        if (best.close) {
          // Exit fills: make the TP/SL type the headline so it's unmistakable.
          head = best.close.exitLabel;                 // "Частичный TP · ..." etc.
          headKind = best.close.exit === 'TP' ? 'tp' : 'sl';
          lines.push(best.label);                      // "Полн. закрытие N (всего в поз. V)"
          lines.push(`${best.side === 'buy' ? 'Покупка' : 'Продажа'} @ ${Math.round(best.price).toLocaleString('ru-RU')}`);
          lines.push(fmtTs(best.rawTime));
          lines.push(`В позиции: ${fmtDur(best.close.holdSecs)}`);
          lines.push(`Макс. контрактов: ${best.close.maxContracts}`);
          lines.push(`Фин. результат: ${fmtMoney(best.close.pnl)} ₽`);
        } else {
          // Entry / averaging fills.
          head = best.label;
          lines.push(`${best.side === 'buy' ? 'Покупка' : 'Продажа'} @ ${Math.round(best.price).toLocaleString('ru-RU')}`);
          lines.push(fmtTs(best.rawTime));
        }
        tip = { x: ts.timeToCoordinate(best.time) ?? px, y: my, head, headKind, lines };
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
    roRef?.disconnect();
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
      periodLabel = `${fmtDay(bars[0].time)} — ${fmtDay(bars[bars.length - 1].time)}`;

      const fills = toFills(result?.trades);
      const { roundTrips } = replay(fills);
      // taker prop decides model: backtest = taker (exchange+broker), live = maker.
      const events = tradeEvents(fills, resampleMin * 60, pointValue, symbol, taker);

      // triangles at exact fill price + hover index; closing fills tinted TP/SL
      const pm = priceMarkers(events, { buy: BUY_COLOR, sell: SELL_COLOR, tp: TP_COLOR, sl: SL_COLOR });
      exits = exitStats(events);
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
        // Align equity to the CANDLE bucket times: lightweight-charts spaces bars by
        // index (not by time), so two charts only line up if they share the exact
        // same time/index set. Carry the last equity value forward onto each candle
        // time → one equity point per candle → axis + curve match the price chart
        // pixel-for-pixel across the whole period (no more scale "чехарда").
        const sorted = [...eq].sort((a, b) => a.time - b.time);
        const base = sorted[0].equity;
        let j = 0, lastEq = base;
        const aligned = bars.map(b => {
          while (j < sorted.length && sorted[j].time <= b.time) { lastEq = sorted[j].equity; j++; }
          return { time: b.time, value: lastEq };
        });
        equitySeries.applyOptions({ baseValue: { type: 'price', price: base } });
        equitySeries.setData(aligned);
      } else {
        equitySeries.setData([]);
      }

      stats = computeStats(fills, roundTrips, eq);
      // Money stats from the NET per-close PnLs (rubles, commission-deducted) in
      // `events`, so avg/max/min match the TP/SL analytics and the equity curve.
      if (stats) {
        const closes = events.filter(e => e.close).map(e => e.close!.pnl);
        if (closes.length) {
          stats = {
            ...stats,
            avgPerTrade: closes.reduce((a, b) => a + b, 0) / closes.length,
            maxProfit: Math.max(...closes),
            maxLoss: Math.min(...closes),
          };
        }
        netResult = closes.reduce((a, b) => a + b, 0);   // net of commission
      }
      // Broker vs exchange commission split (transparency).
      commission = commissionBreakdown(fills, pointValue, symbol, taker);
      // Per-trade rows for the trades table (one row per fill, with role + close PnL).
      tradeRows = events.map((e, i) => ({
        n: i + 1, time: e.rawTime, kind: e.kind, side: e.side, qty: e.qty, price: e.price,
        posAfter: e.posAfter,
        comm: commissionForFill(e.price, e.qty),
        pnl: e.close ? e.close.pnl : null,
        exit: e.close ? e.close.exit : null,
        label: e.label,
      }));

      // Fit all data into the view (equity mirrors via one-way logical sync).
      tvCandle.timeScale().fitContent();
      syncReady = true;
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
    {#if periodLabel}<span class="bt-period" title="Период теста">{periodLabel}</span>{/if}
    {#if strategy}
      <a class="bt-strategy" href={strategy.source} target="_blank" rel="noopener">{strategy.name} ↗</a>
    {/if}
    <span class="bt-legend">
      <span class="lg lg-long">▲ покупка</span><span class="lg lg-short">▼ продажа</span>
      <span class="lg lg-tp">■ TP</span><span class="lg lg-sl">■ SL</span>
    </span>
    <!-- Interval block pinned at the far right; the crosshair date/time is NOT
         here (it would shift these buttons). It lives in an on-chart overlay. -->
    <div class="bt-intervals">
      <button class="bt-fit" title="Показать весь период теста" onclick={fitAll}>Весь период</button>
      {#each INTERVALS as iv}
        <button class:active={resampleMin === iv.v} onclick={() => pickInterval(iv.v)}>{iv.label}</button>
      {/each}
    </div>
  </div>

  <div class="bt-candle-area">
    <div class="candle" bind:this={candleEl}></div>

    <!-- Editable params frame (top-left), collapsed until clicked. Edit a value and
         "Пересчитать" re-runs the backtest with the new params. -->
    <div class="bc-params" class:open={paramsOpen}
         onpointerdown={(e) => e.stopPropagation()} onwheel={(e) => e.stopPropagation()}>
      <button class="bc-params-h" onclick={() => paramsOpen = !paramsOpen}
              title="Параметры прогона — клик чтобы развернуть/свернуть">
        ⚙ Параметры {paramsOpen ? '▾' : '▸'}
      </button>
      {#if paramsOpen}
        <div class="bc-params-body">
          {#each editKeys as k}
            <label class="bc-prow" title={labelFor(k)}>
              <span class="bc-pk">{labelFor(k)}</span>
              {#if typeof params[k] === 'number'}
                <input class="bc-pv" type="number" step="any" bind:value={editParams[k]}
                       onkeydown={(e) => e.key === 'Enter' && applyParams()} />
              {:else}
                <input class="bc-pv" type="text" bind:value={editParams[k]}
                       onkeydown={(e) => e.key === 'Enter' && applyParams()} />
              {/if}
            </label>
          {/each}
          {#if onRerun}
            <button class="bc-apply" class:dirty={paramsDirty} onclick={applyParams}>
              Пересчитать бэктест
            </button>
          {/if}
        </div>
      {/if}
    </div>

    <!-- On-chart crosshair date/time, like TradingView/QUIK (shifted right to clear
         the params frame). -->
    {#if crossLabel}<div class="cross-overlay">{crossLabel}</div>{/if}

    {#if tip}
      <div class="trade-tip" style="left:{tip.x + 12}px; top:{tip.y - 8}px;">
        {#if tip.head}
          <div class="tt-head tt-{tip.headKind ?? 'neutral'}">{tip.head}</div>
        {/if}
        {#each tip.lines as l}
          <div class="tt-sub">{l}</div>
        {/each}
      </div>
    {/if}

    {#if stats}
      <div class="stats-overlay" class:open={statsExpanded}>
        <!-- collapsed: 2 lines. click to expand (frees up chart area). -->
        <button class="st-toggle" onclick={() => statsExpanded = !statsExpanded}
                title={statsExpanded ? 'Свернуть отчёт' : 'Развернуть отчёт'}>
          <div class="st-head">
            <span>Результат</span>
            <b class:pos={netResult > 0} class:neg={netResult < 0}>{fmtMoney(netResult)} ₽</b>
            <span class="st-chev">{statsExpanded ? '▴' : '▾'}</span>
          </div>
          <div class="st-head2">
            <span>{stats.roundTrips} сделок · комиссия {commission ? fmtRub(commission.total) : '—'}</span>
          </div>
        </button>

        {#if statsExpanded}
          <div class="st-body">
            <div class="st-row"><span>Всего сделок</span><b>{stats.roundTrips}</b>
              <span class="st-sub">(L {stats.longRT} / S {stats.shortRT})</span></div>
            <div class="st-row"><span>Макс. позиция</span><b>{stats.maxAbsPos} конт.</b>
              <span class="st-sub">ГО: {margin != null ? fmtMoney(stats.maxAbsPos * margin).replace('+','') + ' ₽' : '—'}</span></div>
            <div class="st-row"><span>Средн. на сделку</span>
              <b class:pos={stats.avgPerTrade > 0} class:neg={stats.avgPerTrade < 0}>{fmtMoney(stats.avgPerTrade)}</b></div>
            <div class="st-row"><span>Макс. прибыль</span><b class="pos">{fmtMoney(stats.maxProfit)}</b></div>
            <div class="st-row"><span>Макс. убыток</span><b class="neg">{fmtMoney(stats.maxLoss)}</b></div>
            <div class="st-row"><span>Фактор восст.</span><b>{stats.recovery != null ? stats.recovery.toFixed(2) : '—'}</b></div>

            {#if commission}
              <div class="st-sep"></div>
              <div class="st-row st-comm-h"><span>Комиссия ({taker ? 'тейкер' : 'мейкер'})</span><b class="neg">−{fmtRub(commission.total)}</b></div>
              <div class="st-row"><span>· брокеру (Finam 0,45/конт.)</span><b class="neg">−{fmtRub(commission.broker)}</b></div>
              <div class="st-row"><span>· бирже (MOEX{taker ? ` ${(commission.rate * 100).toFixed(4)}%` : ', мейкер 0'})</span><b class="neg">−{fmtRub(commission.exchange)}</b></div>
              <div class="st-row"><span>· филлов / контрактов</span><span class="st-sub">{commission.fills} / {commission.contracts}</span></div>
            {/if}

            {#if exits && (exits.tp + exits.sl) > 0}
              <div class="st-sep"></div>
              <div class="st-row"><span>Выходы TP / SL</span>
                <b><span class="pos">{exits.tp}</span> / <span class="neg">{exits.sl}</span></b>
                <span class="st-sub">{(exits.winRateByExit * 100).toFixed(0)}% TP</span></div>
              <div class="st-row"><span>· полные</span>
                <span class="st-sub">TP {exits.tpFull} / SL {exits.slFull}</span></div>
              <div class="st-row"><span>· частичные</span>
                <span class="st-sub">TP {exits.tpPartial} / SL {exits.slPartial}</span></div>
              <div class="st-row"><span>Прибыль TP</span><b class="pos">{fmtMoney(exits.tpPnl)}</b></div>
              <div class="st-row"><span>Убыток SL</span><b class="neg">{fmtMoney(exits.slPnl)}</b></div>
            {/if}

            <button class="st-trades-btn" onclick={() => showTrades = true}>Открыть таблицу сделок бэктеста →</button>
            <div class="st-foot">Суммы в ₽, чистыми (за вычетом комиссии {taker ? 'тейкер: биржа + брокер' : 'мейкер: только брокер'}).</div>
          </div>
        {/if}
      </div>
    {/if}

    <!-- full per-trade table (all details), opened from the report -->
    {#if showTrades}
      <div class="trades-pane">
        <div class="tp-head">
          <span class="tp-title">Сделки бэктеста · {symbol} · {tradeRows.length} филлов</span>
          <button class="tp-close" onclick={() => showTrades = false}>✕</button>
        </div>
        <div class="tp-wrap">
          <table class="tp-table">
            <thead>
              <tr><th>#</th><th>Время (UTC)</th><th>Тип</th><th>Сторона</th><th class="num">Кол.</th><th class="num">Цена</th><th class="num">Комиссия</th><th class="num">Поз. после</th><th class="num">Результат ₽</th></tr>
            </thead>
            <tbody>
              {#each tradeRows as r}
                <tr>
                  <td>{r.n}</td>
                  <td>{fmtTs(r.time)}</td>
                  <td>{KIND_RU[r.kind] ?? r.kind}</td>
                  <td class={r.side === 'buy' ? 'pos' : 'neg'}>{r.side === 'buy' ? 'покупка' : 'продажа'}</td>
                  <td class="num">{r.qty}</td>
                  <td class="num">{Math.round(r.price).toLocaleString('ru-RU')}</td>
                  <td class="num neg">−{fmtRub(r.comm)}</td>
                  <td class="num">{r.posAfter}</td>
                  <td class="num" class:pos={r.pnl > 0} class:neg={r.pnl < 0}>{r.pnl != null ? fmtMoney(r.pnl) : '—'}</td>
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
  .bt-period { font-size: 10px; color: #9ab; background: #12203a; border: 1px solid #24406a; border-radius: 3px; padding: 1px 7px; white-space: nowrap; flex-shrink: 0; }
  .bt-strategy { font-size: 11px; color: #6aa8ff; text-decoration: none; flex-shrink: 0; }
  .bt-strategy:hover { text-decoration: underline; }
  .bt-params { display: flex; gap: 4px; overflow: hidden; flex-shrink: 1; min-width: 0; }
  .bt-param { font-size: 10px; font-family: monospace; color: #888; background: #1a1a2e; border-radius: 2px; padding: 1px 5px; white-space: nowrap; }
  .bt-legend { display: flex; gap: 8px; font-size: 10px; flex-shrink: 0; }
  .lg-long { color: #2ee6a6; } .lg-short { color: #ff5c8a; }
  .lg-tp { color: #19e36a; } .lg-sl { color: #ff3b3b; }
  /* Interval selector pinned to the far right, never wraps, never moves. */
  .bt-intervals { display: flex; gap: 1px; flex-shrink: 0; margin-left: auto; }
  .bt-intervals button { background: transparent; color: #555; border: 1px solid transparent; font-size: 10px; padding: 2px 7px; border-radius: 3px; cursor: pointer; }
  .bt-intervals button:hover { color: #aaa; }
  .bt-intervals button.active { color: #4caf50; border-color: #4caf5066; background: #4caf5012; }
  .bt-intervals button.bt-fit { color: #9ab; border-color: #24406a; background: #12203a; margin-right: 6px; }
  .bt-intervals button.bt-fit:hover { color: #cfe; border-color: #6aa8ff66; }

  .bt-candle-area { position: relative; flex: 1; min-height: 0; }
  .candle { position: absolute; inset: 0; }

  /* On-chart crosshair date/time overlay — shifted right to clear the params frame. */
  .cross-overlay {
    position: absolute; top: 6px; left: 156px; z-index: 6;
    font-size: 11px; font-family: monospace; color: #6aa8ff;
    background: #0f0f1ecc; border: 1px solid #2d2d4a; border-radius: 3px;
    padding: 2px 7px; pointer-events: none; white-space: nowrap;
  }

  /* Editable params frame (top-left, collapsible). */
  .bc-params { position: absolute; top: 6px; left: 8px; z-index: 8; width: 142px;
    background: #0c0c18ee; border: 1px solid #2d2d4a; border-radius: 4px; overflow: hidden; }
  .bc-params.open { box-shadow: 0 6px 22px rgba(0,0,0,0.5); }
  .bc-params-h { width: 100%; text-align: left; background: #14223a; border: none;
    color: #cde; font-size: 11px; padding: 4px 8px; cursor: pointer; }
  .bc-params-h:hover { background: #1a2b48; }
  .bc-params-body { display: flex; flex-direction: column; gap: 3px; padding: 6px; max-height: 60vh; overflow-y: auto; }
  .bc-prow { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .bc-pk { font-size: 9px; color: #9ab; line-height: 1.1; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bc-pv { width: 52px; flex-shrink: 0; background: #0a1120; border: 1px solid #24406a; color: #cfe;
    font-size: 10px; border-radius: 3px; padding: 1px 4px; text-align: right; }
  .bc-pv:focus { outline: none; border-color: #4a7ad0; }
  .bc-apply { margin-top: 4px; background: #1f5e3a; border: 1px solid #2e8b57; color: #cfe;
    font-size: 10px; border-radius: 3px; padding: 3px 6px; cursor: pointer; }
  .bc-apply:hover { background: #267346; }
  .bc-apply.dirty { background: #8a5a1f; border-color: #c8862f; }

  .trade-tip {
    position: absolute; z-index: 8; pointer-events: none;
    background: #12121fee; border: 1px solid #3d3d5a; border-radius: 4px;
    padding: 5px 8px; font-size: 10px; white-space: nowrap;
    box-shadow: 0 4px 12px #000000aa;
  }
  .tt-head { color: #fff; font-weight: 700; margin-bottom: 3px; font-size: 11px; }
  .tt-head.tt-tp { color: #19e36a; }
  .tt-head.tt-sl { color: #ff3b3b; }
  .tt-head.tt-neutral { color: #fff; }
  .tt-sub { color: #aaa; font-family: monospace; }

  .stats-overlay {
    position: absolute; top: 6px; right: 92px; z-index: 5;
    background: #0f0f1ed9; border: 1px solid #2d2d4a; border-radius: 4px;
    padding: 5px 7px; display: flex; flex-direction: column; gap: 2px;
    backdrop-filter: blur(2px); min-width: 210px; max-width: 260px;
  }
  /* collapsed 2-line header (default) — clickable to expand */
  .st-toggle { display: block; width: 100%; background: none; border: none; padding: 0; cursor: pointer; text-align: left; }
  .st-head { display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: #999; }
  .st-head span:first-child { flex: 1; }
  .st-head b { font-size: 13px; }
  .st-chev { color: #6aa8ff; font-size: 11px; }
  .st-head2 { font-size: 9px; color: #667; margin-top: 1px; }
  .st-body { display: flex; flex-direction: column; gap: 2px; margin-top: 5px; border-top: 1px solid #2d2d4a; padding-top: 5px; }
  .st-row { display: flex; align-items: baseline; gap: 6px; font-size: 10px; color: #888; }
  .st-row span:first-child { flex: 1; }
  .st-row b { color: #ccc; font-size: 11px; }
  .st-comm-h b { font-size: 12px; }
  .st-sub { color: #555; font-size: 9px; }
  .st-sep { height: 1px; background: #2d2d4a; margin: 3px 0; }
  .st-trades-btn { margin-top: 6px; padding: 4px 8px; background: #12203a; border: 1px solid #24406a; color: #9cf; border-radius: 3px; font-size: 10px; cursor: pointer; }
  .st-trades-btn:hover { border-color: #6aa8ff66; color: #cfe; }
  .st-foot { font-size: 8px; color: #555; margin-top: 4px; font-style: italic; }
  .pos { color: #4caf50; } .neg { color: #f44336; }

  /* full trades table overlay */
  .trades-pane { position: absolute; inset: 0; z-index: 12; background: #0a0a15f2; display: flex; flex-direction: column; }
  .tp-head { display: flex; align-items: center; justify-content: space-between; padding: 7px 10px; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .tp-title { font-size: 12px; color: #cde; font-weight: 600; }
  .tp-close { width: 24px; height: 24px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; cursor: pointer; }
  .tp-close:hover { color: #f44336; border-color: #f4433655; }
  .tp-wrap { flex: 1; overflow: auto; }
  .tp-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .tp-table th { position: sticky; top: 0; background: #0c0c18; color: #789; font-weight: 500; text-align: left; padding: 5px 10px; border-bottom: 1px solid #1e1e3a; white-space: nowrap; }
  .tp-table th.num, .tp-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .tp-table td { padding: 4px 10px; color: #aaa; border-bottom: 1px solid #14142a; white-space: nowrap; }
  .tp-table tr:hover td { background: #12122a; }

  .bt-equity-label {
    padding: 3px 10px; font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;
    background: #0f0f1e; border-top: 1px solid #1a1a2e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  }
  .equity { flex: 0 0 22%; min-height: 0; }

  .bt-scrollbar {
    position: relative; height: 14px; margin: 4px 10px; flex-shrink: 0;
    background: #1a1a2e; border: 1px solid #3a3a5a; border-radius: 7px;
  }
  .bt-thumb {
    position: absolute; top: 1px; bottom: 1px; min-width: 24px;
    background: #4a4a6e; border-radius: 6px; cursor: grab;
  }
  .bt-thumb:hover { background: #4caf5088; }
  .bt-thumb:active { cursor: grabbing; background: #4caf50aa; }

  .bt-hint { padding: 2px 10px; font-size: 9px; color: #555; background: #0f0f1e; border-top: 1px solid #1a1a2e; flex-shrink: 0; }

  .overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: #0a0a15cc; z-index: 10; font-size: 12px; color: #666; }
  .overlay.error { color: #f4433699; }
</style>
