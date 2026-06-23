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
    exit: 'TP' | 'SL';    // profit → take-profit, loss → stop-loss
    partial: boolean;     // true = part of the position closed, rest still open
    exitLabel: string;    // RU: "TP (частичный)" / "SL (полный)" etc.
  };
}

// Outcome label for a closing fill: TP = closed in profit, SL = closed in loss.
// exitLabel is the human headline, e.g. "Частичный TP" / "Полный SL".
function exitLabel(pnl: number, partial: boolean): { exit: 'TP' | 'SL'; partial: boolean; exitLabel: string } {
  const exit: 'TP' | 'SL' = pnl >= 0 ? 'TP' : 'SL';
  const kind = partial ? 'Частичный' : 'Полный';
  const full = exit === 'TP' ? 'Take-Profit' : 'Stop-Loss';
  return { exit, partial, exitLabel: `${kind} ${exit} · ${partial ? 'част. ' : ''}${full}` };
}

const KIND_LABEL: Record<TradeEvent['kind'], string> = {
  open: 'Открытие позиции',
  average: 'Усреднение',
  partial: 'Част. закрытие',
  full: 'Полн. закрытие',
  reverse: 'Реверс',
};

// FORTS commission model. The backend (trader/lab/commission.py) is the single
// source of truth: applyFeeConfig() overrides these at runtime from
// GET /api/v1/lab/fee-config (call loadFeeConfig() once at app startup). The
// literals below are only a fallback if that fetch has not run / failed, so the
// chart never disagrees silently with the leaderboard after a tariff change.
export interface FeeConfig {
  brokerFeePerContract: number;
  moexTakerRate: Record<string, number>;
  tickerGroup: Record<string, string>;
  defaultGroup: string;
}

let FEE: FeeConfig = {
  brokerFeePerContract: 0.45,
  moexTakerRate: {
    fx: 0.0000462, index: 0.0000660, stock: 0.0001980,
    commodity: 0.0001320, rate: 0.0001650,
  },
  tickerGroup: {
    RI: 'index', MX: 'index', MM: 'index', RV: 'index',
    SI: 'fx', EU: 'fx', CR: 'fx', CN: 'fx', ED: 'fx', UC: 'fx',
    AE: 'fx', GB: 'fx', JP: 'fx', TR: 'fx',
    GZ: 'stock', SR: 'stock', VB: 'stock', LK: 'stock', GM: 'stock',
    RN: 'stock', MN: 'stock', NK: 'stock', TT: 'stock', AF: 'stock',
    FE: 'stock', CH: 'stock', PL: 'stock', TN: 'stock', MG: 'stock',
    SG: 'stock', BS: 'stock', YN: 'stock', PO: 'stock', HY: 'stock',
    BR: 'commodity', GD: 'commodity', SV: 'commodity', PD: 'commodity',
    PT: 'commodity', NG: 'commodity', CU: 'commodity', AL: 'commodity',
    GL: 'commodity', SA: 'commodity', SL: 'commodity',
  },
  defaultGroup: 'index',
};

/** Override the fee model with the backend's authoritative config. */
export function applyFeeConfig(cfg: Partial<FeeConfig> | null | undefined): void {
  if (!cfg) return;
  FEE = {
    brokerFeePerContract: cfg.brokerFeePerContract ?? FEE.brokerFeePerContract,
    moexTakerRate: cfg.moexTakerRate ?? FEE.moexTakerRate,
    tickerGroup: cfg.tickerGroup ?? FEE.tickerGroup,
    defaultGroup: cfg.defaultGroup ?? FEE.defaultGroup,
  };
}

function feeGroup(symbol: string): string {
  const base = (symbol || '').split('@')[0].split('-')[0].trim().toUpperCase().slice(0, 2);
  return FEE.tickerGroup[base] ?? FEE.defaultGroup;
}
// Commission (rubles) for ONE fill of qty contracts of symbol.
export function commissionFor(symbol: string, price: number, qty: number, pointValue = 1, taker = true): number {
  const q = Math.abs(qty) || 1;
  const broker = FEE.brokerFeePerContract * q;
  if (!taker) return broker;
  const notional = Math.abs(price) * (pointValue || 1);
  const rate = FEE.moexTakerRate[feeGroup(symbol)] ?? FEE.moexTakerRate[FEE.defaultGroup];
  return broker + rate * notional * q;
}

export interface CommissionBreakdown {
  broker: number;     // Σ broker fee (Finam 0.45 ₽/contract)
  exchange: number;   // Σ MOEX exchange fee (taker only; 0 for maker)
  total: number;
  fills: number;      // number of order fills charged
  contracts: number;  // Σ contracts across fills
  rate: number;       // exchange rate applied (fraction of notional), for transparency
  group: string;      // MOEX fee group of the instrument
}

