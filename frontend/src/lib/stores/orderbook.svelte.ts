import type { OrderBookLevel } from '$lib/types';

export interface OrderBook {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

const _empty: OrderBook = { bids: [], asks: [] };
let _books = $state<Record<string, OrderBook>>({});

export const orderbookStore = {
  get(symbol: string): OrderBook { return _books[symbol] ?? _empty; },
  set(symbol: string, book: OrderBook): void { _books[symbol] = book; },
  reset(): void { _books = {}; },
};
