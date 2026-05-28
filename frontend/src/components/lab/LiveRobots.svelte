<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let robots = $state<any[]>([]);
  let selected = $state<any | null>(null);
  let loading = $state(true);

  async function load() {
    loading = true;
    const res = await fetchWithAuth('/api/v1/robots');
    robots = res.ok ? await res.json() : [];
    loading = false;
  }

  async function deploy(id: string) {
    await fetchWithAuth(`/api/v1/robots/${id}/deploy`, { method: 'POST' });
    await load();
  }

  async function undeploy(id: string) {
    await fetchWithAuth(`/api/v1/robots/${id}/undeploy`, { method: 'POST' });
    await load();
  }

  $effect(() => { load(); });
</script>

<div class="live-robots">
  <div class="robot-list">
    {#if loading}<div class="loading">Loading…</div>{/if}
    {#each robots as r}
      <div
        class="robot-row"
        class:selected={selected?.id === r.id}
        role="button"
        tabindex="0"
        onclick={() => selected = r}
        onkeydown={(e) => e.key === 'Enter' && (selected = r)}
      >
        <span class="name">{r.name}</span>
        <span class="status" class:deployed={r.deployed}>{r.deployed ? 'LIVE' : 'off'}</span>
        {#if r.deployed}
          <button onclick={(e) => { e.stopPropagation(); undeploy(r.id); }}>Stop</button>
        {:else}
          <button onclick={(e) => { e.stopPropagation(); deploy(r.id); }}>Deploy</button>
        {/if}
      </div>
    {/each}
  </div>

  {#if selected}
    <div class="robot-detail">
      <h3>{selected.name}</h3>
      <pre class="script">{selected.script_code}</pre>
      <div class="params">
        <strong>Params:</strong>
        <pre>{JSON.stringify(selected.params_json, null, 2)}</pre>
      </div>
      <div class="schedule">Schedule: <code>{selected.schedule}</code></div>
    </div>
  {/if}
</div>

<style>
  .live-robots { display: flex; height: 100%; }
  .robot-list { width: 240px; border-right: 1px solid #2d2d4a; overflow-y: auto; flex-shrink: 0; }
  .robot-row {
    padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #1e1e3a;
    display: flex; align-items: center; gap: 8px;
  }
  .robot-row:hover { background: #1a1a2e; }
  .robot-row.selected { background: #1e2a1e; }
  .name { flex: 1; font-size: 12px; color: #ccc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status { font-size: 10px; color: #555; white-space: nowrap; }
  .status.deployed { color: #4caf50; }
  .robot-detail { flex: 1; padding: 16px; overflow-y: auto; }
  .robot-detail h3 { color: #4caf50; margin: 0 0 12px; font-size: 14px; }
  .script { background: #0a0a15; padding: 12px; font-size: 11px; overflow-x: auto; border: 1px solid #2d2d4a; border-radius: 3px; }
  button {
    padding: 2px 8px; font-size: 10px; background: #2d2d4a;
    border: 1px solid #4d4d6a; color: #ccc; cursor: pointer; border-radius: 2px;
    white-space: nowrap;
  }
  .loading { padding: 12px; color: #555; font-size: 12px; }
</style>
