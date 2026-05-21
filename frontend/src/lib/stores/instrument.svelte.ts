import type { InstrumentMeta } from '$lib/types';

let _list = $state<InstrumentMeta[]>([]);
let _params = $state<Record<string, unknown>>({});

export const instrumentStore = {
  get list(): InstrumentMeta[] { return _list; },
  get params(): Record<string, unknown> { return _params; },
  setList(instruments: InstrumentMeta[]): void { _list = instruments; },
  setParams(params: Record<string, unknown>): void { _params = params; },
  reset(): void { _list = []; _params = {}; },
};
