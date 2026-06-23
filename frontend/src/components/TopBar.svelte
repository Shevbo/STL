<!-- frontend/src/components/TopBar.svelte -->
<script lang="ts">
  import { accountStore } from '$lib/stores/account.svelte';
  import { servicesStore } from '$lib/stores/services.svelte';

  let { showLab, onToggleLabPanel }: {
    showLab?: boolean;
    onToggleLabPanel?: () => void;
  } = $props();

  let acc = $derived(accountStore.data);
  let mdStatus = $derived(servicesStore.all.md);

  function fmt(n: number): string {
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  }
</script>

<header class="topbar">
  <div class="account">
    <span>Депозит: <b>{fmt(acc.deposit)} ₽</b></span>
    <span>Свободно: <b>{fmt(acc.free)} ₽</b></span>
    <span>В позиции: <b>{fmt(acc.inPosition)} ₽</b></span>
    <span class="vm" class:pos={acc.variationMargin >= 0} class:neg={acc.variationMargin < 0}>
      Вар.маржа: <b>{fmt(acc.variationMargin)} ₽</b>
    </span>
  </div>
  <div class="controls">
    <button class="lab-btn lab-panel-btn" class:active={showLab} onclick={onToggleLabPanel}>Бэктест</button>
    <span
      class="dot"
      class:ok={mdStatus === 'ok'}
      class:warn={mdStatus === 'warn'}
      class:error={mdStatus === 'error'}
      title="Market Data: {mdStatus}"
    >●</span>
  </div>
</header>

<style>
  .topbar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 12px; height: 36px;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    font-size: 13px; color: #ccc; flex-shrink: 0;
  }
  .account { display: flex; gap: 20px; }
  .controls { display: flex; align-items: center; gap: 10px; }
  .lab-btn {
    padding: 2px 12px; border-radius: 4px; border: 1px solid #444;
    background: transparent; color: #ccc; cursor: pointer; font-size: 12px;
    transition: background 0.15s;
  }
  .lab-btn.active { background: #3d5af1; border-color: #3d5af1; color: #fff; }
  .lab-panel-btn.active { background: #2d6a2d; border-color: #4caf50; color: #4caf50; }
  .dot { font-size: 18px; line-height: 1; }
  .dot.ok { color: #4caf50; }
  .dot.warn { color: #ff9800; }
  .dot.error { color: #f44336; }
  .vm.pos b { color: #4caf50; }
  .vm.neg b { color: #f44336; }
</style>
