<!-- NavMenu: hamburger (☰) with basic site navigation. Used on full-page screens
     (agent-robot showcase, strategy page) that otherwise have no way back to the app.
     Links are plain anchors — the SPA reads ?lab=<tab> on load (App.svelte / LabPanel). -->
<script lang="ts">
  let open = $state(false);
  const items = [
    { href: '/', label: '🏠 Главная (терминал)' },
    { href: '/?lab=live', label: '🤖 LIVE роботы' },
    { href: '/?lab=botstore', label: '🏪 Ботстор' },
    { href: '/?lab=backtest', label: '🧪 Бэктест' },
    { href: '/?lab=market', label: '📈 Графики' },
  ];
  function close() { open = false; }
</script>

<svelte:window onclick={(e) => { if (open && !(e.target as HTMLElement).closest('.navmenu')) close(); }} />

<div class="navmenu">
  <button class="nm-btn" title="Меню навигации" aria-label="Меню" onclick={() => open = !open}>☰</button>
  {#if open}
    <div class="nm-drop">
      {#each items as it}
        <a class="nm-item" href={it.href}>{it.label}</a>
      {/each}
    </div>
  {/if}
</div>

<style>
  .navmenu { position: relative; display: inline-flex; }
  .nm-btn {
    background: #1a1a2e; color: #cde; border: 1px solid #2d2d4a;
    border-radius: 4px; font-size: 16px; line-height: 1; padding: 3px 9px; cursor: pointer;
  }
  .nm-btn:hover { border-color: #6aa8ff55; color: #fff; }
  .nm-drop {
    position: absolute; top: 110%; left: 0; z-index: 100;
    background: #14142a; border: 1px solid #2d2d4a; border-radius: 6px;
    padding: 4px; min-width: 200px; box-shadow: 0 6px 20px #0008;
    display: flex; flex-direction: column; gap: 1px;
  }
  .nm-item {
    display: block; padding: 6px 10px; border-radius: 4px;
    color: #cde; text-decoration: none; font-size: 12px; white-space: nowrap;
  }
  .nm-item:hover { background: #23234a; color: #fff; }
</style>
