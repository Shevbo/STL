<!-- Live Robots tab: list, detail view, deploy/undeploy, settings editor -->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import RobotEditor from './RobotEditor.svelte';

  let robots = $state<any[]>([]);
  let selected = $state<any | null>(null);
  let loading = $state(true);
  let mode = $state<'list' | 'edit' | 'new'>('list');

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

  function onSaved(r: any) {
    mode = 'list';
    load();
    selected = r;
  }

  $effect(() => { load(); });
</script>

<div class="live-robots">
  <!-- Left: robot list -->
  <div class="robot-list">
    <div class="list-header">
      <span class="list-title">Роботы</span>
      <button class="new-btn" onclick={() => { selected = null; mode = 'new'; }}>+</button>
    </div>
    {#if loading}<div class="loading">Loading…</div>{/if}
    {#each robots as r}
      <div
        class="robot-row"
        class:selected={selected?.id === r.id && mode === 'list'}
        role="button"
        tabindex="0"
        onclick={() => { selected = r; mode = 'list'; }}
        onkeydown={(e) => e.key === 'Enter' && (selected = r, mode = 'list')}
      >
        <span class="status-dot" class:live={r.deployed}></span>
        <span class="name">{r.name}</span>
        <span class="status-badge" class:deployed={r.deployed}>{r.deployed ? 'LIVE' : 'off'}</span>
      </div>
    {/each}
  </div>

  <!-- Right panel -->
  <div class="detail-pane">
    {#if mode === 'new'}
      <RobotEditor
        onSaved={onSaved}
        onClose={() => mode = 'list'}
      />

    {:else if mode === 'edit' && selected}
      <RobotEditor
        robot={selected}
        onSaved={onSaved}
        onClose={() => mode = 'list'}
      />

    {:else if selected}
      <!-- Detail view -->
      <div class="detail">
        <div class="detail-header">
          <h3>{selected.name}</h3>
          <div class="detail-actions">
            <button class="icon-btn edit" onclick={() => mode = 'edit'}>✏ Настройки</button>
            {#if selected.deployed}
              <button class="icon-btn stop" onclick={() => undeploy(selected.id)}>⏹ Stop</button>
            {:else}
              <button class="icon-btn deploy" onclick={() => deploy(selected.id)}>▶ Deploy</button>
            {/if}
          </div>
        </div>

        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">Статус</span>
            <span class="info-value" class:green={selected.deployed}>
              {selected.deployed ? 'LIVE' : 'Остановлен'}
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">Торговое окно</span>
            <code class="info-value">{selected.schedule}</code>
          </div>
          <div class="info-item">
            <span class="info-label">Версия</span>
            <span class="info-value">v{selected.version ?? 1}</span>
          </div>
          {#if selected.deployed_at}
            <div class="info-item">
              <span class="info-label">Запущен</span>
              <span class="info-value">{new Date(selected.deployed_at).toLocaleString('ru')}</span>
            </div>
          {/if}
        </div>

        <div class="params-section">
          <div class="params-title">Параметры</div>
          <div class="params-grid">
            {#each Object.entries(selected.params_json ?? {}) as [k, v]}
              <div class="param-row">
                <span class="param-key">{k}</span>
                <span class="param-val">{v}</span>
              </div>
            {/each}
          </div>
        </div>

        <div class="script-section">
          <div class="script-title">Скрипт</div>
          <pre class="script">{selected.script_code}</pre>
        </div>
      </div>

    {:else}
      <div class="empty">
        <div class="empty-icon">🤖</div>
        <div class="empty-text">Выберите робота или создайте нового</div>
        <button class="new-btn-big" onclick={() => mode = 'new'}>Создать робота</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .live-robots { display: flex; height: 100%; overflow: hidden; }

  /* List */
  .robot-list { width: 220px; border-right: 1px solid #2d2d4a; display: flex; flex-direction: column; flex-shrink: 0; }
  .list-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border-bottom: 1px solid #2d2d4a; }
  .list-title { font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
  .new-btn { background: #4caf5020; border: 1px solid #4caf5066; color: #4caf50; font-size: 16px; width: 24px; height: 24px; border-radius: 3px; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 0; }

  .robot-row {
    padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #1a1a2e;
    display: flex; align-items: center; gap: 7px; transition: background 0.1s;
  }
  .robot-row:hover { background: #1a1a2e; }
  .robot-row.selected { background: #0d1a0d; border-left: 2px solid #4caf50; }
  .status-dot { width: 6px; height: 6px; border-radius: 50%; background: #333; flex-shrink: 0; }
  .status-dot.live { background: #4caf50; box-shadow: 0 0 4px #4caf5088; }
  .name { flex: 1; font-size: 12px; color: #ccc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status-badge { font-size: 9px; color: #555; white-space: nowrap; }
  .status-badge.deployed { color: #4caf50; }
  .loading { padding: 12px; color: #555; font-size: 12px; }

  /* Detail pane */
  .detail-pane { flex: 1; overflow-y: auto; min-width: 0; }

  .detail { padding: 16px; display: flex; flex-direction: column; gap: 16px; }
  .detail-header { display: flex; justify-content: space-between; align-items: center; }
  .detail-header h3 { color: #4caf50; margin: 0; font-size: 14px; }
  .detail-actions { display: flex; gap: 8px; }
  .icon-btn { padding: 4px 10px; font-size: 11px; border-radius: 3px; cursor: pointer; border: 1px solid; }
  .icon-btn.edit { background: #1a1a2e; border-color: #3d3d5a; color: #aaa; }
  .icon-btn.deploy { background: #0a1a0a; border-color: #4caf5066; color: #4caf50; }
  .icon-btn.stop { background: #1a0a0a; border-color: #f4433666; color: #f44336; }

  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .info-item { background: #0a0a15; border: 1px solid #1e1e3a; border-radius: 4px; padding: 8px; }
  .info-label { display: block; font-size: 10px; color: #555; margin-bottom: 3px; }
  .info-value { font-size: 12px; color: #ccc; font-family: monospace; }
  .info-value.green { color: #4caf50; }

  .params-section { display: flex; flex-direction: column; gap: 6px; }
  .params-title { font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }
  .params-grid { display: flex; flex-direction: column; gap: 3px; }
  .param-row { display: flex; justify-content: space-between; padding: 4px 8px; background: #0a0a15; border-radius: 3px; }
  .param-key { font-size: 11px; color: #888; }
  .param-val { font-size: 11px; color: #4caf50; font-family: monospace; }

  .script-section { display: flex; flex-direction: column; gap: 6px; }
  .script-title { font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }
  .script { background: #060610; border: 1px solid #1e1e3a; border-radius: 4px; padding: 12px; font-size: 10px; color: #888; overflow-x: auto; line-height: 1.5; white-space: pre; }

  /* Empty state */
  .empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: #555; }
  .empty-icon { font-size: 40px; }
  .empty-text { font-size: 13px; }
  .new-btn-big { padding: 8px 20px; background: #4caf5020; border: 1px solid #4caf5066; color: #4caf50; cursor: pointer; border-radius: 4px; font-size: 12px; }
</style>
