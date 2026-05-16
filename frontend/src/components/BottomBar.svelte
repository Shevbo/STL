<!-- frontend/src/components/BottomBar.svelte -->
<script lang="ts">
  import { servicesStore } from '$lib/stores/services.svelte';
  import type { ServiceId, ServiceStatus } from '$lib/types';

  let { events = [] }: { events?: string[] } = $props();

  let svc = $derived(servicesStore.all);

  const SERVICE_LABELS: Record<ServiceId, string> = {
    auth: 'Auth', md: 'MD', tx: 'TX', oms: 'OMS', pos: 'Pos', audit: 'Audit',
  };

  const STATUS_COLOR: Record<ServiceStatus, string> = {
    ok: '#4caf50', warn: '#ff9800', error: '#f44336',
  };

  const SERVICE_IDS: ServiceId[] = ['auth', 'md', 'tx', 'oms', 'pos', 'audit'];
</script>

<footer class="bottom-bar">
  <div class="events">
    {#each events.slice(-50).reverse() as evt}
      <span class="evt">{evt}</span>
    {/each}
    {#if !events.length}
      <span class="empty">Нет событий</span>
    {/if}
  </div>
  <div class="services">
    {#each SERVICE_IDS as id}
      <span class="svc" title="{SERVICE_LABELS[id]}: {svc[id]}">
        <span class="dot" style="color: {STATUS_COLOR[svc[id]]}">●</span>
        <span class="lbl">{SERVICE_LABELS[id]}</span>
      </span>
    {/each}
  </div>
</footer>

<style>
  .bottom-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 10px; height: 32px;
    background: #1a1a2e; border-top: 1px solid #2d2d4a;
    flex-shrink: 0;
  }
  .events {
    flex: 1; overflow-x: auto; display: flex; gap: 12px;
    align-items: center; scrollbar-width: none;
  }
  .events::-webkit-scrollbar { display: none; }
  .evt { font-size: 11px; color: #888; white-space: nowrap; }
  .empty { font-size: 11px; color: #444; }
  .services { display: flex; gap: 10px; flex-shrink: 0; padding-left: 12px; }
  .svc { display: flex; align-items: center; gap: 3px; cursor: default; }
  .dot { font-size: 12px; }
  .lbl { font-size: 10px; color: #555; }
</style>
