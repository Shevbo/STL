<!-- frontend/src/components/OrderViz.svelte
  Compact order-visualization frame (~1/8 of the main field). When the operator
  places a QUIK order, this auto-shows a small frame with BOTH a price chart AND
  the order book (стакан) for the focus instrument, with the placed order(s)
  highlighted + animated at their price level.

  - Polls /api/v1/quik/orders/working (~1s). "Active" = state in
    {pending, active, partial}. Frame AUTO-SHOWS when >=1 active order, hides when
    none. Focus instrument = code of the most-recently-placed active order; a tiny
    selector appears when several instruments have active orders.
  - LEFT pane: compact стакан for the focus code (poll /api/v1/quik/orderbook/{code}).
    Asks descending above, bids below, mid divider. Rows that match an active
    order's price are highlighted (green buy / red sell) with a CSS pulse + a side
    marker and remaining-qty badge.
  - RIGHT pane: compact lightweight-charts area built from the order-book mid
    ((best_bid+best_ask)/2) accumulated client-side (last 300 points). A
    createPriceLine per active order (buy green / sell red), titled
    "side qty @price (state)". A MOVE/replace (price change) animates as
    remove + re-add.

  Frontend only. No backend changes. No live orders placed here. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { mskTickFormatter, mskCrosshairFormatter } from '$lib/chart-time';

  type OrderRow = {
    client_id: string; code: string; side: string; price: number;
    quantity: number; filled: number; remaining?: number; state: string;
    order_id: string; ts_unix_ms: number; agent_id?: string;
  };
  type Level = { price: number; quantity: number };
  type Book = { bids: Level[]; asks: Level[] };

  let {
    pinned = false,
    onClose = undefined,
  }: {
    // pinned = operator forced it open (stays even with no active orders).
    pinned?: boolean;
    onClose?: (() => void) | undefined;
  } = $props();

  const ACTIVE = new Set(['pending', 'active', 'partial']);
  const BUY = '#4caf50';
  const SELL = '#f44336';
  const MAX_MID = 300;            // cap the mid buffer
  const LADDER_LEVELS = 7;        // compact: ~7 asks + 7 bids

  let orders = $state<OrderRow[]>([]);
  let book = $state<Book>({ bids: [], asks: [] });
  let focusCode = $state<string>('');
  // codes that currently have >=1 active order (for the tiny selector)
  let activeCodes = $state<string[]>([]);

  // Active orders for the focus instrument only.
  let focusOrders = $derived(
    orders.filter(o => ACTIVE.has(o.state) && o.code === focusCode),
  );
  let anyActive = $derived(orders.some(o => ACTIVE.has(o.state)));
  // Visible when there is at least one active order, or the operator pinned it.
  let visible = $derived(pinned || anyActive);

  // Per-price highlight info for the ladder: price -> {side, remaining}.
  let highlightByPrice = $derived.by(() => {
    const m = new Map<number, { side: string; remaining: number }>();
    for (const o of focusOrders) {
      const rem = o.remaining ?? (o.quantity - (o.filled ?? 0));
      const prev = m.get(o.price);
      // Multiple orders at the same price + side → sum the remaining qty.
      if (prev && prev.side === o.side) prev.remaining += rem;
      else m.set(o.price, { side: o.side, remaining: rem });
    }
    return m;
  });

  let asksShown = $derived(book.asks.slice(0, LADDER_LEVELS).reverse());  // descending
  let bidsShown = $derived(book.bids.slice(0, LADDER_LEVELS));            // best first
  let maxQty = $derived(
    Math.max(
      1,
      ...book.asks.slice(0, LADDER_LEVELS).map(a => a.quantity),
      ...book.bids.slice(0, LADDER_LEVELS).map(b => b.quantity),
    ),
  );
  let spread = $derived(
    book.bids.length && book.asks.length
      ? (book.asks[0].price - book.bids[0].price)
      : null,
  );

  function fmt(p: number): string {
    return p.toLocaleString('ru-RU', { maximumFractionDigits: 4 });
  }

  // ── polling ────────────────────────────────────────────────────────────────
  async function loadOrders() {
    try {
      const r = await fetch('/api/v1/quik/orders/working', { credentials: 'include' });
      if (!r.ok) return;
      const rows: OrderRow[] = (await r.json()).orders ?? [];
      orders = rows;
      const act = rows.filter(o => ACTIVE.has(o.state));
      // distinct codes with active orders
      activeCodes = [...new Set(act.map(o => o.code))];
      // Focus = most-recently-placed active order's code; keep the operator's
      // manual pick if it still has an active order.
      if (act.length) {
        if (!focusCode || !activeCodes.includes(focusCode)) {
          const latest = act.reduce((a, b) => (a.ts_unix_ms >= b.ts_unix_ms ? a : b));
          focusCode = latest.code;
        }
      }
    } catch { /* keep previous */ }
  }

  async function loadBook() {
    if (!focusCode) { book = { bids: [], asks: [] }; return; }
    try {
      const r = await fetch(
        `/api/v1/quik/orderbook/${encodeURIComponent(focusCode)}`,
        { credentials: 'include' },
      );
      if (!r.ok) return;
      const ob = await r.json();
      book = { bids: ob.bids ?? [], asks: ob.asks ?? [] };
    } catch { /* keep previous */ }
  }

  // ── chart (lightweight-charts area built from mid buffer) ───────────────────
  let chartEl: HTMLDivElement;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let tvChart: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let midSeries: any = null;
  let chartReady = $state(false);
  // mid buffer keyed by code so switching focus shows that instrument's own line
  let midBuf = new Map<string, { time: number; value: number }[]>();
  let lastMidT = 0;
  // order_id -> { line, price } so a MOVE (price change) animates as remove+re-add
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let orderLines = new Map<string, { line: any; price: number }>();
  // order_ids whose line just moved → brief flash class on the badge in the ladder
  let movedIds = $state<Set<string>>(new Set());

  function pushMid() {
    if (!focusCode || spread === null) return;
    const mid = (book.asks[0].price + book.bids[0].price) / 2;
    // one point per ~second; monotonic time for lightweight-charts
    let t = Math.floor(Date.now() / 1000);
    if (t <= lastMidT) t = lastMidT + 1;
    lastMidT = t;
    const buf = midBuf.get(focusCode) ?? [];
    buf.push({ time: t, value: mid });
    if (buf.length > MAX_MID) buf.shift();
    midBuf.set(focusCode, buf);
    if (midSeries) midSeries.setData(buf);
  }

  function redrawOrderLines() {
    if (!midSeries) return;
    const current = new Set(focusOrders.map(o => o.order_id));
    // remove lines for orders no longer active / not on focus instrument
    for (const [id, rec] of orderLines) {
      if (!current.has(id)) {
        try { midSeries.removePriceLine(rec.line); } catch { /* gone */ }
        orderLines.delete(id);
      }
    }
    const flashed = new Set<string>();
    for (const o of focusOrders) {
      const rem = o.remaining ?? (o.quantity - (o.filled ?? 0));
      const color = o.side === 'buy' ? BUY : SELL;
      const title = `${o.side === 'buy' ? 'BUY' : 'SELL'} ${rem} @ ${fmt(o.price)} (${o.state})`;
      const existing = orderLines.get(o.order_id);
      if (!existing) {
        const line = midSeries.createPriceLine({
          price: o.price, color, lineWidth: 2, lineStyle: 0,
          axisLabelVisible: true, title,
        });
        orderLines.set(o.order_id, { line, price: o.price });
      } else if (existing.price !== o.price) {
        // MOVE / replace → animate the transition: remove the old line and re-add
        // it at the new price, and flash the ladder badge.
        try { midSeries.removePriceLine(existing.line); } catch { /* gone */ }
        const line = midSeries.createPriceLine({
          price: o.price, color, lineWidth: 2, lineStyle: 0,
          axisLabelVisible: true, title,
        });
        orderLines.set(o.order_id, { line, price: o.price });
        flashed.add(o.order_id);
      } else {
        // same price: refresh title (state / remaining may have changed)
        existing.line.applyOptions({ title, color });
      }
    }
    if (flashed.size) {
      movedIds = flashed;
      setTimeout(() => { movedIds = new Set(); }, 700);
    }
  }

  // Clear chart state when the focus instrument changes.
  function resetChartFor(code: string) {
    for (const [, rec] of orderLines) {
      try { midSeries?.removePriceLine(rec.line); } catch { /* gone */ }
    }
    orderLines.clear();
    lastMidT = 0;
    const buf = midBuf.get(code) ?? [];
    if (midSeries) midSeries.setData(buf);
  }

  let lastFocus = '';
  $effect(() => {
    if (focusCode !== lastFocus && chartReady) {
      lastFocus = focusCode;
      resetChartFor(focusCode);
    }
  });

  // Redraw price lines whenever the focus orders change.
  $effect(() => {
    focusOrders;
    if (chartReady) redrawOrderLines();
  });

  onMount(() => {
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let disposed = false;

    (async () => {
      await loadOrders();
      await loadBook();
      const { createChart } = await import('lightweight-charts');
      if (disposed || !chartEl) return;
      tvChart = createChart(chartEl, {
        width: chartEl.clientWidth || 300,
        height: chartEl.clientHeight || 130,
        layout: { background: { color: '#0f0f1e' }, textColor: '#778' },
        grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
        localization: { timeFormatter: mskCrosshairFormatter },
        timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 4, tickMarkFormatter: mskTickFormatter },
        rightPriceScale: { borderColor: '#2d2d4a', autoScale: true },
        crosshair: { mode: 1 },
        handleScroll: false, handleScale: false,
      });
      midSeries = tvChart.addAreaSeries({
        lineColor: '#6aa8ff', topColor: '#6aa8ff33', bottomColor: '#6aa8ff05',
        lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
      });
      const buf = midBuf.get(focusCode) ?? [];
      if (buf.length) midSeries.setData(buf);
      chartReady = true;
      lastFocus = focusCode;
      redrawOrderLines();

      const ro = new ResizeObserver(() => {
        if (!tvChart || !chartEl) return;
        tvChart.applyOptions({
          width: chartEl.clientWidth, height: Math.max(chartEl.clientHeight, 60),
        });
      });
      ro.observe(chartEl);
      roRef = ro;

      // ~1s poll loop: orders + book + mid accumulation.
      pollTimer = setInterval(async () => {
        await loadOrders();
        await loadBook();
        pushMid();
      }, 1000);
      // one immediate mid sample from the initial book
      pushMid();
    })();

    return () => { disposed = true; if (pollTimer) clearInterval(pollTimer); };
  });

  let roRef: ResizeObserver | null = null;
  onDestroy(() => {
    roRef?.disconnect();
    tvChart?.remove();
  });
