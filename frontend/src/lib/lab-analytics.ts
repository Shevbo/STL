// lab-analytics.ts
// Shared backtest trade analytics: per-fill trade events, commission model,
// price markers, position rectangles, and roll-aware P&L.

export interface Fill {
  time: number;     // unix seconds
  side: 'buy' | 'sell';
  qty: number;
  price: number;
}

export function toFills(raw: any): Fill[] {
  const arr = Array.isArray(raw) ? raw : (typeof raw === 'string' ? JSON.parse(raw) : []);
  return arr.filter((t: any) => t && t.time != null);
}

// A trade enriched with its position-lifecycle role, for markers + hover tooltip.
export interface TradeEvent {
  time: number;           // bucketed (chart) time
  rawTime: number;        // original fill time
  id?: string;            // source fill's order_id (unique) — for unambiguous row mapping
  synthetic?: boolean;    // fabricated roll force-close (no real fill / no table row) — no marker
  side: 'buy' | 'sell';
  qty: number;            // contracts this fill moved
  price: number;
  // average = add at a price BETTER than avg entry (price moved AGAINST → улучшаем среднюю);
  // enforce = add at a price WORSE than avg entry (price moved in our favour → усиление).
  kind: 'open' | 'average' | 'enforce' | 'partial' | 'full' | 'reverse';
  posAfter: number;       // signed total position after this fill
  avgAfter: number;       // avg entry of the position after this fill (0 when flat)
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
  enforce: 'Усиление',
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
      // Adding to the position. AVG (усреднение) = price moved AGAINST us, the add is at a
      // BETTER price than the avg entry (improves the average). ENF (усиление) = price moved
      // in our FAVOUR, the add is at a WORSE price than the avg (scaling into a winner).
      const inFavour = pos > 0 ? t.price > avg : t.price < avg;
      kind = inFavour ? 'enforce' : 'average';
      avg = (avg * Math.abs(pos) + t.price * q) / (Math.abs(pos) + q);
      pos += signed; epMaxAbs = Math.max(epMaxAbs, Math.abs(pos));
      carriedFee += c;                          // add fill's fee
    } else {
      const dir = Math.sign(pos);
      const closeQty = Math.min(Math.abs(pos), q);
      const pnlPts = dir > 0 ? (t.price - avg) * closeQty : (avg - t.price) * closeQty;
      // Net of: this closing fill's fee + the entry fees OF THE CONTRACTS BEING
      // CLOSED. Раньше здесь списывался весь carriedFee целиком и не уменьшался,
      // поэтому каждый следующий частичный выход платил комиссию входа ЗАНОВО за
      // всю позицию. Стратегия, которая усредняется до 18 контрактов и выходит
      // кусками, получала минус на ровном месте: у живого MACD·RIU6 155 частичных
      // выходов превращали +129 тыс ₽ в −103 тыс ₽ и рисовали стену липовых SL.
      const isPartial = q < Math.abs(pos);
      const feeShare = isPartial ? carriedFee * (closeQty / Math.abs(pos)) : carriedFee;
      const pnl = pnlPts * pointValue - c - feeShare;
      close = { holdSecs: t.time - epStart, maxContracts: epMaxAbs, pnl, ...exitLabel(pnl, isPartial) };
      if (isPartial) { kind = 'partial'; carriedFee -= feeShare; pos += signed; }
      else if (q === Math.abs(pos)) { kind = 'full'; pos = 0; avg = 0; carriedFee = 0; }
      else {
        kind = 'reverse'; pos += signed;        // flips through zero (full close + new open)
        avg = t.price; epStart = t.time; epMaxAbs = Math.abs(pos); carriedFee = c;
      }
    }

    const totalInPos = Math.abs(pos);
    out.push({
      time: Math.floor(t.time / bucketSecs) * bucketSecs,
      rawTime: t.time, id: (t as any).order_id ?? (t as any).id,
      synthetic: (t as any).synthetic || undefined,
      side: t.side, qty: q, price: t.price,
      kind, posAfter: pos, avgAfter: avg, close,
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

// ── TP/SL distribution by averaging depth ────────────────────────────────────
// For an averaging strategy, group every closing episode by the PEAK contracts it
// reached (close.maxContracts = 1..avg_max) and split TP (profit close) vs SL (loss
// close), summing ₽ (net of commission). Answers "how deep is it safe to average" —
// but COUNT ratio alone misleads (there is no stop-loss in make_on_bar, so deep
// levels almost always grind to TP while the rare deep SL is huge), so P&L per level
// is the honest signal — always read tpPnl/slPnl alongside the tp/sl counts.
export interface LevelStat {
  level: number;                    // peak contracts held in the episode
  tp: number; sl: number;           // count of profit / loss closes at this peak
  tpPnl: number; slPnl: number;     // Σ ₽ (net of commission)
}

export function tpSlByLevel(events: TradeEvent[]): LevelStat[] {
  const m = new Map<number, LevelStat>();
  for (const e of events) {
    if (!e.close) continue;
    const level = Math.max(1, Math.round(e.close.maxContracts || 1));
    const s = m.get(level) ?? { level, tp: 0, sl: 0, tpPnl: 0, slPnl: 0 };
    if (e.close.exit === 'TP') { s.tp++; s.tpPnl += e.close.pnl; }
    else { s.sl++; s.slPnl += e.close.pnl; }
    m.set(level, s);
  }
  return [...m.values()].sort((a, b) => a.level - b.level);
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
  index: Array<{ time: number; price: number; side: 'buy' | 'sell'; label: string; rawTime: number; id?: string; close?: TradeEvent['close'] }>;
} {
  const buy = { points: [] as any[], markers: [] as any[] };
  const sell = { points: [] as any[], markers: [] as any[] };
  const index: any[] = [];
  // Tint an AVERAGING fill vs a bright fresh entry. Was '70' (44% alpha) — too faint;
  // 'b0' (69%) is ~50% more visible by COLOUR while still reading as "not fresh".
  const dim = (c: string) => (c.length === 7 ? c + 'b0' : c);
  for (const e of events) {
    // Synthetic roll force-close fills have no real trade / no table row — no arrow.
    if (e.synthetic) continue;
    // Closing fills are tinted by outcome (TP green / SL red). Fresh entry = bright+large.
    // AVG (add against us) = dim+small. ENF (add into a winner) = full colour but small.
    const base = e.side === 'buy' ? colors.buy : colors.sell;
    const isAdd = e.kind === 'average' || e.kind === 'enforce';
    const color = e.close ? (e.close.exit === 'TP' ? colors.tp : colors.sl)
      : (e.kind === 'average' ? dim(base) : base);
    const size = e.close ? 1 : (isAdd ? 1 : 2);   // fresh entry larger; adds smaller
    // TEXT tag so every fill is unambiguous on the chart (operator: «где OPEN, TP,
    // AVR, ENF, SL?»). A REVERSE fill both CLOSES the old position and OPENS the new
    // one on the same bar — label it «TP→OPEN» / «SL→OPEN» so the flip is visible
    // instead of the entry hiding behind the exit. TP/SL here = profit/loss close
    // (this strategy has no protective stop; a losing close reads as «SL»).
    let text: string;
    if (e.kind === 'reverse' && e.close) text = `${e.close.exit}→OPEN`;
    else if (e.close) text = e.close.exit;            // TP / SL (full or partial close)
    else if (e.kind === 'average') text = 'AVR';       // усреднение (добор против движения)
    else if (e.kind === 'enforce') text = 'ENF';       // усиление (добор в сторону прибыли)
    else text = 'OPEN';                                // свежий вход
    // Use the bucketed time DIRECTLY (no +1s uniqueness nudge). Markers are attached to
    // the candle series (setMarkers), which snaps them to the containing bar and allows
    // several markers on one bar — so we never inject off-grid time points into the shared
    // time scale (that interleave was what made arrows + boxes float off the candles).
    const tt = e.time;
    if (e.side === 'buy') {
      buy.points.push({ time: tt, value: e.price });
      buy.markers.push({ time: tt, position: 'belowBar', color, shape: 'arrowUp', size, text });
      index.push({ time: tt, price: e.price, side: 'buy', label: e.label, rawTime: e.rawTime, id: e.id, close: e.close });
    } else {
      sell.points.push({ time: tt, value: e.price });
      sell.markers.push({ time: tt, position: 'aboveBar', color, shape: 'arrowDown', size, text });
      index.push({ time: tt, price: e.price, side: 'sell', label: e.label, rawTime: e.rawTime, id: e.id, close: e.close });
    }
  }
  return { buy, sell, index };
}

// ─── Финрез с открытой позицией + доходность в год ──────────────────────────
// ПРАВИЛО ОПЕРАТОРА (STRICT): фин.рез ВЕЗДЕ = фикс (закрытые сделки) + ВМ
// открытой позиции. Голый realized при живой позиции — враньё: робот может
// сидеть в минусе и показывать «+0».

// ВМ открытой позиции робота = его СОБСТВЕННАЯ нереализованная прибыль от входа:
// pos × (тек. цена − средняя входа) × ₽/пункт ЭТОГО инструмента.
// Внимание: QUIK в таблице позиций даёт ВМ ЗА СЕССИЮ (с последнего клиринга,
// перенесённые позиции считаются от клиринговой цены, а не от входа) — это
// ДРУГАЯ величина, их нельзя сравнивать напрямую.
export function openVm(position: number, openAvg: number, last: number | null | undefined,
                       pointValue: number): number {
  const px = Number(last);
  if (!position || !(openAvg > 0) || !Number.isFinite(px) || px <= 0) return 0;
  const pv = Number(pointValue);
  if (!Number.isFinite(pv) || pv <= 0) return 0;
  return position * (px - openAvg) * pv;
}

// «Доходность в год»: прибыль (фикс + ВМ) к МАКСИМАЛЬНОМУ ГО, хоть раз
// задействованному с запуска, приведённая к году ЛИНЕЙНО (не сложным процентом:
// у робота с 10 днями истории компаундинг рисует астрономию).
// null = считать нечестно (нет ГО, нет даты старта или прошло < MIN_DAYS).
export const ANN_MIN_DAYS = 3;
export function annualizedPct(net: number, maxGo: number, startMs: number,
                              nowMs: number = Date.now()): number | null {
  if (!(maxGo > 0) || !(startMs > 0)) return null;
  const days = (nowMs - startMs) / 86400_000;
  if (!(days >= ANN_MIN_DAYS)) return null;      // слишком короткая история
  return (net / maxGo) * (365 / days) * 100;
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
  openAvg: number;        // avg entry of that open position (0 when flat)
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
                      qty: Math.abs(pos), side: pos > 0 ? 'sell' : 'buy',
                      order_id: `synthetic:${s}:${last.time + 1}`, synthetic: true } as any];
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
  const lastEv = cur?.events.length ? cur.events[cur.events.length - 1] : null;
  return { net, closes, peakContracts: peak, position: cur?.position ?? 0,
           openAvg: (cur?.position ?? 0) !== 0 ? (lastEv?.avgAfter ?? 0) : 0,
           currentSymbol, byContract, events: allEvents };
}

