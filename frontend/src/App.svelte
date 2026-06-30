<!-- frontend/src/App.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ActiveOrdersPanel from './components/ActiveOrdersPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import InstrumentPanel from './components/InstrumentPanel.svelte';
  import OrderPanel from './components/OrderPanel.svelte';
  import OrderConfirmDialog from './components/OrderConfirmDialog.svelte';
  import PositionsTable from './components/PositionsTable.svelte';
  import OrderBook from './components/OrderBook.svelte';
  import BottomBar from './components/BottomBar.svelte';
  import { startQuikOrderbookBridge } from './lib/quik_md';
  import LabPanel from './components/LabPanel.svelte';
  import QuikTables from './components/QuikTables.svelte';
  import Orders from './components/Orders.svelte';
  import OrderViz from './components/OrderViz.svelte';
  import ChartsGrid from './components/ChartsGrid.svelte';
  import LoginDialog from './components/LoginDialog.svelte';
  import { WsClient } from '$lib/ws';
  import { robotsStore } from '$lib/stores/robots.svelte';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import { positionsStore } from '$lib/stores/positions.svelte';
  import { instrumentStore } from '$lib/stores/instrument.svelte';
  import { placeOrder, loadFeeConfig } from '$lib/api';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import type { OrderRequest } from '$lib/types';

  let authed = $state(false);
  // Session check is in flight on first load. While checking we show a neutral splash
  // (NOT the login form) so a normal F5 with a valid cookie does not flash the login
  // screen. The login form only appears after the check completes with a real 401.
  let checking = $state(true);
  let showLab = $state(false);
  let showQuikTables = $state(false);
  let showQuikOrders = $state(false);
  let showCharts = $state(false);
  // OrderViz: default = auto (self-shows on active orders). Operator can pin it
  // open or hide it; "pin" forces it visible even with no active orders.
  let orderVizPinned = $state(false);
  let orderVizHidden = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let events = $state<string[]>([]);
  let pendingOrder = $state<OrderRequest | null>(null);

  // п.1: активный символ — поднят из ChartFrame в App
  let activeSymbol = $state<string>('');

  // ── Resizable panel sizes (px, persisted to localStorage) ──────────────────
  const LS_KEY = 'stl_panel_sizes';
  function loadSizes(): Record<string, number> {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch { return {}; }
  }
  function saveSizes(s: Record<string, number>) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(s)); } catch {}
  }
  let saved = loadSizes();
  let leftW = $state(saved.leftW || 200);
  let rightW = $state(saved.rightW || 190);
  let labH = $state(saved.labH || 340);
  let posH = $state(saved.posH || 180);
  let bookW = $state(saved.bookW || 220);
  let leftTopPct = $state(saved.leftTopPct || 60);   // % for RobotsPanel
  let rightTopPct = $state(saved.rightTopPct || 55);  // % for InstrumentPanel

  // ── Drag state ────────────────────────────────────────────────────────────
  let dragHandle = $state<string | null>(null);
  let dragStart = $state({ x: 0, y: 0, val: 0, val2: 0 });

  function onPointerDown(handle: string, e: PointerEvent, cur: number, cur2 = 0) {
    dragHandle = handle;
    dragStart = { x: e.clientX, y: e.clientY, val: cur, val2: cur2 };
    try { (e.target as HTMLElement).setPointerCapture(e.pointerId); } catch { /* ignore */ }
    // Listen on window for the whole drag: when the cursor crosses the chart
    // canvas (lightweight-charts captures pointer events), .shell stops getting
    // pointermove and the vertical chart/positions border would freeze otherwise.
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    e.preventDefault();
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragHandle) return;
    const dx = e.clientX - dragStart.x;
    const dy = e.clientY - dragStart.y;
    switch (dragHandle) {
      case 'left': leftW = clamp(dragStart.val + dx, 120, 420); break;
      case 'right': rightW = clamp(dragStart.val - dx, 130, 420); break;
      case 'book': bookW = clamp(dragStart.val - dx, 140, 500); break;
      case 'pos': posH = clamp(dragStart.val - dy, 80, 400); break;
      case 'lab': labH = clamp(dragStart.val - dy, 120, 700); break;
      case 'leftSplit': {
        const el = document.querySelector('.left-col') as HTMLElement | null;
        if (!el) break;
        const h = el.offsetHeight - 4;
        leftTopPct = clamp(Math.round((dragStart.val2 + dy) / h * 100), 15, 85);
        break;
      }
      case 'rightSplit': {
        const el = document.querySelector('.right-col') as HTMLElement | null;
        if (!el) break;
        const h = el.offsetHeight - 4;
        rightTopPct = clamp(Math.round((dragStart.val2 + dy) / h * 100), 15, 85);
        break;
      }
    }
  }

  function onPointerUp(_e: PointerEvent) {
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
    if (!dragHandle) return;
    dragHandle = null;
    // persist
    saveSizes({ leftW, rightW, labH, posH, bookW, leftTopPct, rightTopPct });
  }

  function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, Math.round(v))); }

  // Сбрасывать activeSymbol при смене робота
  $effect(() => {
    const _ = selectedRobotId;
    activeSymbol = '';
  });

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);
  let positions = $derived(positionsStore.all);

  // п.1: effectiveSymbol — то, что реально показано на экране.
  // Защита от мёртвых контрактов: робот может объявлять истёкший контракт (напр.
  // июньский GZM6 в имени), что даёт пустой график и мёртвую подписку в Finam-фид.
  // Если символ не в живом списке инструментов — детерминированно катим месяц M6->U6
  // (тот же инструмент, активный квартал); если и это не живое — берём первый живой.
  let liveSymbols = $derived(new Set(instrumentStore.list.map(i => i.symbol)));
  function liveify(s: string): string {
    if (!s || !liveSymbols.size || liveSymbols.has(s)) return s;
    const rolled = s.replace(/M6(@|$)/, 'U6$1'); // June -> September (current active quarter)
    if (liveSymbols.has(rolled)) return rolled;
    return instrumentStore.list[0]?.symbol ?? s;
  }
  let effectiveSymbol = $derived(liveify(activeSymbol || selectedRobot?.symbol || ''));
  let currentQuote = $derived(quotesStore.get(effectiveSymbol));

  // QUIK market-data bridge: feed the QUIK agent's order book into orderbookStore so the
  // main стакан shows QUIK instruments (GZU6 etc.). Started once; reads effectiveSymbol
  // each tick. getSymbol closure is not tracked here, so this effect does not re-run.
  $effect(() => {
    const stop = startQuikOrderbookBridge(() => effectiveSymbol);
    return stop;
  });

  let ws: WsClient;

  function onLogin() {
    authed = true;
    startWs();
    loadInstruments();
  }

  function startWs() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WsClient(`${proto}//${window.location.host}/ws`);
    ws.connect();
  }

  async function loadInstruments() {
    try {
      const res = await fetchWithAuth('/api/v1/instruments');
      if (!res.ok) return;
      const data = await res.json() as { instruments: { symbol: string; ticker: string; name: string }[] };
      instrumentStore.setList(data.instruments ?? []);
    } catch {
      // Non-critical: instrument list may fail, chart still works with current symbol
    }
  }

  // п.1: fetch params when effective symbol changes
  $effect(() => {
    if (!effectiveSymbol || !authed) return;
    fetchWithAuth(`/api/v1/instruments/${encodeURIComponent(effectiveSymbol)}/params`)
      .then(r => r.ok ? r.json() as Promise<Record<string, unknown>> : null)
      .then(data => { if (data) instrumentStore.setParams(data); })
      .catch(() => {});
  });

  function handleSubscribe(sym: string, timeframe: number) {
    // п.1: сохранить выбранный символ и отправить WS-запрос
    activeSymbol = sym;
    ws?.send({ type: 'subscribe', symbol: sym, timeframe });
  }

  onMount(async () => {
    // Resolve the session before deciding login vs app. A network error / transient
    // server stall during reload must NOT drop the operator to the login screen with a
    // valid 30-day cookie: retry a few times, and only treat an explicit 401 as
    // "not authenticated". Anything else keeps us on the splash and retries.
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const res = await fetchWithAuth('/api/auth/me');
        if (res.ok) {
          authed = true;
          break;
        }
        if (res.status === 401) {
          // Genuinely not logged in — show the login form.
          break;
        }
        // 5xx / unexpected: server busy during reload, retry.
      } catch {
        // network error / aborted: retry rather than bounce to login.
      }
      await new Promise((r) => setTimeout(r, 800));
    }
    checking = false;
    if (authed) {
      startWs();
      loadInstruments();
      loadFeeConfig();
    }
  });
  onDestroy(() => {
    ws?.disconnect();
  });

  async function handleConfirmOrder(order: OrderRequest): Promise<void> {
    try {
      const resp = await placeOrder(order);
      events = [...events, `${new Date().toLocaleTimeString()} Заявка ${resp.order_id}: ${resp.status}`];
    } catch (e) {
      events = [...events, `${new Date().toLocaleTimeString()} Ошибка заявки: ${e}`];
    } finally {
      pendingOrder = null;
    }
  }

  // п.6: клик по стакану → открыть диалог заявки с quantity=1
  function handleBookOrder(partial: Omit<OrderRequest, 'quantity'>): void {
    pendingOrder = { ...partial, quantity: 1 };
  }