</script>

{#if visible}
  <div class="oviz">
    <div class="oviz-head">
      <span class="ov-title">Визуализация заявки</span>
      {#if activeCodes.length > 1}
        <select class="ov-sel" bind:value={focusCode} title="Инструмент с активной заявкой">
          {#each activeCodes as c}
            <option value={c}>{c}</option>
          {/each}
        </select>
      {:else}
        <span class="ov-code">{focusCode || '—'}</span>
      {/if}
      <span class="ov-spread">
        {#if spread !== null}спред {fmt(spread)}{:else}нет стакана{/if}
      </span>
      <span class="ov-count">{focusOrders.length} акт.</span>
      {#if onClose}
        <button class="ov-close" title="Скрыть" onclick={onClose}>✕</button>
      {/if}
    </div>

    <div class="oviz-body">
      <!-- LEFT: compact ladder -->
      <div class="ov-ladder">
        {#if !book.asks.length && !book.bids.length}
          <div class="ov-empty">Ожидание стакана…</div>
        {:else}
          <div class="ov-asks">
            {#each asksShown as lv (lv.price)}
              {@const hl = highlightByPrice.get(lv.price)}
              <div
                class="ov-row ask"
                class:hl={!!hl}
                class:hl-buy={hl?.side === 'buy'}
                class:hl-sell={hl?.side === 'sell'}
              >
                <div class="ov-bar ask-bar" style="width:{(lv.quantity / maxQty) * 100}%"></div>
                {#if hl}
                  <span class="ov-mark" class:buy={hl.side === 'buy'} class:sell={hl.side === 'sell'}>
                    {hl.side === 'buy' ? '▲' : '▼'}
                  </span>
                  <span class="ov-badge" class:buy={hl.side === 'buy'} class:sell={hl.side === 'sell'}>
                    {hl.remaining}
                  </span>
                {/if}
                <span class="ov-q">{lv.quantity}</span>
                <span class="ov-p ask-p">{fmt(lv.price)}</span>
              </div>
            {/each}
          </div>
          <div class="ov-mid">
            {#if spread !== null}мид {fmt((book.asks[0].price + book.bids[0].price) / 2)}{:else}—{/if}
          </div>
          <div class="ov-bids">
            {#each bidsShown as lv (lv.price)}
              {@const hl = highlightByPrice.get(lv.price)}
              <div
                class="ov-row bid"
                class:hl={!!hl}
                class:hl-buy={hl?.side === 'buy'}
                class:hl-sell={hl?.side === 'sell'}
              >
                <div class="ov-bar bid-bar" style="width:{(lv.quantity / maxQty) * 100}%"></div>
                {#if hl}
                  <span class="ov-mark" class:buy={hl.side === 'buy'} class:sell={hl.side === 'sell'}>
                    {hl.side === 'buy' ? '▲' : '▼'}
                  </span>
                  <span class="ov-badge" class:buy={hl.side === 'buy'} class:sell={hl.side === 'sell'}>
                    {hl.remaining}
                  </span>
                {/if}
                <span class="ov-q">{lv.quantity}</span>
                <span class="ov-p bid-p">{fmt(lv.price)}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <!-- RIGHT: compact mid chart with order price lines -->
      <div class="ov-chart" bind:this={chartEl}></div>
    </div>
  </div>
{/if}

<style>
  .oviz {
    display: flex; flex-direction: column; height: 100%;
    background: #14142a; color: #ccc; overflow: hidden;
    font-size: 11px; font-family: 'JetBrains Mono', 'Consolas', monospace;
  }
  .oviz-head {
    display: flex; align-items: center; gap: 8px;
    padding: 3px 8px; background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    flex-shrink: 0;
  }
  .ov-title { color: #9ab; font-weight: 600; }
  .ov-code { color: #4caf50; font-weight: 600; }
  .ov-sel {
    background: #0f0f1e; color: #4caf50; border: 1px solid #2d2d4a;
    font-size: 11px; padding: 1px 4px; border-radius: 3px;
  }
  .ov-spread { color: #667; font-size: 10px; }
  .ov-count { color: #ffd27f; font-size: 10px; }
  .ov-close {
    margin-left: auto; background: transparent; color: #888;
    border: 1px solid #2d2d4a; border-radius: 3px; cursor: pointer;
    width: 18px; height: 18px; line-height: 1; padding: 0;
  }
  .ov-close:hover { color: #f44336; border-color: #5a2020; }

  .oviz-body { flex: 1; display: flex; min-height: 0; overflow: hidden; }

  /* LEFT ladder */
  .ov-ladder {
    width: 200px; flex-shrink: 0; display: flex; flex-direction: column;
    background: #0d0d1c; border-right: 1px solid #2d2d4a; min-height: 0;
    overflow: hidden;
  }
  .ov-empty { padding: 16px 8px; color: #444; text-align: center; font-size: 10px; }
  .ov-asks { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; overflow: hidden; }
  .ov-bids { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .ov-mid {
    text-align: center; color: #889; font-size: 9px; padding: 1px 0;
    border-top: 1px solid #1e1e3a; border-bottom: 1px solid #1e1e3a; background: #111120;
    flex-shrink: 0;
  }
  .ov-row {
    position: relative; display: flex; align-items: center; gap: 3px;
    height: 15px; padding: 0 5px; overflow: hidden;
  }
  .ov-bar { position: absolute; top: 0; right: 0; bottom: 0; opacity: 0.16; pointer-events: none; }
  .ask-bar { background: #f44336; }
  .bid-bar { background: #4caf50; }
  .ov-q { color: #666; min-width: 26px; text-align: right; z-index: 1; font-size: 10px; }
  .ov-p { flex: 1; text-align: right; z-index: 1; font-weight: 500; }
  .ask-p { color: #f44336; }
  .bid-p { color: #4caf50; }
  .ov-mark { z-index: 1; font-size: 9px; }
  .ov-mark.buy { color: #4caf50; }
  .ov-mark.sell { color: #f44336; }
  .ov-badge {
    z-index: 1; font-size: 9px; font-weight: 700; border-radius: 3px;
    padding: 0 4px; color: #0d0d1c;
  }
  .ov-badge.buy { background: #4caf50; }
  .ov-badge.sell { background: #f44336; }

  /* highlighted level: pulse/blink animation */
  .ov-row.hl-buy {
    background: #4caf5022;
    box-shadow: inset 2px 0 0 #4caf50;
    animation: pulseBuy 1.1s ease-in-out infinite;
  }
  .ov-row.hl-sell {
    background: #f4433622;
    box-shadow: inset 2px 0 0 #f44336;
    animation: pulseSell 1.1s ease-in-out infinite;
  }
  @keyframes pulseBuy {
    0%, 100% { background: #4caf5018; }
    50% { background: #4caf5040; }
  }
  @keyframes pulseSell {
    0%, 100% { background: #f4433618; }
    50% { background: #f4433640; }
  }

  /* RIGHT chart */
  .ov-chart { flex: 1; min-width: 0; position: relative; }
</style>
