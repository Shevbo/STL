import type { OhlcBar } from '$lib/types';

// Plain (non-proxied) storage. Reactivity via version counter.
let _bars: Record<string, OhlcBar[]> = {};
let _version = $state(0);

function toPlain(b: OhlcBar): OhlcBar {
  return {
    time: b.time,
    open: +b.open,
    high: +b.high,
    low: +b.low,
    close: +b.close,
    volume: +(b.volume ?? 0),
  };
}

export const candlesStore = {
  get(symbol: string): OhlcBar[] {
    // eslint-disable-next-line @typescript-eslint/no-unused-expressions
    _version; // register reactive dependency
    return _bars[symbol] ?? [];
  },
  setHistory(symbol: string, bars: OhlcBar[]): void {
    const seen = new Map<number, OhlcBar>();
    for (const b of bars) seen.set(b.time, toPlain(b));
    _bars[symbol] = [...seen.values()].sort((a, b) => a.time - b.time);
    _version += 1;
  },
  upsertBar(symbol: string, bar: OhlcBar): void {
    const plain = toPlain(bar);
    const arr = _bars[symbol] ? _bars[symbol].slice() : [];
    if (arr.length && arr[arr.length - 1].time === plain.time) {
      arr[arr.length - 1] = plain;
    } else if (!arr.length || plain.time > arr[arr.length - 1].time) {
      arr.push(plain);
      if (arr.length > 500) arr.shift();
    }
    // silently drop out-of-order bars (history already covers them)
    _bars[symbol] = arr;
    _version += 1;
  },
  reset(): void { _bars = {}; _version += 1; },
};
