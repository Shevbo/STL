// Roll-aware P&L: a robot that rolled RIM6 → RIU6 traded two instruments at very
// different price levels. Realized P&L must be per-contract; pairing across contracts
// invents phantom profit. These tests validate the corrected number THREE ways:
//   (1) rolledPnl() aggregate
//   (2) an independent per-contract replay written here (different code)
//   (3) Σ of the per-contract breakdown == the aggregate
// and document the phantom the naive single-book replay produces.
import { describe, it, expect } from 'vitest';
import { rolledPnl, tradeEvents, type RawFill } from './lab-analytics';

const PV = 1.449026;   // RI point value (₽/point), from instrument_meta
const BROKER = 0.45;   // Finam maker fee, ₽/contract

// Independent reference: realized net for ONE contract's fills (maker model).
// Mirrors the accounting (close fill fee + carried entry/averaging fees) but is a
// separate implementation from lab-analytics — method (2) of the cross-check.
function handContractNet(fills: RawFill[]): number {
  let pos = 0, avg = 0, carried = 0, net = 0;
  for (const f of fills) {
    const q = Number(f.qty) || 1;
    const signed = f.side === 'buy' ? q : -q;
    const broker = BROKER * q;
    if (pos === 0) { avg = f.price; pos = signed; carried = broker; }
    else if (Math.sign(pos) === Math.sign(signed)) {
      avg = (avg * Math.abs(pos) + f.price * q) / (Math.abs(pos) + q);
      pos += signed; carried += broker;
    } else {
      const dir = Math.sign(pos);
      const closeQty = Math.min(Math.abs(pos), q);
      const pts = dir > 0 ? (f.price - avg) * closeQty : (avg - f.price) * closeQty;
      net += pts * PV - broker - carried;
      pos += signed;
      if (pos === 0) { avg = 0; carried = 0; }
    }
  }
  return net;
}

// Naive single-book replay (the BUG): all fills as one instrument.
function naiveNet(fills: RawFill[], symbol = ''): number {
  const evs = tradeEvents(fills, 60, PV, symbol, false);
  return evs.reduce((a, e) => a + (e.close ? e.close.pnl : 0), 0);
}

describe('rolledPnl — phantom profit when a position carries across the roll', () => {
  // Robot short 9 on RIM6 (left open at expiry), then short 9 on RIU6, then closes RIU6.
  // Naive pairs the RIM6 sell @110910 against the RIU6 buy @94800 → fake ~16k/contract.
  const fills: RawFill[] = [
    { symbol: 'RIM6', time: 100, side: 'sell', qty: 9, price: 110910 },
    { symbol: 'RIU6', time: 200, side: 'sell', qty: 9, price: 95000 },
    { symbol: 'RIU6', time: 300, side: 'buy', qty: 9, price: 94800 },
  ];

  it('realizes ONLY the RIU6 round-trip; RIM6 open position is unrealized (not phantom profit)', () => {
    const r = rolledPnl(fills, PV, false, { settleCarried: false });   // strict: leave the carry open
    const riu6 = handContractNet(fills.filter(f => f.symbol === 'RIU6'));
    expect(r.net).toBeCloseTo(riu6, 6);          // method (1) == method (2)
    expect(r.closes).toBe(1);
  });

  it('never doubles margin: peak contracts = 9 (one contract), not 18 (9+9 summed)', () => {
    const r = rolledPnl(fills, PV, false);
    expect(r.peakContracts).toBe(9);
  });

  it('the naive single-book replay invents a large phantom profit (the bug being fixed)', () => {
    const phantom = naiveNet(fills);
    const correct = rolledPnl(fills, PV, false).net;
    expect(phantom).toBeGreaterThan(correct + 50_000);   // ~106k phantom vs ~2.6k real
  });
});

describe('rolledPnl — clean roll (each contract round-trips to flat): no under/over-count', () => {
  const fills: RawFill[] = [
    { symbol: 'RIM6', time: 100, side: 'sell', qty: 9, price: 110910 },  // open short RIM6
    { symbol: 'RIM6', time: 150, side: 'buy', qty: 9, price: 110700 },   // close RIM6 (+210 pts)
    { symbol: 'RIU6', time: 200, side: 'sell', qty: 9, price: 95200 },   // open short RIU6
    { symbol: 'RIU6', time: 250, side: 'buy', qty: 9, price: 95000 },    // close RIU6 (+200 pts)
  ];

  it('agrees three ways: aggregate == Σ independent per-contract == Σ breakdown', () => {
    const r = rolledPnl(fills, PV, false);
    const m2 = handContractNet(fills.filter(f => f.symbol === 'RIM6'))
             + handContractNet(fills.filter(f => f.symbol === 'RIU6'));
    const m3 = r.byContract.reduce((a, c) => a + c.net, 0);
    expect(r.net).toBeCloseTo(m2, 6);            // (1) == (2)
    expect(r.net).toBeCloseTo(m3, 6);            // (1) == (3)
    expect(r.closes).toBe(2);
    expect(r.peakContracts).toBe(9);
    expect(r.position).toBe(0);                  // flat on the current contract
  });

  it('here naive == rolled (no carry across the roll ⇒ no phantom)', () => {
    expect(naiveNet(fills)).toBeCloseTo(rolledPnl(fills, PV, false).net, 6);
  });

  it('current contract is the latest traded (RIU6)', () => {
    expect(rolledPnl(fills, PV, false).currentSymbol).toBe('RIU6');
  });
});
