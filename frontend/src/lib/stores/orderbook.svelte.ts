import type { OrderBookLevel } from '$lib/types';

export interface OrderBook {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

const _empty: OrderBook = { bids: [], asks: [] };
let _books = $state<Record<string, OrderBook>>({});
let _version = $state(0);

// QUIK-source ownership: a symbol fed by the QUIK agent bridge (whitelist instruments
// like SRU6) is owned by QUIK for QUIK_TTL_MS after each QUIK write. While owned, a
// Finam ws write for the SAME symbol is IGNORED. Without this, the Finam ws and the
// QUIK bridge both wrote orderbookStore[symbol] and the стакан flickered between the two
// books (e.g. SRU6 jumping ~28100 ↔ ~28490) — a stale Finam overlay racing the live
// QUIK book. Ownership expires so Finam takes over again if the bridge stops.
const QUIK_TTL_MS = 4000;
const _quikClaim: Record<string, number> = {};

export const orderbookStore = {
  get(symbol: string): OrderBook {
    // eslint-disable-next-line @typescript-eslint/no-unused-expressions
    _version; // register reactive dependency
    return _books[symbol] ?? _empty;
  },
  // Finam ws source: defers to a live QUIK claim for the same symbol.
  set(symbol: string, book: OrderBook): void {
    const claim = _quikClaim[symbol];
    if (claim && Date.now() - claim < QUIK_TTL_MS) return; // QUIK owns this symbol now
    _books[symbol] = book;
    _version += 1;
  },
  // QUIK agent-bridge source: claims the symbol (priority) and writes.
  setQuik(symbol: string, book: OrderBook): void {
    _quikClaim[symbol] = Date.now();
    _books[symbol] = book;
    _version += 1;
  },
  clear(symbol: string): void {
    delete _books[symbol];
    delete _quikClaim[symbol];
    _version += 1;
  },
  reset(): void {
    _books = {};
    _version += 1;
  },
};
