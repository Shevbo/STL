import type { OrderBookLevel } from '$lib/types';

export interface OrderBook {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

const _empty: OrderBook = { bids: [], asks: [] };
let _books = $state<Record<string, OrderBook>>({});
let _version = $state(0);

export const orderbookStore = {
  get(symbol: string): OrderBook {
    // eslint-disable-next-line @typescript-eslint/no-unused-expressions
    _version; // register reactive dependency
    return _books[symbol] ?? _empty;
  },
  set(symbol: string, book: OrderBook): void {
    _books[symbol] = book;
    _version += 1;
  },
  clear(symbol: string): void {
    delete _books[symbol];
    _version += 1;
  },
  reset(): void {
    _books = {};
    _version += 1;
  },
};
