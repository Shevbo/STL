<!-- frontend/src/components/LabBar.svelte -->
<script lang="ts">
  import type { Strategy } from '$lib/types';

  let { onRunBacktest, onLoadStrategy, onOpenEditor, onExportRobot, onToggleOffline }: {
    onRunBacktest: (symbol: string, from: string, to: string, strategyId: string) => Promise<void>;
    onLoadStrategy: (s: Strategy) => void;
    onOpenEditor: (scriptPath: string) => void;
    onExportRobot: (s: Strategy) => void;
    onToggleOffline: (enabled: boolean) => void;
  } = $props();

  let activeTab = $state<'strategies' | 'backtest' | 'scripts'>('strategies');
  let offlineMode = $state(false);

  const mockStrategies: Strategy[] = [
    {
      id: 's1', name: 'GZM6-Trend-v1', symbol: 'GZM6@RTSX',
      params: { fast: 5, slow: 20 }, scriptPath: 'scripts/trend_v1.py',
    },
    {
      id: 's2', name: 'GZM6-Mean-v2', symbol: 'GZM6@RTSX',
      params: { window: 30, threshold: 0.5 }, scriptPath: 'scripts/mean_v2.py',
    },
  ];

  let btSymbol = $state('GZM6@RTSX');
  let btFrom = $state('2026-01-01');
  let btTo = $state('2026-05-01');
  let btStratId = $state(mockStrategies[0].id);
  let btProgress = $state<number | null>(null);
  let btError = $state('');

  async function runBacktest(): Promise<void> {
    btProgress = 0;
    btError = '';
    try {
      await onRunBacktest(btSymbol, btFrom, btTo, btStratId);
      btProgress = 100;
    } catch (e) {
      btError = String(e);
      btProgress = null;
    }
  }

  function toggleOffline(): void {
    offlineMode = !offlineMode;
    onToggleOffline(offlineMode);
  }
</script>

<div class="labbar">
  <div class="tabs">
    <button class:active={activeTab === 'strategies'} onclick={() => activeTab = 'strategies'}>Strategies</button>
    <button class:active={activeTab === 'backtest'} onclick={() => activeTab = 'backtest'}>Backtest</button>
    <button class:active={activeTab === 'scripts'} onclick={() => activeTab = 'scripts'}>Scripts</button>
  </div>

  <div class="tab-content">
    {#if activeTab === 'strategies'}
      <div class="strategies">
        <label class="offline-toggle">
          <input type="checkbox" checked={offlineMode} onchange={toggleOffline} />
          Offline
        </label>
        {#each mockStrategies as s (s.id)}
          <div class="strat-row">
            <span class="strat-name">{s.name}</span>
            <span class="strat-sym">{s.symbol}</span>
            <button onclick={() => onLoadStrategy(s)}>Load</button>
            <button onclick={() => onExportRobot(s)}>Export</button>
          </div>
        {/each}
      </div>
    {:else if activeTab === 'backtest'}
      <div class="backtest">
        <label>
          Инструмент
          <input bind:value={btSymbol} />
        </label>
        <label>
          С
          <input type="date" bind:value={btFrom} />
        </label>
        <label>
          По
          <input type="date" bind:value={btTo} />
        </label>
        <label>
          Стратегия
          <select bind:value={btStratId}>
            {#each mockStrategies as s (s.id)}
              <option value={s.id}>{s.name}</option>
            {/each}
          </select>
        </label>
        <button onclick={runBacktest} disabled={btProgress !== null && btProgress < 100}>
          {btProgress !== null && btProgress < 100 ? `${btProgress}%…` : 'Run'}
        </button>
        {#if btError}<span class="error">{btError}</span>{/if}
      </div>
    {:else}
      <div class="scripts">
        {#each mockStrategies.filter(s => s.scriptPath) as s (s.id)}
          <div class="script-row">
            <span class="script-path">{s.scriptPath}</span>
            <button onclick={() => onOpenEditor(s.scriptPath!)}>Edit</button>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .labbar {
    background: #1a1a2e; border-top: 1px solid #2d2d4a;
    display: flex; flex-direction: column; height: 160px; flex-shrink: 0;
  }
  .tabs { display: flex; border-bottom: 1px solid #2d2d4a; }
  .tabs button {
    padding: 4px 14px; border: none; background: transparent;
    color: #666; font-size: 12px; cursor: pointer; border-bottom: 2px solid transparent;
  }
  .tabs button.active { color: #ddd; border-bottom-color: #3d5af1; }
  .tab-content { flex: 1; overflow-y: auto; padding: 8px 12px; }
  .strategies, .scripts { display: flex; flex-direction: column; gap: 6px; }
  .strat-row, .script-row {
    display: flex; align-items: center; gap: 8px; font-size: 12px; color: #ccc;
  }
  .strat-name, .script-path { flex: 1; font-size: 11px; color: #aaa; }
  .strat-sym { color: #666; font-size: 11px; }
  .backtest { display: flex; flex-direction: row; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
  .backtest label { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #666; }
  .backtest input, .backtest select {
    background: #0f0f1e; border: 1px solid #2d2d4a;
    color: #ccc; padding: 2px 6px; font-size: 12px; border-radius: 3px;
  }
  .offline-toggle { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #aaa; cursor: pointer; }
  button {
    padding: 2px 10px; background: #2d2d4a; border: 1px solid #3d3d5a;
    color: #ccc; font-size: 11px; border-radius: 3px; cursor: pointer;
  }
  button:hover { background: #3d3d6a; }
  button:disabled { opacity: 0.5; cursor: default; }
  .error { color: #f44336; font-size: 11px; }
</style>
