// Molecule validation on REAL production fills (robot paper-fvg-RIM6, 161 fills,
// RIM6 → RIU6 roll). This is method (2) of the three-way cross-check: the production
// rolledPnl() must reproduce the number the independent Python script computed
// (scripts/validate_fvg_pnl.py) to the ruble, and must NOT reproduce the phantom that
// the naive single-book replay (the bug) produces.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, it, expect } from 'vitest';
import { rolledPnl, tradeEvents, positionRects, type RawFill } from './lab-analytics';

const PV = { RIM6: 1.438154, RIU6: 1.449026 };   // real point values from instrument_meta

const here = dirname(fileURLToPath(import.meta.url));
const csv = readFileSync(join(here, '__fixtures__', 'fvg-rim6-fills.csv'), 'utf8');
const fills: RawFill[] = csv.trim().split('\n').map(line => {
  const [symbol, side, qty, price, time] = line.split(',');
  return { symbol, side: side as 'buy' | 'sell', qty: Number(qty), price: Number(price), time: Number(time) };
});

describe('real FVG RI robot — rolledPnl reproduces the molecule numbers', () => {
  it('loads the 161 real fills (RIM6 then RIU6)', () => {
    expect(fills.length).toBe(161);
    expect(fills.filter(f => f.symbol === 'RIM6').length).toBe(47);
    expect(fills.filter(f => f.symbol === 'RIU6').length).toBe(114);
  });

  it('per-contract realized matches Python: RIM6 +39,582 / RIU6 +38,004 (RIU6 also == chart overlay)', () => {
    const r = rolledPnl(fills, PV, false, { settleCarried: false });
    const rim6 = r.byContract.find(c => c.symbol === 'RIM6')!;
    const riu6 = r.byContract.find(c => c.symbol === 'RIU6')!;
    expect(Math.round(rim6.net)).toBe(39582);
    expect(rim6.closes).toBe(11);
    expect(Math.round(riu6.net)).toBe(38004);   // independently equals the BacktestChart overlay
    expect(riu6.closes).toBe(20);
  });

  it('roll-aware total (force-close carried RIM6 short at settlement) = +77,046', () => {
    const r = rolledPnl(fills, PV, false, { settleCarried: true });
    expect(Math.round(r.net)).toBe(77046);   // strict 77,586 minus the settled RIM6 carry (incl. its entry+exit fees)
    expect(r.peakContracts).toBe(9);            // never 18
    expect(r.position).toBe(-9);                // current RIU6 short, unrealized
    expect(r.currentSymbol).toBe('RIU6');
  });

  it('strict realized (no force-close) = +77,586; sum of per-contract == total', () => {
    const r = rolledPnl(fills, PV, false, { settleCarried: false });
    expect(Math.round(r.net)).toBe(77586);
    expect(Math.round(r.byContract.reduce((a, c) => a + c.net, 0))).toBe(77586);
  });

  it('position rectangles never span the roll — each box stays within one contract price band', () => {
    const r = rolledPnl(fills, PV, false);
    const last = fills[fills.length - 1];
    const rects = positionRects(r.events, last.time, last.price);
    expect(rects.length).toBeGreaterThan(5);
    // RIM6 ~107-110k, RIU6 ~95-104k. A box spanning the roll would jump ~12k; within a
    // contract, entry→exit moves are small. This proves boxes are per-contract.
    expect(Math.max(...rects.map(b => Math.abs(b.pIn - b.pOut)))).toBeLessThan(6000);
    expect(rects.filter(b => b.open).length).toBe(1);          // one live (open) box = current RIU6 short
    for (const b of rects) { expect(b.pIn).toBeGreaterThan(80000); expect(b.pOut).toBeGreaterThan(80000); }
  });

  it('the NAIVE single-book replay reproduces the +228,528 phantom (3.0x) — the bug we are removing', () => {
    const naive = tradeEvents(fills, 60, PV.RIU6, '', false)
      .reduce((a, e) => a + (e.close ? e.close.pnl : 0), 0);
    expect(Math.round(naive)).toBe(228528);
    expect(naive / rolledPnl(fills, PV, false).net).toBeGreaterThan(2.9);
  });
});
