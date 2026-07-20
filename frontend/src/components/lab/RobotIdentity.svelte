<script lang="ts">
  // One consistent way to show a robot's identity everywhere: the friendly name
  // (operator override) reads first, the raw id sits right beside it as a mono
  // chip you can always see without hunting. When no friendly name is set, name
  // collapses to the id, so we show the id ALONE (never "id / id"). The id is the
  // real key (QUIK order comment, state files, attribution) — it stays visible on
  // purpose, so what you read here matches what you see in the QUIK terminal.
  //
  // size: 'title' — page/modal header (name large, id under it)
  //       'row'   — table row / card line (name + id inline, compact)
  //       'chip'  — dense list / dropdown item (smallest, still >=10px)
  let {
    name = '',
    id = '',
    size = 'row',
  }: { name?: string; id?: string; size?: 'title' | 'row' | 'chip' } = $props();

  const hasName = $derived(!!name && !!id && name !== id);
  const primary = $derived(name || id);
</script>

<span class="rid {size}" title={hasName ? name + '  ·  ' + id : id}>
  <span class="rid-name">{primary}</span>
  {#if hasName}
    <span class="rid-id" title="ID робота (ключ атрибуции, комментарий в QUIK)">{id}</span>
  {/if}
</span>

<style>
  .rid {
    display: inline-flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }
  /* title + row STACK the id under the name so both read in full even inside a
     narrow, nowrap table cell (the inline chip variant is 'chip', for dense lists). */
  .rid.title,
  .rid.row { flex-direction: column; align-items: flex-start; gap: 2px; }

  .rid-name {
    color: var(--rid-name, #e8ecf1);
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .rid-id {
    font-family: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
    color: var(--rid-id-fg, #96a0b0);
    background: var(--rid-id-bg, rgba(255, 255, 255, 0.06));
    border: 1px solid var(--rid-id-bd, rgba(255, 255, 255, 0.08));
    border-radius: 5px;
    padding: 1px 6px;
    letter-spacing: 0.2px;
    white-space: nowrap;
    flex: 0 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    user-select: all;            /* click drags the whole id — easy copy */
  }

  /* Sizes — all id text stays >= 10px (operator UI standard). */
  .rid.title .rid-name { font-size: 18px; line-height: 1.15; }
  .rid.title .rid-id   { font-size: 11px; }

  .rid.row .rid-name { font-size: 13px; }
  .rid.row .rid-id   { font-size: 11px; }

  .rid.chip .rid-name { font-size: 12px; }
  .rid.chip .rid-id   { font-size: 10px; padding: 0 5px; }
</style>