// Total broker vs exchange commission across all fills — so the UI can SHOW the split
// and the user can verify it's the real broker/exchange tariff, not a guess.
export function commissionBreakdown(
  trades: Fill[], pointValue = 1, symbol = '', taker = true,
): CommissionBreakdown {
  let broker = 0, exchange = 0, fills = 0, contracts = 0;
  for (const t of trades) {
    const q = Math.abs(Number(t.qty) || 1);
    const full = commissionFor(symbol, t.price, q, pointValue, taker);
    const brk = FEE.brokerFeePerContract * q;
    broker += brk; exchange += full - brk;
    fills++; contracts += q;
  }
  return {
    broker, exchange, total: broker + exchange, fills, contracts,
    rate: taker ? (FEE.moexTakerRate[feeGroup(symbol)] ?? FEE.moexTakerRate[FEE.defaultGroup]) : 0,
    group: feeGroup(symbol),
  };
}

// Walk fills, classify each by what it does to the running position, bucket time
// to candle time, and on closing fills compute hold time, max contracts in the
// episode, and realized PnL (rubles, via pointValue) NET of commission. Each fill's
// commission uses the taker/maker model above (backtest=taker, live=maker); the
// entry/averaging fees are carried and charged to the round-trip on its close
// (same accounting as backend compute_metrics).
export function tradeEvents(trades: Fill[], bucketSecs: number, pointValue = 1, symbol = '', taker = true): TradeEvent[] {
  let pos = 0, avg = 0, epStart = 0, epMaxAbs = 0, carriedFee = 0;
  const out: TradeEvent[] = [];
  const sorted = [...trades].sort((a, b) => a.time - b.time);
  for (const t of sorted) {
    const q = Number(t.qty) || 1;
    const signed = t.side === 'buy' ? q : -q;
    const c = commissionFor(symbol, t.price, q, pointValue, taker);
    let kind: TradeEvent['kind'];
    let close: TradeEvent['close'];

    if (pos === 0) {
      kind = 'open'; avg = t.price; epStart = t.time; epMaxAbs = q; pos = signed;
      carriedFee = c;                           // opening fill's fee
    } else if (Math.sign(pos) === Math.sign(signed)) {
      kind = 'average';
      avg = (avg * Math.abs(pos) + t.price * q) / (Math.abs(pos) + q);
      pos += signed; epMaxAbs = Math.max(epMaxAbs, Math.abs(pos));
      carriedFee += c;                          // averaging fill's fee
    } else {
      const dir = Math.sign(pos);
      const closeQty = Math.min(Math.abs(pos), q);
      const pnlPts = dir > 0 ? (t.price - avg) * closeQty : (avg - t.price) * closeQty;
      // Net of: this closing fill's fee + carried entry/averaging fees.
      const pnl = pnlPts * pointValue - c - carriedFee;
      const isPartial = q < Math.abs(pos);
      close = { holdSecs: t.time - epStart, maxContracts: epMaxAbs, pnl, ...exitLabel(pnl, isPartial) };
      if (isPartial) { kind = 'partial'; pos += signed; }
      else if (q === Math.abs(pos)) { kind = 'full'; pos = 0; avg = 0; carriedFee = 0; }
      else {
        kind = 'reverse'; pos += signed;        // flips through zero (full close + new open)
        avg = t.price; epStart = t.time; epMaxAbs = Math.abs(pos); carriedFee = c;
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

// Aggregate exit analytics over all closing fills: counts + rubles for TP/SL,
// split into full and partial exits. Used by the stats overlay.
export interface ExitStats {
  tp: number; sl: number;            // count of profitable / losing closes
  tpPartial: number; slPartial: number;
  tpFull: number; slFull: number;
  tpPnl: number; slPnl: number;      // summed rubles
  winRateByExit: number;             // tp / (tp+sl), 0..1
}

export function exitStats(events: TradeEvent[]): ExitStats {
  const s: ExitStats = { tp: 0, sl: 0, tpPartial: 0, slPartial: 0, tpFull: 0, slFull: 0, tpPnl: 0, slPnl: 0, winRateByExit: 0 };
  for (const e of events) {
    if (!e.close) continue;
    if (e.close.exit === 'TP') {
      s.tp++; s.tpPnl += e.close.pnl; e.close.partial ? s.tpPartial++ : s.tpFull++;
    } else {
      s.sl++; s.slPnl += e.close.pnl; e.close.partial ? s.slPartial++ : s.slFull++;
    }
  }
  const tot = s.tp + s.sl;
  s.winRateByExit = tot ? s.tp / tot : 0;
  return s;
}

// Trade triangles placed STRICTLY at the trade price via an invisible anchor line
// series + `inBar` markers (lightweight-charts markers can't take a free price).
// Buy = up-triangle, sell = down-triangle. Colors passed in. Returns per-side
// points/markers plus a flat `index` (bucketed time + price + label) for hover.
// Times nudged +1s on collision so each fill keeps a unique point.
// colors.buy/sell tint entries+averages; a CLOSING fill is tinted by outcome
// (colors.tp green / colors.sl red) so take-profit vs stop-loss exits stand out.
export function priceMarkers(
  events: TradeEvent[],
  colors: { buy: string; sell: string; tp: string; sl: string },
): {
  buy: { points: any[]; markers: any[] };
  sell: { points: any[]; markers: any[] };
  index: Array<{ time: number; price: number; side: 'buy' | 'sell'; label: string; rawTime: number; close?: TradeEvent['close'] }>;
} {
  const buy = { points: [] as any[], markers: [] as any[] };
  const sell = { points: [] as any[], markers: [] as any[] };
  const index: any[] = [];
  let lastBuyT = -Infinity, lastSellT = -Infinity;
  // Dim an entry color for AVERAGING fills (faded vs a bright fresh entry). Hex → +alpha.
  const dim = (c: string) => (c.length === 7 ? c + '70' : c);
  for (const e of events) {
    // Closing fills are tinted by outcome (TP green / SL red). Entries are bright;
    // averaging fills are dim+smaller so a fresh entry visually dominates the adds.
    const base = e.side === 'buy' ? colors.buy : colors.sell;
    const isAvg = e.kind === 'average';
    const color = e.close ? (e.close.exit === 'TP' ? colors.tp : colors.sl) : (isAvg ? dim(base) : base);
    const size = e.close ? 1 : (isAvg ? 1 : 2);   // bright entry larger, AVG smaller
    if (e.side === 'buy') {
      let tt = e.time; if (tt <= lastBuyT) tt = lastBuyT + 1; lastBuyT = tt;
      buy.points.push({ time: tt, value: e.price });
      buy.markers.push({ time: tt, position: 'inBar', color, shape: 'arrowUp', size });
      index.push({ time: tt, price: e.price, side: 'buy', label: e.label, rawTime: e.rawTime, close: e.close });
    } else {
      let tt = e.time; if (tt <= lastSellT) tt = lastSellT + 1; lastSellT = tt;
      sell.points.push({ time: tt, value: e.price });
      sell.markers.push({ time: tt, position: 'inBar', color, shape: 'arrowDown', size });
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

// ─── Per-contract (roll-aware) P&L ──────────────────────────────────────────
// A robot that rolled (RIM6 → RIU6 → …) traded DISTINCT instruments at very
// different price levels. Realized P&L MUST be computed per contract and summed —
// pairing a fill on one contract against a fill on another invents phantom profit
// (a RIM6 sell @110 910 "closed" by a RIU6 buy @94 780 → fake +16 000/contract).
// At expiry the old contract is force-closed and a same-size position opens on the
// next one, so each contract's book is self-contained: replay it alone, sum across.
export type RawFill = Fill & { symbol?: string };

export interface ContractPnl {
  symbol: string;
  net: number;            // Σ realized close pnl (₽, net of commission) on this contract
  closes: number;         // round trips closed on this contract
  peakContracts: number;  // max abs contracts held on this contract (margin at risk)
  position: number;       // signed position still open on this contract at the end
  events: TradeEvent[];
}

export interface RolledPnl {
  net: number;            // Σ realized pnl over all contracts (₽, net of commission)
  closes: number;         // total round trips
  peakContracts: number;  // max contracts held on any single contract (never summed across)
  position: number;       // open position on the CURRENT (latest-traded) contract
  currentSymbol: string;
  byContract: ContractPnl[];
  events: TradeEvent[];   // every fill's event, contract-correct lifecycle, time-ordered
}

// Group fills by contract symbol, replay each group independently, sum the realized
// P&L. `taker` selects the fee model (live = maker/false, backtest = taker/true).
//   pointValue: a number (one ₽/point for all) OR a {symbol: pv} map. A rolled robot's
//     contracts have slightly different point values, so the map is molecule-exact.
//   opts.settleCarried (default true): a position left open on an EXPIRED (non-current)
//     contract is force-closed at that contract's last traded price — the exchange cash-
//     settles at expiry, and the robot reopens the same size on the next contract. The
//     CURRENT contract's open position is kept unrealized (it is the live position).
export function rolledPnl(
  fills: RawFill[],
  pointValue: number | Record<string, number> = 1,
  taker = false,
  opts: { settleCarried?: boolean; bucketSecs?: number } = {},
): RolledPnl {
  const { settleCarried = true, bucketSecs = 60 } = opts;
  const pvFor = (s: string): number =>
    typeof pointValue === 'number' ? pointValue : (pointValue[s] ?? pointValue[''] ?? 1);

  const groups = new Map<string, RawFill[]>();
  const order: string[] = [];                 // contracts in first-seen (chronological) order
  const sorted = [...fills].sort((a, b) => a.time - b.time);
  for (const f of sorted) {
    const s = f.symbol || '';
    if (!groups.has(s)) { groups.set(s, []); order.push(s); }
    groups.get(s)!.push(f);
  }
  const currentSymbol = order.length ? order[order.length - 1] : '';

  const byContract: ContractPnl[] = [];
  let net = 0, closes = 0, peak = 0;
  const allEvents: TradeEvent[] = [];
  for (const s of order) {
    const gf = groups.get(s)!;
    let pos = 0, cPeak = 0;
    for (const f of gf) {
      pos += f.side === 'buy' ? (Number(f.qty) || 1) : -(Number(f.qty) || 1);
      cPeak = Math.max(cPeak, Math.abs(pos));
    }
    // Force-close a carried position on an expired contract at its last traded price.
    let seq = gf;
    if (settleCarried && s !== currentSymbol && pos !== 0) {
      const last = gf[gf.length - 1];
      seq = [...gf, { symbol: s, time: last.time + 1, price: last.price,
                      qty: Math.abs(pos), side: pos > 0 ? 'sell' : 'buy' }];
    }
    const evs = tradeEvents(seq, bucketSecs, pvFor(s), s, taker);
    let cNet = 0, cCloses = 0;
    for (const e of evs) if (e.close) { cNet += e.close.pnl; cCloses++; }
    const endPos = (settleCarried && s !== currentSymbol) ? 0 : pos;
    byContract.push({ symbol: s, net: cNet, closes: cCloses, peakContracts: cPeak, position: endPos, events: evs });
    net += cNet; closes += cCloses; peak = Math.max(peak, cPeak);
    allEvents.push(...evs);
  }
  allEvents.sort((a, b) => a.rawTime - b.rawTime);
  const cur = byContract.find(c => c.symbol === currentSymbol);
  return { net, closes, peakContracts: peak, position: cur?.position ?? 0, currentSymbol, byContract, events: allEvents };
}

// One RECTANGLE per position episode (open → full close), for the chart overlay:
// x spans hold time (open→close), y spans the entry level (avg cost) → exit level.
// Long = green, short = red. Built from roll-aware events, so episodes never span the
// roll (a contract's synthetic settle closes its book). A still-open final episode
// extends to lastTime/lastPrice (the live position).
export interface PositionRect {
  dir: 'long' | 'short';
  tIn: number; pIn: number;     // open time / volume-weighted entry (avg cost)
  tOut: number; pOut: number;   // close time / exit price (last fill if still open)
  open?: boolean;
}

export function positionRects(events: TradeEvent[], lastTime?: number, lastPrice?: number): PositionRect[] {
  const rects: PositionRect[] = [];
  let pos = 0, avg = 0, tIn = 0, dir: 'long' | 'short' = 'long';
  for (const e of events) {
    const q = Number(e.qty) || 1;
    const signed = e.side === 'buy' ? q : -q;
    if (e.kind === 'open') { pos = signed; avg = e.price; tIn = e.time; dir = signed > 0 ? 'long' : 'short'; continue; }
    if (e.kind === 'average') { avg = (avg * Math.abs(pos) + e.price * q) / (Math.abs(pos) + q); pos += signed; continue; }
    if (e.kind === 'partial') { pos += signed; continue; }   // rect stays open until the full close
    // full close or reverse: close the current episode's rectangle
    rects.push({ dir, tIn, pIn: avg, tOut: e.time, pOut: e.price });
    if (e.kind === 'reverse') { pos += signed; avg = e.price; tIn = e.time; dir = pos > 0 ? 'long' : 'short'; }
    else { pos = 0; avg = 0; }
  }
  if (pos !== 0) rects.push({ dir, tIn, pIn: avg, tOut: lastTime ?? tIn, pOut: lastPrice ?? avg, open: true });
  return rects;
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
