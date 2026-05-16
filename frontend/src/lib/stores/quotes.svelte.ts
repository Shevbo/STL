// frontend/src/lib/stores/quotes.svelte.ts
import type { Quote } from '$lib/types';

let _all = $state<Record<string, Quote>>({});

export const quotesStore = {
  get all(): Record<string, Quote> { return _all; },
  update(symbol: string, q: Quote): void { _all[symbol] = q; },
  get(symbol: string): Quote | undefined { return _all[symbol]; },
  reset(): void { _all = {}; },
};
