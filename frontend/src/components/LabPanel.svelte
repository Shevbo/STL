<script lang="ts">
  import LiveRobots from './lab/LiveRobots.svelte';
  import BacktestLab from './lab/BacktestLab.svelte';
  import Botstore from './lab/Botstore.svelte';
  import ChartFrame from './ChartFrame.svelte';

  type Tab = 'live' | 'market' | 'backtest' | 'botstore';
  let activeTab = $state<Tab>('live');
  let fullscreen = $state(false);

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && fullscreen) fullscreen = false;
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="lab-panel" class:fullscreen>
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
    <button class:active={activeTab === 'botstore'} onclick={() => activeTab = 'botstore'}>
      Botstore
    </button>
    <button
      class="fullscreen-btn"
      title={fullscreen ? 'Свернуть (Esc)' : 'Развернуть на весь экран'}
      onclick={() => fullscreen = !fullscreen}
    >
      {fullscreen ? '⊟ Свернуть' : '⛶ Во весь экран'}
    </button>
  </div>

  <div class="lab-content">
    {#if activeTab === 'live'}
      <LiveRobots />
    {:else if activeTab === 'market'}
      <ChartFrame symbol="RIM6" />
    {:else if activeTab === 'botstore'}
      <Botstore />
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
  .lab-panel.fullscreen {
    position: fixed; inset: 0; z-index: 1000;
    height: 100vh; width: 100vw; border-top: none;
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
  .fullscreen-btn {
    margin-left: auto; color: #888 !important;
    border-color: #2d2d4a !important;
  }
  .fullscreen-btn:hover { color: #4caf50 !important; border-color: #4caf5066 !important; }
  .lab-content { flex: 1; overflow: hidden; min-height: 0; }
</style>
