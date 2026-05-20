<!-- frontend/src/components/RobotsPanel.svelte -->
<script lang="ts">
  import { robotsStore } from '$lib/stores/robots.svelte';

  let { selectedId, onSelect }: {
    selectedId: string | null;
    onSelect: (id: string) => void;
  } = $props();

  let robots = $derived(robotsStore.all);

  function fmtPnl(pnl: number | undefined): string {
    const v = pnl ?? 0;
    const sign = v >= 0 ? '+' : '';
    return `${sign}${v.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₽`;
  }

  function posLabel(pos: number | undefined): string {
    const p = pos ?? 0;
    if (p === 0) return 'Flat';
    return p > 0 ? `↑${p}` : `↓${Math.abs(p)}`;
  }
</script>

<aside class="robots-panel">
  {#each robots as robot (robot.id)}
    <div
      class="card"
      class:selected={selectedId === robot.id}
      role="button"
      tabindex="0"
      onclick={() => onSelect(robot.id)}
      onkeydown={(e) => e.key === 'Enter' && onSelect(robot.id)}
    >
      <div class="row">
        <span class="name">{robot.name}</span>
        <span class="settings" onclick={(e) => e.stopPropagation()} title="Настройки">⚙</span>
      </div>
      <div class="stats">
        <span class="pnl" class:pos={robot.pnl >= 0} class:neg={robot.pnl < 0}>{fmtPnl(robot.pnl)}</span>
        <span class="trades">{robot.tradeCount} сд.</span>
        <span class="pos-label" class:long={robot.position > 0} class:short={robot.position < 0}>
          {posLabel(robot.position)}
        </span>
      </div>
    </div>
  {:else}
    <div class="empty">Нет роботов</div>
  {/each}
</aside>

<style>
  .robots-panel {
    width: 200px; flex-shrink: 0;
    overflow-y: auto; background: #14142a;
    border-right: 1px solid #2d2d4a;
    display: flex; flex-direction: column;
  }
  .card {
    padding: 8px 10px; cursor: pointer;
    border-left: 3px solid transparent;
    border-bottom: 1px solid #1e1e3a;
  }
  .card:hover { background: #1e1e3a; }
  .card.selected { border-left-color: #3d5af1; background: #1e1e3a; }
  .row { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .name { font-size: 12px; font-weight: 600; color: #ddd; }
  .settings { color: #444; cursor: pointer; font-size: 14px; }
  .settings:hover { color: #aaa; }
  .stats { display: flex; gap: 8px; font-size: 11px; flex-wrap: wrap; color: #888; }
  .pnl.pos { color: #4caf50; }
  .pnl.neg { color: #f44336; }
  .pos-label.long { color: #4caf50; }
  .pos-label.short { color: #f44336; }
  .empty { padding: 16px; color: #555; font-size: 12px; text-align: center; }
</style>