</script>

{#if checking}
  <div class="session-splash">
    <div class="ss-card">
      <div class="ss-spinner"></div>
      <div class="ss-text">Проверка сессии…</div>
    </div>
  </div>
{:else if !authed}
  <LoginDialog {onLogin} />
{:else}
<div class="shell" onpointermove={onPointerMove} onpointerup={onPointerUp} onpointerleave={onPointerUp}>
  <TopBar
    {showLab}
    onToggleLabPanel={() => showLab = !showLab}
    {showQuikTables}
    onToggleQuikTables={() => showQuikTables = !showQuikTables}
    {showQuikOrders}
    onToggleQuikOrders={() => showQuikOrders = !showQuikOrders}
    {showCharts}
    onToggleCharts={() => showCharts = !showCharts}
  />
  <div class="body">
    <!-- LEFT COLUMN -->
    <div class="left-col" style="width:{leftW}px">
      <div class="l-top" style="flex:{leftTopPct}">
        <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
      </div>
      <!-- left-col vertical split handle -->
      <div class="dh dh-h" title="Размер панелей"
           onpointerdown={(e) => { const top = (e.currentTarget as HTMLElement).previousElementSibling as HTMLElement | null; onPointerDown('leftSplit', e, 0, top?.offsetHeight ?? 0); }}>
        <div class="dh-dot"></div>
      </div>
      <div class="l-bot" style="flex:{100 - leftTopPct}">
        <ActiveOrdersPanel />
      </div>
      <!-- left-col right-edge handle -->
      <div class="dh dh-v dh-right" title="Ширина колонки"
           onpointerdown={(e) => onPointerDown('left', e, leftW)}></div>
    </div>

    <!-- CENTER COLUMN -->
    <div class="center-col">
      <div class="chart-book-row" style="flex:1">
        <main class="content" style="flex:1">
          {#if effectiveSymbol}
            <ChartFrame
              symbol={effectiveSymbol}
              onSubscribe={handleSubscribe}
            />
          {/if}
        </main>
        <!-- chart/orderbook split handle -->
        <div class="dh dh-v" title="Ширина стакана"
             onpointerdown={(e) => onPointerDown('book', e, bookW)}></div>
        {#if effectiveSymbol}
          <div style="width:{bookW}px; flex-shrink:0; overflow:hidden">
            <OrderBook
              symbol={effectiveSymbol}
              onOpenOrder={handleBookOrder}
            />
          </div>
        {/if}
      </div>
      <!-- positions split handle -->
      <div class="dh dh-h" title="Высота таблицы позиций"
           onpointerdown={(e) => onPointerDown('pos', e, posH)}>
        <div class="dh-dot"></div>
      </div>
      <div class="positions-wrap" style="height:{posH}px">
        <PositionsTable {positions} />
      </div>
    </div>

    <!-- right-column left-edge handle -->
    <div class="dh dh-v dh-right" title="Ширина правой колонки"
         onpointerdown={(e) => onPointerDown('right', e, rightW)}></div>
    <!-- RIGHT COLUMN -->
    <div class="right-col" style="width:{rightW}px">
      <div class="r-top" style="flex:{rightTopPct}">
        <InstrumentPanel symbol={effectiveSymbol} />
      </div>
      <!-- right-col vertical split handle -->
      <div class="dh dh-h" title="Размер панелей"
           onpointerdown={(e) => { const top = (e.currentTarget as HTMLElement).previousElementSibling as HTMLElement | null; onPointerDown('rightSplit', e, 0, top?.offsetHeight ?? 0); }}>
        <div class="dh-dot"></div>
      </div>
      <div class="r-bot" style="flex:{100 - rightTopPct}">
        <OrderPanel
          symbol={effectiveSymbol}
          quote={currentQuote}
          onSubmit={(order) => pendingOrder = order}
        />
      </div>
    </div>
  </div>
  {#if showLab}
    <!-- lab panel top-edge handle -->
    <div class="dh dh-h" title="Высота панели LAB"
         onpointerdown={(e) => onPointerDown('lab', e, labH)}>
      <div class="dh-dot"></div>
    </div>
    <div class="lab-panel-wrap" style="height:{labH}px">
      <LabPanel />
    </div>
  {/if}
  {#if showQuikTables}
    <div class="quik-tables-wrap" style="height:{labH}px">
      <QuikTables />
    </div>
  {/if}
  {#if showQuikOrders}
    <div class="quik-tables-wrap" style="height:{labH}px">
      <Orders />
    </div>
  {/if}
  {#if showCharts}
    <div class="quik-tables-wrap" style="height:{labH}px">
      <ChartsGrid />
    </div>
  {/if}
  <!-- OrderViz: slim auto-frame (~1/8 height). Self-shows when there is >=1 active
       QUIK order; the pin button keeps it open, the ✕ inside hides it until the next
       new order. The wrapper only takes space when the frame is actually shown. -->
  {#if !orderVizHidden}
    <div class="orderviz-wrap">
      <OrderViz pinned={orderVizPinned} onClose={() => orderVizHidden = true} />
    </div>
  {/if}
  <div class="orderviz-bar">
    <button
      class="ovz-toggle"
      class:on={orderVizPinned}
      title="Закрепить/открепить визуализацию заявки"
      onclick={() => { orderVizPinned = !orderVizPinned; if (orderVizPinned) orderVizHidden = false; }}
    >📌 Виз. заявки</button>
    {#if orderVizHidden}
      <button class="ovz-toggle" title="Показать визуализацию заявки"
              onclick={() => orderVizHidden = false}>Показать</button>
    {/if}
  </div>
  <BottomBar {events} />
  {#if pendingOrder}
    <OrderConfirmDialog
      order={pendingOrder}
      onConfirm={handleConfirmOrder}
      onCancel={() => pendingOrder = null}
    />
  {/if}
</div>
{/if}

<style>
  .session-splash {
    position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
    background: #0f0f1e; color: #ccc; z-index: 2000;
  }
  .ss-card { display: flex; flex-direction: column; align-items: center; gap: 14px; }
  .ss-spinner {
    width: 34px; height: 34px; border-radius: 50%;
    border: 3px solid #2d2d4a; border-top-color: #4caf50;
    animation: ss-spin 0.8s linear infinite;
  }
  .ss-text { font-size: 13px; opacity: 0.8; }
  @keyframes ss-spin { to { transform: rotate(360deg); } }

  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; min-height: 0; }
  .left-col {
    flex-shrink: 0; position: relative;
    background: #14142a; border-right: 1px solid #2d2d4a;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .l-top, .l-bot, .r-top, .r-bot { overflow: hidden; min-height: 0; display: flex; flex-direction: column; }
  .center-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .chart-book-row { display: flex; min-height: 0; overflow: hidden; }
  .content { overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; min-width: 0; }
  .right-col {
    flex-shrink: 0; position: relative;
    background: #14142a; border-left: 1px solid #2d2d4a;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .positions-wrap { flex-shrink: 0; overflow: hidden; }
  .lab-panel-wrap { overflow: hidden; }
  .quik-tables-wrap { overflow: hidden; flex-shrink: 0; border-top: 1px solid #2d2d4a; }
  /* OrderViz slim frame: ~1/8 of the viewport height (compact), self-hides when
     empty so it never disrupts the main layout. */
  .orderviz-wrap {
    flex-shrink: 0; height: 12.5vh; min-height: 130px; max-height: 160px;
    overflow: hidden; border-top: 1px solid #2d2d4a;
  }
  .orderviz-bar {
    flex-shrink: 0; display: flex; gap: 6px; align-items: center;
    padding: 2px 8px; background: #0f0f1e; border-top: 1px solid #1a1a2e;
  }
  .ovz-toggle {
    background: #1a1a2e; color: #889; border: 1px solid #2d2d4a;
    border-radius: 3px; font-size: 10px; padding: 1px 8px; cursor: pointer;
  }
  .ovz-toggle:hover { color: #cde; border-color: #6aa8ff55; }
  .ovz-toggle.on { color: #4caf50; border-color: #4caf5066; background: #4caf5012; }

  /* ── Drag handles ─────────────────────────────────────────────── */
  .dh {
    flex-shrink: 0;
    background: #1a1a32;
    transition: background 0.15s;
    position: relative;
    z-index: 10;
  }
  .dh:hover, .dh:active { background: #4caf5055; }
  /* Expand the grab band (~±6px) via a pseudo-element so the thin visible line
     is still easy to grab with the mouse, even next to the chart's own border. */
  .dh-v {
    width: 4px; cursor: col-resize; position: relative; touch-action: none;
    border-left: 1px solid #2d2d4a; border-right: 1px solid #2d2d4a;
  }
  .dh-v::after { content: ''; position: absolute; top: 0; bottom: 0; left: -6px; right: -6px; }
  .dh-v.dh-right { position: absolute; right: -3px; top: 0; bottom: 0; width: 5px; border: none; }
  .dh-h {
    height: 5px; cursor: row-resize; position: relative; touch-action: none;
    display: flex; align-items: center; justify-content: center;
    border-top: 1px solid #2d2d4a; border-bottom: 1px solid #2d2d4a;
  }
  .dh-h::after { content: ''; position: absolute; left: 0; right: 0; top: -6px; bottom: -6px; }
  .dh-dot {
    width: 24px; height: 3px; border-radius: 2px;
    background: #3d3d5a; transition: background 0.15s, width 0.15s;
    pointer-events: none;
  }
  .dh:hover .dh-dot { background: #6aa8ff; width: 36px; }
</style>
