// Event classification: AVG vs ENF (add against vs add in favour) + unique-id mapping.
import { describe, it, expect } from 'vitest';
import { tradeEvents } from './lab-analytics';

const ev = (fills: any[]) => tradeEvents(fills, 60, 1, 'RIU6', false);

describe('AVG vs ENF — averaging (against) vs enforcement (in favour)', () => {
  it('LONG: add at a LOWER price (against) = AVG; add at a HIGHER price (in favour) = ENF', () => {
    const a = ev([{ time: 1, side: 'buy', qty: 1, price: 100 }, { time: 2, side: 'buy', qty: 1, price: 98 }]);
    expect(a[1].kind).toBe('average');
    const b = ev([{ time: 1, side: 'buy', qty: 1, price: 100 }, { time: 2, side: 'buy', qty: 1, price: 102 }]);
    expect(b[1].kind).toBe('enforce');
  });

  it('SHORT: add at a HIGHER price (against) = AVG; add at a LOWER price (in favour) = ENF', () => {
    const a = ev([{ time: 1, side: 'sell', qty: 1, price: 100 }, { time: 2, side: 'sell', qty: 1, price: 102 }]);
    expect(a[1].kind).toBe('average');
    const b = ev([{ time: 1, side: 'sell', qty: 1, price: 100 }, { time: 2, side: 'sell', qty: 1, price: 98 }]);
    expect(b[1].kind).toBe('enforce');
  });

  it('the add (AVG or ENF) never affects realized P&L — only its label', () => {
    const avg = ev([{ time: 1, side: 'buy', qty: 1, price: 100 }, { time: 2, side: 'buy', qty: 1, price: 98 }, { time: 3, side: 'sell', qty: 2, price: 101 }]);
    const enf = ev([{ time: 1, side: 'buy', qty: 1, price: 100 }, { time: 2, side: 'buy', qty: 1, price: 102 }, { time: 3, side: 'sell', qty: 2, price: 101 }]);
    // both close 2 lots: avg path entry 99 -> +2*2pts; enf path entry 101 -> 0. Fees: two
    // entry fills (0.45 each) carried + the qty-2 close fill (0.45*2). Labels differ; each
    // P&L is the honest avg-cost result (no double counting).
    expect(avg.at(-1)!.close!.pnl).toBeCloseTo((101 - 99) * 2 - 0.9 - 0.9, 6);
    expect(enf.at(-1)!.close!.pnl).toBeCloseTo((101 - 101) * 2 - 0.9 - 0.9, 6);
  });
});

describe('unique order_id mapping (fixes the reverse-collision P&L distortion)', () => {
  it('two identical fills (same time/side/qty/price) keep DISTINCT events, each tagged by order_id', () => {
    // long 1 @100, then ONE signal sells to close (SL) and sells again to open a short — two
    // identical sells @98 at the same second. Value-keying collapses them; order_id does not.
    const events = ev([
      { time: 1, side: 'buy', qty: 1, price: 100, order_id: 'A' },
      { time: 5, side: 'sell', qty: 1, price: 98, order_id: 'B' },   // closes long → SL
      { time: 5, side: 'sell', qty: 1, price: 98, order_id: 'C' },   // opens short → вход
    ]);
    expect(events.map(e => e.id)).toEqual(['A', 'B', 'C']);
    expect(events[1].close).toBeTruthy();          // B is the SL close
    expect(events[1].close!.exit).toBe('SL');
    expect(events[2].kind).toBe('open');           // C is a fresh entry
    expect(events[2].close).toBeUndefined();
    // distinct ids ⇒ a Map keyed by id keeps BOTH; keyed by (time,side,qty,price) keeps one
    const byId = new Map(events.map(e => [e.id, e]));
    expect(byId.size).toBe(3);
  });
});
