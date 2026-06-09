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
  import LabBar from './components/LabBar.svelte';
  import LabPanel from './components/LabPanel.svelte';
  import CodeEditor from './components/CodeEditor.svelte';
  import LoginDialog from './components/LoginDialog.svelte';
  import { WsClient } from '$lib/ws';
  import { OfflinePlayer } from '$lib/offline-player';
  import { robotsStore } from '$lib/stores/robots.svelte';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import { positionsStore } from '$lib/stores/positions.svelte';
  import { instrumentStore } from '$lib/stores/instrument.svelte';
  import { placeOrder } from '$lib/api';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import type { Strategy, BacktestResult, OrderRequest } from '$lib/types';

  let authed = $state(false);
  let labMode = $state(false);
  let showLab = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let backtestResult = $state<BacktestResult | null>(null);
  let editorPath = $state<string | null>(null);
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
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
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

  // п.1: effectiveSymbol — то, что реально показано на экране
  let effectiveSymbol = $derived(activeSymbol || selectedRobot?.symbol || '');
  let currentQuote = $derived(quotesStore.get(effectiveSymbol));

  let ws: WsClient;
  let offlinePlayer: OfflinePlayer | null = null;

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
    const res = await fetchWithAuth('/api/auth/me');
    if (res.ok) {
      authed = true;
      startWs();
      loadInstruments();
    }
  });
  onDestroy(() => {
    ws?.disconnect();
    offlinePlayer?.stop();
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

  async function handleRunBacktest(symbol: string, from: string, to: string, stratId: string): Promise<void> {
    const res = await fetchWithAuth(`/api/backtest?symbol=${symbol}&from=${from}&to=${to}&strategy=${stratId}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    backtestResult = await res.json() as BacktestResult;
  }

  function handleLoadStrategy(s: Strategy): void {
    events = [...events, `${new Date().toLocaleTimeString()} Загружена стратегия: ${s.name}`];
  }

  function handleOpenEditor(path: string): void {
    editorPath = path;
  }

  function handleExportRobot(s: Strategy): void {
    const blob = new Blob([JSON.stringify(s, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${s.name}.json`; a.click();
    URL.revokeObjectURL(url);
    events = [...events, `${new Date().toLocaleTimeString()} Экспортирован робот: ${s.name}`];
  }

  async function handleToggleOffline(enabled: boolean): Promise<void> {
    if (enabled) {
      const input = document.createElement('input');
      input.type = 'file'; input.accept = '.json';
      input.onchange = async () => {
        const file = input.files?.[0];
        if (!file) return;
        ws.disconnect();
        offlinePlayer = new OfflinePlayer();
        await offlinePlayer.load(file);
        offlinePlayer.play();
        events = [...events, `${new Date().toLocaleTimeString()} Offline: ${file.name}`];
      };
      input.click();
    } else {
      offlinePlayer?.stop();
      offlinePlayer = null;
      ws.connect();
      events = [...events, `${new Date().toLocaleTimeString()} Online режим`];
    }
  }

  async function handleEditorSave(path: string, content: string): Promise<void> {
    const res = await fetchWithAuth(`/api/scripts/${encodeURIComponent(path)}`, {
      method: 'PUT', body: content,
      headers: { 'Content-Type': 'text/plain' },
    });
    if (!res.ok) throw new Error(`Save failed: ${res.status}`);
  }

  async function handleEditorRun(path: string, content: string): Promise<void> {
    await handleEditorSave(path, content);
    await handleRunBacktest(
      selectedRobot?.symbol ?? 'GZM6@RTSX', '2026-01-01', '2026-05-01', 's1',
    );
  }

  // п.6: клик по стакану → открыть диалог заявки с quantity=1
  function handleBookOrder(partial: Omit<OrderRequest, 'quantity'>): void {
    pendingOrder = { ...partial, quantity: 1 };
  }
</script>

{#if !authed}
  <LoginDialog {onLogin} />
{:else}
<div class="shell" onpointermove={onPointerMove} onpointerup={onPointerUp} onpointerleave={onPointerUp}>
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} {showLab} onToggleLabPanel={() => showLab = !showLab} />
  <div class="body">
    <!-- LEFT COLUMN -->
    <div class="left-col" style="width:{leftW}px">
      <div class="l-top" style="flex:{leftTopPct}">
        <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
      </div>
      <!-- left-col vertical split handle -->
      <div class="dh dh-h" title="Размер панелей"
           onpointerdown={(e) => { const el = (e.target as HTMLElement).previousElementSibling as HTMLElement | null; onPointerDown('leftSplit', e, 0, el?.offsetHeight ?? 0); }}>
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
           onpointerdown={(e) => { const el = (e.target as HTMLElement).previousElementSibling as HTMLElement | null; onPointerDown('rightSplit', e, 0, el?.offsetHeight ?? 0); }}>
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
  {#if labMode}
    <LabBar
      onRunBacktest={handleRunBacktest}
      onLoadStrategy={handleLoadStrategy}
      onOpenEditor={handleOpenEditor}
      onExportRobot={handleExportRobot}
      onToggleOffline={handleToggleOffline}
    />
  {/if}
  <BottomBar {events} />
  {#if editorPath}
    <CodeEditor
      scriptPath={editorPath}
      onSave={handleEditorSave}
      onRun={handleEditorRun}
      onClose={() => editorPath = null}
    />
  {/if}
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

  /* ── Drag handles ─────────────────────────────────────────────── */
  .dh {
    flex-shrink: 0;
    background: #1a1a32;
    transition: background 0.15s;
    position: relative;
    z-index: 10;
  }
  .dh:hover, .dh:active { background: #4caf5055; }
  .dh-v {
    width: 4px; cursor: col-resize;
    border-left: 1px solid #2d2d4a; border-right: 1px solid #2d2d4a;
  }
  .dh-v.dh-right { position: absolute; right: -3px; top: 0; bottom: 0; width: 5px; border: none; }
  .dh-h {
    height: 5px; cursor: row-resize;
    display: flex; align-items: center; justify-content: center;
    border-top: 1px solid #2d2d4a; border-bottom: 1px solid #2d2d4a;
  }
  .dh-dot {
    width: 24px; height: 3px; border-radius: 2px;
    background: #3d3d5a; transition: background 0.15s, width 0.15s;
  }
  .dh:hover .dh-dot { background: #6aa8ff; width: 36px; }
</style>
