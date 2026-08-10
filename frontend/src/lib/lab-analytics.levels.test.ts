import { describe, it, expect } from 'vitest';
import { tpSlByLevel, type TradeEvent } from './lab-analytics';

// Minimal closing-event factory (only the fields tpSlByLevel reads).
function close(maxContracts: number, pnl: number): TradeEvent {
  return {
    time: 0, rawTime: 0, side: 'sell', qty: 1, price: 0, kind: 'full', posAfter: 0, label: '',
    close: { holdSecs: 0, maxContracts, pnl, exit: pnl >= 0 ? 'plus' : 'minus', partial: false, exitLabel: '' },
  };
}

describe('tpSlByLevel', () => {
  it('bins closes by peak contracts, splitting TP/SL and summing pnl', () => {
    const events = [
      close(1, 100), close(1, 120), close(1, -40),   // level 1: 2 TP / 1 SL
      close(3, 300), close(3, -5000),                // level 3: 1 TP / 1 SL (the SL is huge)
    ];
    const stats = tpSlByLevel(events);
    expect(stats.map((s) => s.level)).toEqual([1, 3]);   // sorted by level

    const l1 = stats[0];
    expect(l1).toMatchObject({ level: 1, tp: 2, sl: 1, tpPnl: 220, slPnl: -40 });

    const l3 = stats[1];
    expect(l3).toMatchObject({ level: 3, tp: 1, sl: 1, tpPnl: 300, slPnl: -5000 });
    // The point of the metric: level 3 has a 1:1 count but is net-negative (no stop-loss
    // → the one deep SL dwarfs the deep TP). Count ratio alone would look fine.
    expect(l3.tpPnl + l3.slPnl).toBeLessThan(0);
    expect(l1.tpPnl + l1.slPnl).toBeGreaterThan(0);
  });

  it('ignores non-closing events and empty input', () => {
    const open: TradeEvent = { time: 0, rawTime: 0, side: 'buy', qty: 1, price: 0, kind: 'open', posAfter: 1, label: '' };
    expect(tpSlByLevel([open])).toEqual([]);
    expect(tpSlByLevel([])).toEqual([]);
  });
});
