<!-- frontend/src/components/ExchangeInterface.svelte
  "Интерфейс биржи" (exchange data-source) selector + QUIK agent link lamp.
  sprint02 Phase 1: DATA SOURCE + status only. No order routing. -->
<script lang="ts">
  let iface = $state('finam');
  let options = $state<{ value: string; label: string }[]>([
    { value: 'finam', label: 'Finam Trade API' },
    { value: 'quik', label: 'QUIK агент' },
  ]);
  let quikLink = $state<'green' | 'yellow' | 'red' | 'off'>('off');
  let quikEnabled = $state(false);

  async function load() {
    try {
      const r = await fetch('/api/v1/quik/exchange-interface', { credentials: 'include' });
      if (r.ok) {
        const j = await r.json();
        iface = j.interface ?? 'finam';
        if (Array.isArray(j.options) && j.options.length) options = j.options;
      }
    } catch (_) { /* leave defaults */ }
    await loadStatus();
  }

  async function loadStatus() {
    try {
      const r = await fetch('/api/v1/quik/status', { credentials: 'include' });
      if (r.ok) {
        const j = await r.json();
        quikEnabled = !!j.enabled;
        const agents = j.agents ?? [];
        quikLink = agents.length ? (agents[0].link ?? 'red') : 'off';
      }
    } catch (_) { quikLink = 'off'; }
  }

  async function change(e: Event) {
    const value = (e.target as HTMLSelectElement).value;
    try {
      const r = await fetch('/api/v1/quik/exchange-interface', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interface: value }),
      });
      if (r.ok) iface = (await r.json()).interface;
    } catch (_) { /* keep previous */ }
  }

  $effect(() => {
    load();
    const t = setInterval(loadStatus, 5000);
    return () => clearInterval(t);
  });
</script>

<div class="exch" title="Интерфейс биржи (источник данных)">
  <span class="lbl">Биржа:</span>
  <select value={iface} onchange={change}>
    {#each options as o}
      <option value={o.value}>{o.label}</option>
    {/each}
  </select>
  {#if iface === 'quik'}
    <span class="lamp" class:green={quikLink === 'green'}
          class:yellow={quikLink === 'yellow'} class:red={quikLink === 'red'}
          class:off={quikLink === 'off'}
          title="QUIK агент: {quikEnabled ? quikLink : 'отключён'}">●</span>
  {/if}
</div>

<style>
  .exch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #ccc; }
  .lbl { opacity: 0.8; }
  select {
    background: #14142a; color: #ccc; border: 1px solid #444;
    border-radius: 4px; padding: 1px 6px; font-size: 12px;
  }
  .lamp { font-size: 16px; line-height: 1; }
  .lamp.green { color: #4caf50; }
  .lamp.yellow { color: #ff9800; }
  .lamp.red { color: #f44336; }
  .lamp.off { color: #666; }
</style>
