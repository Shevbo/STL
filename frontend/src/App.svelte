<!-- frontend/src/App.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import InstrumentPanel from './components/InstrumentPanel.svelte';
  import OrderPanel from './components/OrderPanel.svelte';
  import OrderConfirmDialog from './components/OrderConfirmDialog.svelte';
  import PositionsTable from './components/PositionsTable.svelte';
  import OrderBook from './components/OrderBook.svelte';
  import BottomBar from './components/BottomBar.svelte';
  import LabBar from './components/LabBar.svelte';
  import CodeEditor from './components/CodeEditor.svelte';
  import LoginDialog from './components/LoginDialog.svelte';
  import { WsClient } from '$lib/ws';
  import { OfflinePlayer } from '$lib/offline-player';
  import { robotsStore } from '$lib/stores/robots.svelte';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import { positionsStore } from '$lib/stores/positions.svelte';
  import { instrumentStore } from '$lib/stores/instrument.svelte';
  import { placeOrder } from '$lib/api';
  import type { Strategy, BacktestResult, OrderRequest } from '$lib/types';

  let authed = $state(false);
  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let backtestResult = $state<BacktestResult | null>(null);
  let editorPath = $state<string | null>(null);
  let events = $state<string[]>([]);
  let pendingOrder = $state<OrderRequest | null>(null);

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);
  let positions = $derived(positionsStore.all);
  let currentQuote = $derived(selectedRobot ? quotesStore.get(selectedRobot.symbol) : undefined);

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
      const res = await fetch('/api/v1/instruments');
      if (!res.ok) return;
      const data = await res.json() as { instruments: { symbol: string; ticker: string; name: string }[] };
      instrumentStore.setList(data.instruments ?? []);
    } catch {
      // Non-critical: instrument list may fail, chart still works with current symbol
    }
  }

  // Task 4: fetch params when active symbol changes
  $effect(() => {
    const sym = selectedRobot?.symbol;
    if (!sym || !authed) return;
    fetch(`/api/v1/instruments/${encodeURIComponent(sym)}/params`)
      .then(r => r.ok ? r.json() as Promise<Record<string, unknown>> : null)
      .then(data => { if (data) instrumentStore.setParams(data); })
      .catch(() => {});
  });

  function handleSubscribe(sym: string, timeframe: number) {
    ws?.send({ type: 'subscribe', symbol: sym, timeframe });
  }

  onMount(async () => {
    const res = await fetch('/api/auth/me');
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
    const res = await fetch(`/api/backtest?symbol=${symbol}&from=${from}&to=${to}&strategy=${stratId}`);
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
    const res = await fetch(`/api/scripts/${encodeURIComponent(path)}`, {
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
</script>

{#if !authed}
  <LoginDialog {onLogin} />
{:else}
<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <div class="center-col">
      <div class="chart-book-row">
        <main class="content">
          {#each robots as robot (robot.id)}
            <ChartFrame
              symbol={robot.symbol}
              onSubscribe={handleSubscribe}
            />
          {/each}
        </main>
        {#if selectedRobot}
          <OrderBook symbol={selectedRobot.symbol} />
        {/if}
      </div>
      <PositionsTable {positions} />
    </div>
    <div class="right-col">
      <InstrumentPanel symbol={selectedRobot?.symbol ?? ''} />
      <OrderPanel
        symbol={selectedRobot?.symbol ?? ''}
        quote={currentQuote}
        onSubmit={(order) => pendingOrder = order}
      />
    </div>
  </div>
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
  .center-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .chart-book-row { flex: 1; display: flex; min-height: 0; overflow: hidden; }
  .content { flex: 1; overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; min-width: 0; }
  .right-col { width: 180px; flex-shrink: 0; background: #14142a; border-left: 1px solid #2d2d4a; display: flex; flex-direction: column; overflow-y: auto; }
</style>
