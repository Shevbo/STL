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

// A trade enriched with its position-lifecycle role, for markers + hover tooltip.
export interface TradeEvent {
  time: number;           // bucketed (chart) time
  rawTime: number;        // original fill time
  side: 'buy' | 'sell';
  qty: number;            // contracts this fill moved
  price: number;
  kind: 'open' | 'average' | 'partial' | 'full' | 'reverse';
  posAfter: number;       // signed total position after this fill
  label: string;          // RU label e.g. "Открытие позиции 1 (всего в поз. 1)"
  close?: {               // present on partial/full/reverse (a closing fill)
    holdSecs: number;     // time in position from episode open to this close
    maxContracts: number; // max abs position reached during the episode
    pnl: number;          // realized result of the closed portion (rubles)
  };
}

const KIND_LABEL: Record<TradeEvent['kind'], string> = {
  open: 'Открытие позиции',
  average: 'Усреднение',
  partial: 'Част. закрытие',
  full: 'Полн. закрытие',
  reverse: 'Реверс',
};

// Walk fills, classify each by what it does to the running position, bucket time
// to candle time, and on closing fills compute hold time, max contracts in the
// episode, and realized PnL (rubles, via pointValue).
export function tradeEvents(trades: Fill[], bucketSecs: number, pointValue = 1): TradeEvent[] {
  let pos = 0, avg = 0, epStart = 0, epMaxAbs = 0;
  const out: TradeEvent[] = [];
  const sorted = [...trades].sort((a, b) => a.time - b.time);
  for (const t of sorted) {
    const q = Number(t.qty) || 1;
    const signed = t.side === 'buy' ? q : -q;
    let kind: TradeEvent['kind'];
    let close: TradeEvent['close'];

    if (pos === 0) {
      kind = 'open'; avg = t.price; epStart = t.time; epMaxAbs = q; pos = signed;
    } else if (Math.sign(pos) === Math.sign(signed)) {
      kind = 'average';
      avg = (avg * Math.abs(pos) + t.price * q) / (Math.abs(pos) + q);
      pos += signed; epMaxAbs = Math.max(epMaxAbs, Math.abs(pos));
    } else {
      const dir = Math.sign(pos);
      const closeQty = Math.min(Math.abs(pos), q);
      const pnlPts = dir > 0 ? (t.price - avg) * closeQty : (avg - t.price) * closeQty;
      close = { holdSecs: t.time - epStart, maxContracts: epMaxAbs, pnl: pnlPts * pointValue };
      if (q < Math.abs(pos)) { kind = 'partial'; pos += signed; }
      else if (q === Math.abs(pos)) { kind = 'full'; pos = 0; avg = 0; }
      else {
        kind = 'reverse'; pos += signed;        // flips through zero
        avg = t.price; epStart = t.time; epMaxAbs = Math.abs(pos);
      }
    }

    const totalInPos = Math.abs(pos);
    out.push({
      time: Math.floor(t.time / bucketSecs) * bucketSecs,
      rawTime: t.time, side: t.side, qty: q, price: t.price,
      kind, posAfter: pos, close,
      label: `${KIND_LABEL[kind]} ${q} (всего в поз. ${totalInPos})`,
    });
  }
  return out;
}

