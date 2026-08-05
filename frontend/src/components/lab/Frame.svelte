<script module lang="ts">
  // Pure: parse persisted collapsed flag. Anything other than 'true'/'false' -> def.
  export function loadCollapsed(raw: string | null, def = false): boolean {
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    return def;
  }
</script>

<script lang="ts">
  let {
    title,
    fid,
    maxId = $bindable(),
    children,
    head = null,
    basis = null,
  }: { title: string; fid: string; maxId: string | null; children: any; head?: any; basis?: number | null } = $props();

  const KEY = `ars_f_${fid}_col`;
  let collapsed = $state(read());

  function read(): boolean {
    try { return loadCollapsed(localStorage.getItem(KEY)); } catch { return false; }
  }
  function toggleCollapse() {
    collapsed = !collapsed;
    try { localStorage.setItem(KEY, String(collapsed)); } catch { /* private mode */ }
  }

  let maximized = $derived(maxId === fid);
  let hidden = $derived(maxId != null && maxId !== fid);
  let expanded = $derived(maximized || !collapsed); // maximize forces body open
  // The frame owns its own flex so collapse (header-only) always wins over a parent
  // basis, and inline style beats the CSS class. maximize is absolute -> flex ignored.
  let flexStyle = $derived(
    !expanded ? '0 0 auto' : basis != null ? `0 0 ${basis}px` : '1 1 auto');
</script>

{#if !hidden}
  <div class="frame" class:max={maximized} style:flex={maximized ? null : flexStyle}>
    <div class="fh">
      <span class="ft">{title}</span>
      <span class="sp"></span>
      {#if head}{@render head()}{/if}
      {#if maximized}
        <button class="fb" title="Восстановить" onclick={() => (maxId = null)}>⤡</button>
      {:else}
        <button class="fb" title="На весь экран" onclick={() => (maxId = fid)}>⤢</button>
      {/if}
      <button class="fb" title={expanded ? 'Свернуть' : 'Развернуть'} onclick={toggleCollapse}>
        {expanded ? '▾' : '▸'}
      </button>
    </div>
    <div class="fbody" style:display={expanded ? null : 'none'}>
      {@render children()}
    </div>
  </div>
{/if}

<style>
  .frame { display: flex; flex-direction: column; min-height: 0; min-width: 0; background: #0a0a15;
    border: 1px solid #22224a; border-radius: 6px; overflow: hidden; }
  .frame.max { position: absolute; inset: 0; z-index: 50; }
  .fh { display: flex; align-items: center; gap: 6px; padding: 3px 8px; border-bottom: 1px solid #17172e;
    flex-shrink: 0; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: #667; cursor: default; }
  .ft { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sp { flex: 1 1 auto; }
  .fb { background: none; border: none; cursor: pointer; font-size: 12px; line-height: 1; padding: 0 3px; color: #667; }
  .fb:hover { color: #6aa8ff; }
  .fbody { flex: 1 1 auto; min-height: 0; overflow: auto; }
  .fbody::-webkit-scrollbar { width: 8px; height: 8px; }
  .fbody::-webkit-scrollbar-thumb { background: #2a2a52; border-radius: 4px; }
  .fbody::-webkit-scrollbar-track { background: transparent; }

  /* ── Телефон ───────────────────────────────────────────────────────────────
     На узком экране высоты фреймов не делят экран, а складываются в ленту: всё
     видно целиком длинным скроллом, а не в шести окошках по десять строк.
     `!important` нужен потому, что basis приходит ИНЛАЙНОВЫМ стилем от родителя
     (см. flexStyle) — иначе медиа-правило его не перебьёт. Заголовок фрейма
     крупнее: он же и разделитель разделов при скролле. */
  @media (max-width: 820px) {
    .frame { flex: 0 0 auto !important; border-radius: 8px; }
    .frame.max { position: static; inset: auto; }
    .fh { font-size: 12px; padding: 7px 10px; letter-spacing: .04em; }
    .fb { font-size: 16px; padding: 0 6px; }
    .fbody { overflow: visible; }
  }
</style>
