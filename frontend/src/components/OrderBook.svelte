<!-- frontend/src/components/OrderBook.svelte -->
<script lang="ts">
  import { orderbookStore } from '$lib/stores/orderbook.svelte';

  let { symbol }: { symbol: string } = $props();

  const LEVELS = 14;

  let book = $derived(orderbookStore.get(symbol));
  let asks = $derived(book.asks.slice(0, LEVELS));
  let bids = $derived(book.bids.slice(0, LEVELS));
  let asksReversed = $derived([...asks].reverse());

  let maxSize = $derived(
    Math.max(...asks.map(a => a.size), ...bids.map(b => b.size), 1)
  );

  let spread = $derived(
    bids.length && asks.length
      ? (asks[0].price - bids[0].price).toFixed(0)
      : '—'
  );

  function fmt(price: number): string {
    return price.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  }
</script>

<div class="ob">
  <div class="ob-title">Стакан</div>
  {#if !bids.length && !asks.length}
    <div class="ob-empty">Ожидание данных…</div>
  {:else}
    <div class="ob-levels">
      {#each asksReversed as level (level.price)}
        <div class="ob-row ask">
          <div class="ob-bar ask-bar" style="width:{(level.size / maxSize) * 100}%"></div>
          <span class="ob-size">{level.size}</span>
          <span class="ob-price ask-price">{fmt(level.price)}</span>
        </div>
      {/each}
      <div class="ob-spread">спред {spread}</div>
      {#each bids as level (level.price)}
        <div class="ob-row bid">
          <div class="ob-bar bid-bar" style="width:{(level.size / maxSize) * 100}%"></div>
          <span class="ob-size">{level.size}</span>
          <span class="ob-price bid-price">{fmt(level.price)}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .ob {
    display: flex; flex-direction: column;
    background: #0d0d1c;
    border-left: 1px solid #2d2d4a;
    width: 160px; flex-shrink: 0;
    font-size: 11px; font-family: 'JetBrains Mono', 'Consolas', monospace;
    overflow: hidden;
  }
  .ob-title {
    padding: 3px 8px; font-size: 11px; color: #666;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    flex-shrink: 0;
  }
  .ob-empty {
    padding: 16px 8px; color: #444; font-size: 10px; text-align: center;
  }
  .ob-levels {
    flex: 1; overflow: hidden; display: flex; flex-direction: column;
    justify-content: center;
  }
  .ob-row {
    position: relative; display: flex; align-items: center;
    height: 17px; padding: 0 6px; gap: 4px; overflow: hidden;
  }
  .ob-bar {
    position: absolute; top: 0; right: 0; bottom: 0;
    opacity: 0.18; pointer-events: none;
  }
  .ask-bar { background: #f44336; }
  .bid-bar { background: #4caf50; }
  .ob-size {
    color: #666; min-width: 28px; text-align: right; z-index: 1; font-size: 10px;
  }
  .ob-price {
    flex: 1; text-align: right; z-index: 1; font-weight: 500;
  }
  .ask-price { color: #f44336; }
  .bid-price { color: #4caf50; }
  .ob-spread {
    text-align: center; color: #555; font-size: 10px;
    padding: 2px 0; border-top: 1px solid #1e1e3a; border-bottom: 1px solid #1e1e3a;
    background: #111120; flex-shrink: 0;
  }
</style>
