<!-- frontend/src/components/OrderBook.svelte -->
<script lang="ts">
  import { orderbookStore } from '$lib/stores/orderbook.svelte';
  import type { OrderRequest } from '$lib/types';

  let {
    symbol,
    onOpenOrder,
  }: {
    symbol: string;
    onOpenOrder?: (order: Omit<OrderRequest, 'quantity'>) => void;
  } = $props();

  const LEVELS = 20;

  let book = $derived.by(() => orderbookStore.get(symbol));
  let asks = $derived.by(() => book.asks.slice(0, LEVELS));
  let bids = $derived.by(() => book.bids.slice(0, LEVELS));
  let asksReversed = $derived.by(() => [...asks].reverse());

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

  function clickAsk(price: number): void {
    onOpenOrder?.({ symbol, side: 'buy', order_type: 'limit', price });
  }

  function clickBid(price: number): void {
    onOpenOrder?.({ symbol, side: 'sell', order_type: 'limit', price });
  }
</script>

<div class="ob">
  <div class="ob-title">Стакан</div>
  {#if !bids.length && !asks.length}
    <div class="ob-empty">Ожидание данных…</div>
  {:else}
    <div class="ob-levels">
      <div class="ob-asks">
        {#each asksReversed as level (level.price)}
          <button class="ob-row ask" onclick={() => clickAsk(level.price)}>
            <div class="ob-bar ask-bar" style="width:{(level.size / maxSize) * 100}%"></div>
            <span class="ob-size">{level.size}</span>
            <span class="ob-price ask-price">{fmt(level.price)}</span>
          </button>
        {/each}
      </div>
      <div class="ob-spread">спред {spread}</div>
      <div class="ob-bids">
        {#each bids as level (level.price)}
          <button class="ob-row bid" onclick={() => clickBid(level.price)}>
            <div class="ob-bar bid-bar" style="width:{(level.size / maxSize) * 100}%"></div>
            <span class="ob-size">{level.size}</span>
            <span class="ob-price bid-price">{fmt(level.price)}</span>
          </button>
        {/each}
      </div>
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
    overflow: hidden; align-self: stretch;
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
    min-height: 0;
  }
  /* Both sides scroll vertically so all N levels are reachable (the box shows ~15-20 of
     50); the best price stays anchored at the spread (asks bottom / bids top). */
  .ob-asks {
    flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
    overflow-y: auto; overflow-x: hidden;
  }
  .ob-bids {
    flex: 1; display: flex; flex-direction: column;
    overflow-y: auto; overflow-x: hidden;
  }
  .ob-asks::-webkit-scrollbar, .ob-bids::-webkit-scrollbar { width: 7px; }
  .ob-asks::-webkit-scrollbar-thumb, .ob-bids::-webkit-scrollbar-thumb {
    background: #2d2d4a; border-radius: 4px;
  }
  .ob-asks::-webkit-scrollbar-thumb:hover, .ob-bids::-webkit-scrollbar-thumb:hover { background: #3d3d5a; }
  .ob-asks, .ob-bids { scrollbar-width: thin; scrollbar-color: #2d2d4a transparent; }
  .ob-row {
    position: relative; display: flex; align-items: center; flex-shrink: 0;
    height: 17px; padding: 0 6px; gap: 4px; overflow: hidden;
    cursor: pointer; background: transparent; border: none; width: 100%;
    text-align: left;
  }
  .ob-row:hover { background: rgba(255,255,255,0.04); }
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
