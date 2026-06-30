<!-- frontend/src/components/ChartsGrid.svelte
  One frame, every instrument that matters: a compact chart for each symbol that is
  currently IN A POSITION or has WORKING ORDERS (Finam open orders + QUIK orders). The
  set updates live as positions/orders change; an empty set shows a hint. -->
<script lang="ts">
  import { positionsStore } from '$lib/stores/positions.svelte';
  import { ordersStore } from '$lib/stores/orders.svelte';
  import { instrumentStore } from '$lib/stores/instrument.svelte';
  import MiniChart from './MiniChart.svelte';

  type Entry = {
    symbol: string;
    label: string;
    badge: string;
    badgeKind: 'long' | 'short' | 'neutral';
  };

  // QUIK working orders are polled here (their codes -> chart symbols are CODE@RTSX).
  let quikCodes = $state<string[]>([]);
  async function loadQuik() {
    try {
      const r = await fetch('/api/v1/quik/orders/working', { credentials: 'include' });
      if (!r.ok) return;
      const rows: { code: string; state: string }[] = (await r.json()).orders ?? [];
      const active = rows.filter((o) => ['pending', 'active', 'partial'].includes(o.state));
      quikCodes = [...new Set(active.map((o) => o.code))];
    } catch { /* keep previous */ }
  }
  $effect(() => {
    loadQuik();
    const t = setInterval(loadQuik, 4000);
    return () => clearInterval(t);
  });

  function tickerOf(symbol: string): string {
    const found = instrumentStore.list.find((i) => i.symbol === symbol);
    return found?.ticker || symbol.split('@')[0] || symbol;
  }

  // Build the deduped entry list: positions first (with side/qty), then Finam orders,
  // then QUIK orders. A symbol already shown gets its badge enriched, not duplicated.
  let entries = $derived.by<Entry[]>(() => {
    const map = new Map<string, Entry>();

    for (const p of positionsStore.all) {
      if (!p.symbol || !p.quantity || p.side === 'flat') continue;
      map.set(p.symbol, {
        symbol: p.symbol,
        label: tickerOf(p.symbol),
        badge: `${p.side === 'short' ? 'Short' : 'Long'} ${Math.abs(p.quantity)}`,
        badgeKind: p.side === 'short' ? 'short' : 'long',
      });
    }

    const orderCount = new Map<string, number>();
    for (const o of ordersStore.all) {
      if (!o.symbol) continue;
      orderCount.set(o.symbol, (orderCount.get(o.symbol) ?? 0) + 1);
    }
    for (const [symbol, n] of orderCount) {
      const e = map.get(symbol);
      if (e) e.badge += ` · ${n} заяв.`;
      else map.set(symbol, { symbol, label: tickerOf(symbol), badge: `${n} заяв.`, badgeKind: 'neutral' });
    }

    for (const code of quikCodes) {
      const symbol = code.includes('@') ? code : `${code}@RTSX`;
      const e = map.get(symbol);
      if (e) { if (!e.badge.includes('QUIK')) e.badge += ' · QUIK'; }
      else map.set(symbol, { symbol, label: tickerOf(symbol), badge: 'QUIK заяв.', badgeKind: 'neutral' });
    }

    return [...map.values()];
  });
</script>

<div class="grid-frame">
  <div class="gf-head">
    <span class="gf-title">Графики: позиции и заявки</span>
    <span class="gf-count">{entries.length}</span>
  </div>
  {#if !entries.length}
    <div class="gf-empty">Нет открытых позиций и активных заявок.</div>
  {:else}
    <div class="gf-grid">
      {#each entries as e (e.symbol)}
        <MiniChart symbol={e.symbol} label={e.label} badge={e.badge} badgeKind={e.badgeKind} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .grid-frame { display: flex; flex-direction: column; height: 100%; background: #14142a; overflow: hidden; }
  .gf-head {
    display: flex; align-items: center; gap: 8px; flex-shrink: 0;
    padding: 4px 8px; border-bottom: 1px solid #2d2d4a; font-size: 12px;
  }
  .gf-title { color: #9ab; font-weight: 600; }
  .gf-count {
    background: #1a1a2e; color: #cde; border: 1px solid #2d2d4a; border-radius: 10px;
    padding: 0 8px; font-size: 11px;
  }
  .gf-empty { padding: 16px; color: #667; font-size: 12px; }
  .gf-grid {
    flex: 1; min-height: 0; overflow: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 8px; padding: 8px;
  }
  .gf-grid :global(.mini) { height: 200px; }
</style>
