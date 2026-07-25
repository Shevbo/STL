<!-- NavMenu: бургер (☰) с двухуровневой навигацией по всему сайту. Стоит на
     КАЖДОМ экране: в шапке главного шелла (TopBar) и на полноэкранных страницах
     (витрина робота, страница стратегии). Ссылки — обычные якоря: SPA читает
     ?lab=<tab> на загрузке, статические страницы открываются как есть. -->
<script lang="ts">
  let open = $state(false);
  // Раскрытые группы. По умолчанию открыты все: меню — карта сайта, а не тест
  // на память. Клик по заголовку группы сворачивает её.
  let collapsed = $state<Record<string, boolean>>({});

  type Item = { href: string; label: string; ext?: boolean };
  type Group = { id: string; title: string; items: Item[] };

  const groups: Group[] = [
    {
      id: 'trade',
      title: 'Торговля',
      items: [
        { href: '/', label: 'Терминал (главный экран)' },
        { href: '/?orders=1', label: 'Заявки: обычная · умные · графики' },
        { href: '/?equity=1', label: 'Доходность алготорговли' },
        { href: '/?tables=1', label: 'Таблицы QUIK' },
      ],
    },
    {
      id: 'lab',
      title: 'Лаборатория',
      items: [
        { href: '/?lab=live', label: 'LIVE роботы' },
        { href: '/?lab=botstore', label: 'Ботстор (хитпарад)' },
        { href: '/?lab=backtest', label: 'Бэктест' },
        { href: '/?lab=market', label: 'Графики рынка' },
      ],
    },
    {
      id: 'watch',
      title: 'Мониторинг',
      items: [
        { href: '/watchdog-log.html', label: 'Вотчер: лог и настройки', ext: true },
        { href: '/agent-status.html', label: 'QUIK-агент: статус и сверка', ext: true },
        { href: '/i9.html', label: 'i9: ресурсы переборщика', ext: true },
        { href: '/companion.html', label: 'Компаньон (веб-версия панели)', ext: true },
      ],
    },
    {
      id: 'help',
      title: 'Справка',
      items: [
        { href: '/docs.html', label: 'Документация платформы STL', ext: true },
      ],
    },
  ];

  function close() { open = false; }
  function toggleGroup(id: string) { collapsed[id] = !collapsed[id]; }
</script>

<svelte:window onclick={(e) => { if (open && !(e.target as HTMLElement).closest('.navmenu')) close(); }} />

<div class="navmenu">
  <button class="nm-btn" title="Меню навигации" aria-label="Меню" onclick={() => open = !open}>☰</button>
  {#if open}
    <div class="nm-drop">
      {#each groups as g (g.id)}
        <button class="nm-group" onclick={() => toggleGroup(g.id)}>
          <span class="nm-caret">{collapsed[g.id] ? '▸' : '▾'}</span>{g.title}
        </button>
        {#if !collapsed[g.id]}
          {#each g.items as it}
            <a class="nm-item" href={it.href} target={it.ext ? '_blank' : undefined}
               rel={it.ext ? 'noopener' : undefined}>{it.label}{#if it.ext}<span class="nm-ext">↗</span>{/if}</a>
          {/each}
        {/if}
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
    position: absolute; top: 110%; left: 0; z-index: 1000;
    background: #14142a; border: 1px solid #2d2d4a; border-radius: 6px;
    padding: 4px; min-width: 250px; max-height: 80vh; overflow-y: auto;
    box-shadow: 0 6px 20px #0008;
    display: flex; flex-direction: column; gap: 1px;
  }
  .nm-group {
    display: flex; align-items: center; gap: 6px; text-align: left;
    background: none; border: none; cursor: pointer;
    padding: 6px 8px 3px; color: #8a90a8; font-size: 10px;
    letter-spacing: .14em; text-transform: uppercase;
  }
  .nm-group:hover { color: #cde; }
  .nm-caret { width: 10px; font-size: 9px; }
  .nm-item {
    display: flex; justify-content: space-between; gap: 10px;
    padding: 5px 10px 5px 24px; border-radius: 4px;
    color: #cde; text-decoration: none; font-size: 12px; white-space: nowrap;
  }
  .nm-item:hover { background: #23234a; color: #fff; }
  .nm-ext { color: #6f7590; font-size: 10px; }
</style>
