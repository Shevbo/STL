import type { Position } from '$lib/types';

let _positions = $state<Position[]>([]);

export const positionsStore = {
  get all(): Position[] { return _positions; },
  set(positions: Position[]): void { _positions = positions; },
  reset(): void { _positions = []; },
};
