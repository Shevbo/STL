<!-- AgentBookPane: compact QUIK order book (10 asks / 10 bids) for the agent-robot
     showcase, polled from the agent's QLua feed (getQuoteLevel2) via STL. Colors match the chart's
     trade markers (teal = buy side, rose = sell side). -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let { symbol, agentId = null, depth = 10 }:
    { symbol: string; agentId?: string | null; depth?: number } = $props();

  type Lvl = { price: number; qty: number };
  let bids = $state<Lvl[]>([]);
  let asks = $state<Lvl[]>([]);
  let ageS = $state<number | null>(null);
  let err = $state('');

  const maxQty = $derived(Math.max(1, ...bids.map(l => l.qty), ...asks.map(l => l.qty)));
  const bestBid = $derived(bids[0]?.price ?? 0);
  const bestAsk = $derived(asks[0]?.price ?? 0);
  const spread = $derived(bestBid && bestAsk ? bestAsk - bestBid : 0);

  function norm(levels: any[]): Lvl[] {
    return (levels ?? [])
      .map((l: any) => ({ price: Number(l.price ?? 0), qty: Number(l.quantity ?? l.qty ?? 0) }))
      .filter(l => l.price > 0 && l.qty > 0)
      .slice(0, depth);
  }

  async function load() {
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
      const res = await fetchWithAuth(`/api/v1/quik/orderbook/${encodeURIComponent(symbol)}${q}`);
      if (!res.ok) { err = res.status === 404 ? 'нет стакана' : `HTTP ${res.status}`; return; }
      const d = await res.json();
      bids = norm(d.bids);           // best-first from the agent
      asks = norm(d.asks);
      ageS = d.received_at_unix_ms ? Math.max(0, Math.round((Date.now() - Number(d.received_at_unix_ms)) / 1000)) : null;
      err = '';
    } catch (e) { err = String(e); }
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(() => { load(); timer = setInterval(load, 1000); });
  onDestroy(() => { if (timer) clearInterval(timer); });

  const fmtP = (p: number) => Math.round(p).toLocaleString('ru-RU');
</script>

<div class="book" class:stale={ageS !== null && ageS > 15}>
  <div class="b-head">
    <span class="b-title">Стакан {symbol}</span>
    <span class="b-age" class:ok={ageS !== null && ageS <= 15}>{ageS === null ? '—' : ageS + 'с'}</span>
  </div>
  {#if err}
    <div class="b-err">{err === 'нет стакана' ? `Нет данных стакана: открой окно «Стакан ${symbol}» в QUIK и держи его открытым — getQuoteLevel2 отдаёт уровни только при открытом стакане (как лента при открытой «Таблице всех сделок»)` : err}</div>
  {:else}
    <div class="b-rows">
      {#each [...asks].slice(0, depth).reverse() as l}
        <div class="row ask">
          <span class="bar" style="width:{Math.min(100, l.qty / maxQty * 100)}%"></span>
          <span class="qty">{l.qty}</span>
          <span class="price">{fmtP(l.price)}</span>
        </div>
      {/each}
      <div class="mid">
        <span class="spread">спред {spread > 0 ? fmtP(spread) : '—'}</span>
      </div>
      {#each bids.slice(0, depth) as l}
        <div class="row bid">
          <span class="bar" style="width:{Math.min(100, l.qty / maxQty * 100)}%"></span>
          <span class="qty">{l.qty}</span>
          <span class="price">{fmtP(l.price)}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .book { width: 172px; flex: 0 0 172px; display: flex; flex-direction: column; min-height: 0;
          background: #0a0a15; border-right: 1px solid #1a1a2e; }
  .book.stale { opacity: 0.55; }
  .b-head { display: flex; justify-content: space-between; align-items: center;
            padding: 4px 8px; border-bottom: 1px solid #1a1a2e; flex-shrink: 0; }
  .b-title { font-size: 10px; color: #667; text-transform: uppercase; letter-spacing: 0.5px; }
  .b-age { font-size: 10px; color: #f44336; font-family: monospace; }
  .b-age.ok { color: #4caf50; }
  .b-err { padding: 10px 8px; font-size: 10px; color: #b96; line-height: 1.5; }

  .b-rows { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column;
            justify-content: center; padding: 2px 0; }
  .row { position: relative; display: flex; justify-content: space-between; align-items: center;
         padding: 0 8px; height: 17px; font-family: monospace; font-size: 11px; }
  .row .bar { position: absolute; right: 0; top: 1px; bottom: 1px; opacity: 0.16; border-radius: 1px; transition: width 220ms ease; }
  .row.ask .bar { background: #ff5c8a; }
  .row.bid .bar { background: #2ee6a6; }
  .row .qty { color: #99a; z-index: 1; }
  .row .price { z-index: 1; }
  .row.ask .price { color: #ff5c8a; }
  .row.bid .price { color: #2ee6a6; }

  .mid { display: flex; justify-content: center; padding: 3px 0; margin: 2px 0;
         border-top: 1px dashed #22224a; border-bottom: 1px dashed #22224a; }
  .spread { font-size: 10px; color: #778; font-family: monospace; }
</style>