// One RECTANGLE per position episode (open → full close), for the chart overlay:
// x spans hold time (open→close), y spans the entry level (avg cost) → exit level.
// Long = green, short = red. Built from roll-aware events, so episodes never span the
// roll (a contract's synthetic settle closes its book). A still-open final episode
// extends to lastTime/lastPrice (the live position).
export interface PositionRect {
  dir: 'long' | 'short';
  tIn: number; pIn: number;     // ENTRY vertex: entry-fill time / entry-fill PRICE (not avg)
  tOut: number; pOut: number;   // EXIT vertex: close-fill time / close-fill price
  pnl: number;                  // realized P&L of the whole episode (₽, net of commission)
  open?: boolean;               // true = position still open (exit vertex = latest point)
}

// One rectangle per position EPISODE. Its diagonal runs ENTRY vertex -> EXIT vertex:
//   - entry vertex = the OPEN fill (its exact time + price). Averaging fills inside the
//     episode do NOT move the vertices (they only show as arrows inside the box).
//   - exit vertex  = the closing fill (full close or reverse), its exact time + price.
// dir = side of the position (long = opened by a buy, short = by a sell). pnl = the
// episode's realized result. A still-open episode extends to lastTime/lastPrice.
export function positionRects(events: TradeEvent[], lastTime?: number, lastPrice?: number): PositionRect[] {
  const rects: PositionRect[] = [];
  let pos = 0, pIn = 0, tIn = 0, dir: 'long' | 'short' = 'long', epPnl = 0;
  for (const e of events) {
    const q = Number(e.qty) || 1;
    const signed = e.side === 'buy' ? q : -q;
    if (e.kind === 'open') {
      pos = signed; pIn = e.price; tIn = e.time; dir = signed > 0 ? 'long' : 'short'; epPnl = 0; continue;
    }
    if (e.kind === 'average' || e.kind === 'enforce') { pos += signed; continue; }   // adds never move the vertices
    if (e.kind === 'partial') { pos += signed; if (e.close) epPnl += e.close.pnl; continue; }
    // full close or reverse: finalise this episode's rectangle
    if (e.close) epPnl += e.close.pnl;
    rects.push({ dir, tIn, pIn, tOut: e.time, pOut: e.price, pnl: epPnl });
    if (e.kind === 'reverse') { pos += signed; pIn = e.price; tIn = e.time; dir = pos > 0 ? 'long' : 'short'; epPnl = 0; }
    else { pos = 0; }
  }
  if (pos !== 0) rects.push({ dir, tIn, pIn, tOut: lastTime ?? tIn, pOut: lastPrice ?? pIn, pnl: epPnl, open: true });
  return rects;
}

// «Рассчитать эффект точно»: один прогон несёт ДВА комбо — параметры робота как
// есть и они же с выключенными фильтрами входа. /results сортирует строки по
// доходности, поэтому ветку узнаём по САМИМ параметрам, а не по порядку: без
// фильтров = обе ручки в нуле. Ноль строк / одна строка / обе без фильтров
// (робот и так без них) => null, звать это «эффектом» нельзя.
export function splitFilterRuns(rows: any[]): { on: any; off: any } | null {
  if (!Array.isArray(rows) || rows.length < 2) return null;
  const isOff = (r: any) => Number(r?.params?.min_gap_pts ?? 0) === 0
                         && Number(r?.params?.cooldown_min ?? 0) === 0;
  const off = rows.find(isOff);
  const on = rows.find((r) => !isOff(r));
  return off && on ? { on, off } : null;
}
