<!-- frontend/src/components/QuikTables.svelte
  Generic viewer for arbitrary QUIK tables pushed by the agent as RawTable.
  Read-only: lists available tables (poll /tables ~5s) and renders the selected
  one (columns header + rows) in a scrollable grid. Additive to the typed views. -->
<script lang="ts">
  type TableSummary = {
    agent_id: string;
    name: string;
    columns_count: number;
    rows_count: number;
    received_at_unix_ms: number;
  };
  type TableData = {
    columns: string[];
    rows: string[][];
    received_at_unix_ms: number;
  };

  let tables = $state<TableSummary[]>([]);
  let selected = $state<string>('');
  let data = $state<TableData | null>(null);
  let loading = $state(false);

  async function loadList() {
    try {
      const r = await fetch('/api/v1/quik/tables', { credentials: 'include' });
      if (!r.ok) return;
      const j = await r.json();
      tables = Array.isArray(j.tables) ? j.tables : [];
      // Auto-select the first table once one appears.
      if (!selected && tables.length) {
        selected = tables[0].name;
        await loadTable();
      }
    } catch (_) { /* keep previous list */ }
  }

  async function loadTable() {
    if (!selected) { data = null; return; }
    loading = true;
    try {
      const r = await fetch(`/api/v1/quik/tables/${encodeURIComponent(selected)}`, {
        credentials: 'include',
      });
      data = r.ok ? await r.json() : null;
    } catch (_) {
      data = null;
    } finally {
      loading = false;
    }
  }

  function onSelect(e: Event) {
    selected = (e.target as HTMLSelectElement).value;
    loadTable();
  }

  function fmtTs(ms: number): string {
    if (!ms) return '';
    try { return new Date(ms).toLocaleTimeString('ru-RU'); } catch { return ''; }
  }

  $effect(() => {
    loadList();
    const t = setInterval(async () => { await loadList(); await loadTable(); }, 5000);
    return () => clearInterval(t);
  });
</script>

<div class="quik-tables">
  <div class="head">
    <span class="lbl">Таблицы QUIK:</span>
    {#if tables.length}
      <select value={selected} onchange={onSelect}>
        {#each tables as t}
          <option value={t.name}>{t.name} ({t.rows_count})</option>
        {/each}
      </select>
      {#if data}<span class="ts">{fmtTs(data.received_at_unix_ms)}</span>{/if}
    {:else}
      <span class="empty">нет данных</span>
    {/if}
  </div>
  {#if data && data.columns.length}
    <div class="grid-wrap">
      <table>
        <thead>
          <tr>{#each data.columns as c}<th>{c}</th>{/each}</tr>
        </thead>
        <tbody>
          {#each data.rows as row}
            <tr>{#each row as cell}<td>{cell}</td>{/each}</tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else if loading}
    <div class="empty pad">загрузка…</div>
  {:else if selected}
    <div class="empty pad">таблица пуста</div>
  {/if}
</div>

<style>
  .quik-tables {
    display: flex; flex-direction: column; height: 100%;
    background: #14142a; color: #ccc; font-size: 12px; overflow: hidden;
  }
  .head {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 8px; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .lbl { opacity: 0.8; }
  .ts { opacity: 0.6; font-size: 11px; margin-left: auto; }
  .empty { opacity: 0.5; }
  .pad { padding: 8px; }
  select {
    background: #14142a; color: #ccc; border: 1px solid #444;
    border-radius: 4px; padding: 1px 6px; font-size: 12px;
  }
  .grid-wrap { flex: 1; overflow: auto; min-height: 0; }
  table { border-collapse: collapse; width: 100%; }
  thead th {
    position: sticky; top: 0; background: #1a1a32; color: #9ab;
    text-align: left; padding: 3px 8px; border-bottom: 1px solid #2d2d4a;
    white-space: nowrap; font-weight: 600;
  }
  tbody td {
    padding: 2px 8px; border-bottom: 1px solid #20203a;
    white-space: nowrap;
  }
  tbody tr:hover { background: #1a1a32; }
</style>
