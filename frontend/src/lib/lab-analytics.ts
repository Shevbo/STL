// lab-analytics.ts
// Shared backtest trade analytics: ledger (per-fill enriched rows),
// aggregate stats, per-candle marker collapsing, and open→close connectors.

export interface Fill {
  time: number;     // unix seconds
  side: 'buy' | 'sell';
  qty: number;
  price: number;
}

export interface LedgerRow {
  time: number;
  side: 'buy' | 'sell';
  qty: number;
  price: number;
  type: 'open' | 'average' | 'close' | 'reverse';
  pnl: number | null;          // realized PnL (points) on close, else null
}

export interface RoundTrip {
  dir: 'long' | 'short';
  tIn: number;  pIn: number;
  tOut: number; pOut: number;
  pnl: number;
}

export interface Stats {
  fills: number;
  roundTrips: number;
  longRT: number;
  shortRT: number;
  maxAbsPos: number;
  avgPerTrade: number;
  maxProfit: number;
  maxLoss: number;
  netProfit: number;
  maxDDmoney: number;
  recovery: number | null;
}

export function toFills(raw: any): Fill[] {
  const arr = Array.isArray(raw) ? raw : (typeof raw === 'string' ? JSON.parse(raw) : []);
  return arr.filter((t: any) => t && t.time != null);
}

// Replay fills → ledger rows + round trips (position lifecycle).
export function replay(trades: Fill[]): { ledger: LedgerRow[]; roundTrips: RoundTrip[] } {
  let pos = 0, avg = 0, posOpenTime = 0;
  const ledger: LedgerRow[] = [];
  const roundTrips: RoundTrip[] = [];

  for (const t of trades) {
    const q = Number(t.qty) || 1;
    const signed = t.side === 'buy' ? q : -q;
    let type: LedgerRow['type'] = 'open';
    let pnl: number | null = null;

    if (pos === 0) {
      type = 'open'; avg = t.price; pos = signed; posOpenTime = t.time;
    } else if (Math.sign(pos) === Math.sign(signed)) {
      type = 'average';
      const c = avg * Math.abs(pos) + t.price * q;
      pos += signed; avg = c / Math.abs(pos);
    } else {
      const dir = Math.sign(pos);                 // +1 long, -1 short
      const closeQty = Math.min(Math.abs(pos), q);
      pnl = dir > 0 ? (t.price - avg) * closeQty : (avg - t.price) * closeQty;
      roundTrips.push({
        dir: dir > 0 ? 'long' : 'short',
        tIn: posOpenTime, pIn: avg,
        tOut: t.time, pOut: t.price,
        pnl,
      });
      const leftover = q - closeQty;
      if (leftover > 0) {
        type = 'reverse'; pos = -dir * leftover; avg = t.price; posOpenTime = t.time;
      } else {
        type = 'close'; pos += signed; if (pos === 0) avg = 0;
      }
    }
    ledger.push({ time: t.time, side: t.side, qty: q, price: t.price, type, pnl });
  }
  return { ledger, roundTrips };
}

export function computeStats(trades: Fill[], roundTrips: RoundTrip[], equity: any[]): Stats {
  let maxAbsPos = 0, pos = 0;
  for (const t of trades) {
    pos += t.side === 'buy' ? (t.qty || 1) : -(t.qty || 1);
    maxAbsPos = Math.max(maxAbsPos, Math.abs(pos));
  }
  const pnls = roundTrips.map(r => r.pnl);
  const rt = pnls.length;
  const sum = pnls.reduce((a, b) => a + b, 0);

  let netProfit = 0, maxDDmoney = 0;
  if (equity.length) {
    netProfit = equity[equity.length - 1].equity - equity[0].equity;
    let peak = -Infinity;
    for (const p of equity) {
      if (p.equity > peak) peak = p.equity;
      const dd = peak - p.equity;
      if (dd > maxDDmoney) maxDDmoney = dd;
    }
  }
  return {
    fills: trades.length,
    roundTrips: rt,
    longRT: roundTrips.filter(r => r.dir === 'long').length,
    shortRT: roundTrips.filter(r => r.dir === 'short').length,
    maxAbsPos,
    avgPerTrade: rt ? sum / rt : 0,
    maxProfit: rt ? Math.max(...pnls) : 0,
    maxLoss: rt ? Math.min(...pnls) : 0,
    netProfit,
    maxDDmoney,
    recovery: maxDDmoney > 0 ? netProfit / maxDDmoney : null,
  };
}

// One marker per fill, QUIK-style: green up-triangle for a buy (below the bar),
// red down-triangle for a sell (above the bar). No aggregation, no count badges,
// no price text. Markers must be bucketed to the chart's candle time so each one
// sits ON its candle; lightweight-charts stacks multiple markers on the same bar.
export function perFillMarkers(trades: Fill[], bucketSecs: number): any[] {
  const markers = trades.map(t => ({
    time: Math.floor(t.time / bucketSecs) * bucketSecs,
    position: t.side === 'buy' ? 'belowBar' : 'aboveBar',
    color: t.side === 'buy' ? '#26a65b' : '#f44336',
    shape: t.side === 'buy' ? 'arrowUp' : 'arrowDown',
    size: 2,
  }));
  // lightweight-charts requires markers sorted by time (stable per same time).
  markers.sort((a, b) => a.time - b.time);
  return markers;
}

// Build dashed open→close connector points for one LineSeries per direction.
// Each round trip = [entry, exit, whitespace-gap]; times forced strictly ascending.
export function buildConnectors(roundTrips: RoundTrip[], dir: 'long' | 'short'): any[] {
  const pts: any[] = [];
  let lastT = -Infinity;
  const push = (time: number, value?: number) => {
    let tt = time;
    if (tt <= lastT) tt = lastT + 1;
    lastT = tt;
    pts.push(value === undefined ? { time: tt } : { time: tt, value });
  };
  for (const r of roundTrips) {
    if (r.dir !== dir) continue;
    push(r.tIn, r.pIn);
    push(r.tOut, r.pOut);
    push(r.tOut + 1);   // whitespace → break before next segment
  }
  return pts;
}
