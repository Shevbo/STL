<!-- frontend/src/App.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import InstrumentPanel from './components/InstrumentPanel.svelte';
  import BottomBar from './components/BottomBar.svelte';
  import LabBar from './components/LabBar.svelte';
  import CodeEditor from './components/CodeEditor.svelte';
  import { WsClient } from '$lib/ws';
  import { OfflinePlayer } from '$lib/offline-player';
  import { robotsStore } from '$lib/stores/robots.svelte';
  import type { Strategy, BacktestResult } from '$lib/types';

  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let backtestResult = $state<BacktestResult | null>(null);
  let editorPath = $state<string | null>(null);
  let events = $state<string[]>([]);

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);

  let ws: WsClient;
  let offlinePlayer: OfflinePlayer | null = null;

  onMount(() => {
    ws = new WsClient('ws://localhost:8000/ws');
    ws.connect();
  });
  onDestroy(() => {
    ws?.disconnect();
    offlinePlayer?.stop();
  });

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

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <main class="content">
      {#each robots as robot (robot.id)}
        <ChartFrame
          robotName={robot.name}
          symbol={robot.symbol}
          backtest={robot.id === selectedRobot?.id ? backtestResult : null}
        />
      {/each}
    </main>
    <InstrumentPanel info={selectedRobot ? {
      symbol: selectedRobot.symbol,
      priceMin: 20_000,
      priceMax: 30_000,
      margin: 12_400,
      expiration: '17.06.2026',
    } : null} />
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
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; min-height: 0; }
  .content { flex: 1; overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; }
</style>
