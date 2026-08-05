<!-- Единая точка входа во всё, что касается ручных заявок.
     Раньше это жило в трёх местах: кнопка «Заявки», отдельная кнопка «Графики
     поз./заявок» и свёрнутая панель умных заявок в правой колонке. Оператор
     искал, где что. Теперь одна кнопка и три вкладки. -->
<script lang="ts">
  import ScreenTag from '../lab/ScreenTag.svelte';
  import Orders from '../Orders.svelte';
  import ChartsGrid from '../ChartsGrid.svelte';
  import SmartOrders from './SmartOrders.svelte';
  import { smartOrdersStore } from '$lib/stores/smart-orders.svelte';
  import { onMount, onDestroy } from 'svelte';

  let { symbol = '' }: { symbol?: string } = $props();

  type Tab = 'plain' | 'smart' | 'charts';
  let tab = $state<Tab>('plain');
  let unsub: (() => void) | null = null;
  // Разворот во весь экран — как у панели LAB: в обычном режиме фрейм занимает
  // полосу внизу, а таблица заявок и графики позиций там задыхаются.
  let fullscreen = $state(false);
  function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && fullscreen) fullscreen = false; }

  // Счётчик взведённых нужен на корешке вкладки, даже когда открыта другая:
  // умная заявка это то, что сработает без вас.
  onMount(() => { unsub = smartOrdersStore.subscribe(5000); });
  onDestroy(() => unsub?.());
  const armed = $derived(smartOrdersStore.armed.length);
</script>

<svelte:window onkeydown={onKey} />

<div class="of" class:fullscreen>
  <ScreenTag id="ORDERS" name="ручные заявки" corner="tl" />
  <nav class="of-tabs">
    <button class:on={tab === 'plain'} onclick={() => tab = 'plain'}>Обычная заявка</button>
    <button class:on={tab === 'smart'} onclick={() => tab = 'smart'}>
      Умные заявки{#if armed} <span class="of-cnt">{armed}</span>{/if}
    </button>
    <button class:on={tab === 'charts'} onclick={() => tab = 'charts'}>Позиции и заявки на графиках</button>
    <button class="of-full" title={fullscreen ? 'Свернуть (Esc)' : 'Развернуть на весь экран'}
            onclick={() => fullscreen = !fullscreen}>
      {fullscreen ? '⊟ Свернуть' : '⛶ Во весь экран'}</button>
  </nav>
  <div class="of-body">
    {#if tab === 'plain'}<Orders />
    {:else if tab === 'smart'}<SmartOrders {symbol} />
    {:else}<ChartsGrid />{/if}
  </div>
</div>

<style>
  .of { display: flex; flex-direction: column; height: 100%; background: #0f0f1e; position: relative; }
  /* Во весь экран: поверх всего, включая шапку терминала. z-index 1000 — тот же
     ярус, что у панели LAB (они не показываются одновременно). */
  /* Размер задаёт inset, а не 100vw/100vh: vw включает ширину полосы прокрутки и
     сам порождает горизонтальный скролл на странице. */
  .of.fullscreen { position: fixed; inset: 0; z-index: 1000; }
  /* Развернули — значит нужен ОБЪЁМ. Таблица «В работе» рассчитана на полосу
     внизу экрана (max-height 180px) и в полный рост оставляла полэкрана пустым.
     Здесь она забирает свободную высоту, скроллится сама, а не страница. */
  .of.fullscreen :global(.orders) { overflow: hidden; }
  .of.fullscreen :global(.gw-working) { max-height: none; flex: 1 1 auto; min-height: 120px; }
  .of.fullscreen :global(.gw-exec) { max-height: 30vh; }
  /* Графики позиций: в полосе внизу плитки 280x200 уместны, в полный рост они
     жались в угол, оставляя 85% пустоты. auto-fit вместо auto-fill схлопывает
     пустые дорожки, поэтому две позиции занимают всю ширину. */
  .of.fullscreen :global(.gf-grid) {
    grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  }
  .of.fullscreen :global(.gf-grid .mini) { height: min(46vh, 440px); }
  /* Умные заявки намеренно ограничены по ширине (длинная строка объяснения
     нечитаема). Растягивать нельзя — центрируем, иначе в полный рост пустота
     липнет к правому краю и выглядит как обрезанная вёрстка. */
  .of.fullscreen :global(.so) { margin-inline: auto; }
  .of-tabs { display: flex; gap: 2px; padding: 5px 10px 0; border-bottom: 1px solid #2d2d4a; flex-shrink: 0; }
  .of-tabs button {
    background: none; border: 1px solid transparent; border-bottom: none;
    color: #8a90a8; padding: 6px 14px; cursor: pointer; font-size: 12px;
  }
  .of-tabs button:hover { color: #e8e8f0; }
  .of-tabs button.on {
    color: #e8e8f0; background: #14142a; border-color: #2d2d4a;
    position: relative; top: 1px;
  }
  .of-full { margin-left: auto; color: #8a90a8; border: 1px solid #2d2d4a; border-radius: 3px;
    align-self: center; margin-bottom: 4px; }
  .of-full:hover { color: #4caf50; border-color: #4caf5066; }
  .of-cnt {
    background: #e0a53c; color: #14142a; border-radius: 8px;
    padding: 0 5px; font-size: 10px; font-weight: 600;
  }
  .of-body { flex: 1; min-height: 0; overflow: hidden; display: flex; }
  .of-body > :global(*) { flex: 1; min-width: 0; }

  /* Телефон: вкладки крупнее и на всю ширину — попасть пальцем, не целясь. */
  @media (max-width: 820px) {
    .of-tabs { gap: 2px; }
    .of-tabs button { flex: 1 1 auto; min-height: 40px; font-size: 14px; }
    .of-body { overflow-y: auto; -webkit-overflow-scrolling: touch; }
  }
</style>