// Trade triangles placed STRICTLY at the trade price via an invisible anchor line
// series + `inBar` markers (lightweight-charts markers can't take a free price).
// Buy = up-triangle, sell = down-triangle. Colors passed in. Returns per-side
// points/markers plus a flat `index` (bucketed time + price + label) for hover.
// Times nudged +1s on collision so each fill keeps a unique point.
export function priceMarkers(
  events: TradeEvent[],
  colors: { buy: string; sell: string },
): {
  buy: { points: any[]; markers: any[] };
  sell: { points: any[]; markers: any[] };
  index: Array<{ time: number; price: number; side: 'buy' | 'sell'; label: string; rawTime: number; close?: TradeEvent['close'] }>;
} {
  const buy = { points: [] as any[], markers: [] as any[] };
  const sell = { points: [] as any[], markers: [] as any[] };
  const index: any[] = [];
  let lastBuyT = -Infinity, lastSellT = -Infinity;
  for (const e of events) {
    if (e.side === 'buy') {
      let tt = e.time; if (tt <= lastBuyT) tt = lastBuyT + 1; lastBuyT = tt;
      buy.points.push({ time: tt, value: e.price });
      buy.markers.push({ time: tt, position: 'inBar', color: colors.buy, shape: 'arrowUp', size: 1 });
      index.push({ time: tt, price: e.price, side: 'buy', label: e.label, rawTime: e.rawTime, close: e.close });
    } else {
      let tt = e.time; if (tt <= lastSellT) tt = lastSellT + 1; lastSellT = tt;
      sell.points.push({ time: tt, value: e.price });
      sell.markers.push({ time: tt, position: 'inBar', color: colors.sell, shape: 'arrowDown', size: 1 });
      index.push({ time: tt, price: e.price, side: 'sell', label: e.label, rawTime: e.rawTime, close: e.close });
    }
  }
  return { buy, sell, index };
}

// A position EPISODE: from first entry (pos 0 → nonzero) to FULL close (back to 0).
// Spans averages and partial closes. On a reverse the episode closes at that fill
// and a new one opens. dir = side of the position held.
export interface Episode {
  dir: 'long' | 'short';
  tIn: number; pIn: number;   // open time/price
  tOut: number; pOut: number; // full-close time/price (or last point if still open)
  open?: boolean;             // true = position still open, tOut/pOut is the latest fill
}

// `lastTime`/`lastPrice` extend a still-open episode to the right edge so its
// dashed connector is visible (e.g. a freshly opened position not yet closed).
export function positionEpisodes(trades: Fill[], lastTime?: number, lastPrice?: number): Episode[] {
  const eps: Episode[] = [];
  let pos = 0, tIn = 0, pIn = 0, lastT = 0, lastP = 0;
  const sorted = [...trades].sort((a, b) => a.time - b.time);
  for (const t of sorted) {
    const q = Number(t.qty) || 1;
    const signed = t.side === 'buy' ? q : -q;
    lastT = t.time; lastP = t.price;
    if (pos === 0) { pos = signed; tIn = t.time; pIn = t.price; continue; }
    if (Math.sign(pos) === Math.sign(signed)) { pos += signed; continue; } // average
    const dir = pos > 0 ? 'long' : 'short';
    const newPos = pos + signed;
    if (newPos === 0) {                       // full close
      eps.push({ dir, tIn, pIn, tOut: t.time, pOut: t.price });
      pos = 0;
    } else if (Math.sign(newPos) !== Math.sign(pos)) {  // reverse through zero
      eps.push({ dir, tIn, pIn, tOut: t.time, pOut: t.price });
      pos = newPos; tIn = t.time; pIn = t.price;
    } else {                                   // partial close, position remains
      pos = newPos;
    }
  }
  if (pos !== 0) {  // still-open episode → draw to the latest known point
    const dir = pos > 0 ? 'long' : 'short';
    eps.push({
      dir, tIn, pIn,
      tOut: lastTime ?? lastT, pOut: lastPrice ?? lastP, open: true,
    });
  }
  return eps;
}

// Dashed connector points for one LineSeries per direction, from episode open to
// full close. Times forced strictly ascending; whitespace gap between segments.
export function buildConnectors(episodes: Episode[], dir: 'long' | 'short'): any[] {
  const pts: any[] = [];
  let lastT = -Infinity;
  const push = (time: number, value?: number) => {
    let tt = time;
    if (tt <= lastT) tt = lastT + 1;
    lastT = tt;
    pts.push(value === undefined ? { time: tt } : { time: tt, value });
  };
  for (const e of episodes) {
    if (e.dir !== dir) continue;
    push(e.tIn, e.pIn);
    push(e.tOut, e.pOut);
    push(e.tOut + 1);   // whitespace → break before next segment
  }
  return pts;
}
