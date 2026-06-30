// QUIK market-data bridge: feed the QUIK agent's order book into the main orderbookStore
// so the main стакан shows QUIK instruments (e.g. GZU6) like Finam ones. The main book/
// chart are otherwise fed by the Finam ws; QUIK instruments live behind the agent and are
// fetched over /api/v1/quik. Order books only here (candles need OHLC bars the agent does
// not provide). Symbol "GZU6@RTSX" maps to the QUIK code "GZU6".

import { orderbookStore } from './stores/orderbook.svelte';

function quikCode(symbol: string): string {
  return (symbol || '').split('@')[0].trim();
}

let whitelist: string[] = [];
let agentId = '';
let lastMeta = 0;

async function refreshMeta(): Promise<void> {
  const now = Date.now();
  if (now - lastMeta < 30000) return;
  lastMeta = now;
  try {
    const r = await fetch('/api/v1/quik/orders/config', { credentials: 'include' });
    if (r.ok) whitelist = (await r.json()).instrument_whitelist ?? [];
  } catch { /* keep previous */ }
  try {
    const r = await fetch('/api/v1/quik/status', { credentials: 'include' });
    if (r.ok) {
      const d = await r.json();
      const green = (d.agents ?? []).find((a: { link: string }) => a.link === 'green');
      if (green) agentId = green.agent_id;
    }
  } catch { /* keep previous */ }
}

/** Poll the QUIK order book for the currently selected symbol and feed orderbookStore.
 *  Returns a stop function. getSymbol() is read each tick so symbol switches are picked up
 *  without restarting the bridge. */
export function startQuikOrderbookBridge(getSymbol: () => string): () => void {
  let stopped = false;

  const tick = async (): Promise<void> => {
    if (stopped) return;
    await refreshMeta();
    const sym = getSymbol();
    const code = quikCode(sym);
    if (!code || !whitelist.includes(code)) return; // not a QUIK instrument
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
      const r = await fetch(`/api/v1/quik/orderbook/${encodeURIComponent(code)}${q}`, {
        credentials: 'include',
      });
      if (!r.ok) return;
      const d = await r.json();
      const map = (l: { price: number; quantity: number }) => ({ price: l.price, size: l.quantity });
      const bids = (d.bids ?? []).map(map);
      const asks = (d.asks ?? []).map(map);
      if (bids.length || asks.length) {
        // Key by the FULL UI symbol so the main OrderBook (get(effectiveSymbol)) matches.
        // setQuik claims the symbol so the Finam ws does not overwrite the live QUIK book
        // (the two racing was the SRU6 стакан flicker).
        orderbookStore.setQuik(sym, { bids, asks });
      }
    } catch { /* ignore transient */ }
  };

  const timer = setInterval(tick, 1000);
  void tick();
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
