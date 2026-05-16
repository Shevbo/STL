// frontend/src/lib/stores/robots.svelte.ts
import type { Robot } from '$lib/types';

let _all = $state<Robot[]>([]);

export const robotsStore = {
  get all(): Robot[] { return _all; },
  set(robots: Robot[]): void { _all = robots; },
  updatePnl(id: string, pnl: number): void {
    const r = _all.find(r => r.id === id);
    if (r) r.pnl = pnl;
  },
  reset(): void { _all = []; },
};
