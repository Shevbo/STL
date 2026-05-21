import type { OpenOrder } from '$lib/types';

let _orders = $state<OpenOrder[]>([]);

export const ordersStore = {
  get all(): OpenOrder[] { return _orders; },
  forSymbol(symbol: string): OpenOrder[] { return _orders.filter(o => o.symbol === symbol); },
  set(orders: OpenOrder[]): void { _orders = orders; },
  reset(): void { _orders = []; },
};
