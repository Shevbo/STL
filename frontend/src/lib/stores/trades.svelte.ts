import type { TradeFill } from '$lib/types';

let _trades = $state<TradeFill[]>([]);

export const tradesStore = {
  get all(): TradeFill[] { return _trades; },
  forSymbol(symbol: string): TradeFill[] { return _trades.filter(t => t.symbol === symbol); },
  set(trades: TradeFill[]): void { _trades = trades; },
  reset(): void { _trades = []; },
};
