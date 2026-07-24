<!-- Единая точка входа во всё, что касается ручных заявок.
     Раньше это жило в трёх местах: кнопка «Заявки», отдельная кнопка «Графики
     поз./заявок» и свёрнутая панель умных заявок в правой колонке. Оператор
     искал, где что. Теперь одна кнопка и три вкладки. -->
<script lang="ts">
  import Orders from '../Orders.svelte';
  import ChartsGrid from '../ChartsGrid.svelte';
  import SmartOrders from './SmartOrders.svelte';
  import { smartOrdersStore } from '$lib/stores/smart-orders.svelte';
  import { onMount, onDestroy } from 'svelte';

  let { symbol = '' }: { symbol?: string } = $props();

  type Tab = 'plain' | 'smart' | 'charts';
  let tab = $state<Tab>('plain');
  let unsub: (() => void) | null = null;

  // Счётчик взведённых нужен на корешке вкладки, даже когда открыта другая:
  // умная заявка это то, что сработает без вас.
  onMount(() => { unsub = smartOrdersStore.subscribe(5000); });
  onDestroy(() => unsub?.());
  const armed = $derived(smartOrdersStore.armed.length);
</script>

<div class="of">
  <nav class="of-tabs">
    <button class:on={tab === 'plain'} onclick={() => tab = 'plain'}>Обычная заявка</button>
    <button class:on={tab === 'smart'} onclick={() => tab = 'smart'}>
      Умные заявки{#if armed} <span class="of-cnt">{armed}</span>{/if}
    </button>
    <button class:on={tab === 'charts'} onclick={() => tab = 'charts'}>Позиции и заявки на графиках</button>
  </nav>
  <div class="of-body">
    {#if tab === 'plain'}<Orders />
    {:else if tab === 'smart'}<SmartOrders {symbol} />
    {:else}<ChartsGrid />{/if}
  </div>
</div>

<style>
  .of { display: flex; flex-direction: column; height: 100%; background: #0f0f1e; }
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
  .of-cnt {
    background: #e0a53c; color: #14142a; border-radius: 8px;
    padding: 0 5px; font-size: 10px; font-weight: 600;
  }
  .of-body { flex: 1; min-height: 0; overflow: hidden; display: flex; }
  .of-body > :global(*) { flex: 1; min-width: 0; }
</style>
