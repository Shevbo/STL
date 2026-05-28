<script lang="ts">
  import LiveRobots from './lab/LiveRobots.svelte';
  import BacktestLab from './lab/BacktestLab.svelte';
  import ChartFrame from './ChartFrame.svelte';

  type Tab = 'live' | 'market' | 'backtest';
  let activeTab = $state<Tab>('live');
</script>

<div class="lab-panel">
  <div class="lab-tabs">
    <button class:active={activeTab === 'live'} onclick={() => activeTab = 'live'}>
      Live Robots
    </button>
    <button class:active={activeTab === 'market'} onclick={() => activeTab = 'market'}>
      Market Browser
    </button>
    <button class:active={activeTab === 'backtest'} onclick={() => activeTab = 'backtest'}>
      Backtest Lab
    </button>
  </div>

  <div class="lab-content">
    {#if activeTab === 'live'}
      <LiveRobots />
    {:else if activeTab === 'market'}
      <ChartFrame symbol="RIM6" />
    {:else}
      <BacktestLab />
    {/if}
  </div>
</div>

<style>
  .lab-panel {
    display: flex; flex-direction: column; height: 100%;
    background: #0f0f1e; border-top: 2px solid #4caf50;
  }
  .lab-tabs {
    display: flex; gap: 2px; padding: 4px 8px;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .lab-tabs button {
    padding: 4px 12px; background: transparent; color: #555;
    border: 1px solid transparent; font-size: 11px; border-radius: 3px; cursor: pointer;
  }
  .lab-tabs button:hover { color: #aaa; }
  .lab-tabs button.active { color: #4caf50; border-color: #4caf5066; }
  .lab-content { flex: 1; overflow: hidden; min-height: 0; }
</style>
