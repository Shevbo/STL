export interface Quote {
  symbol: string;
  bid: number;
  bidSize: number;
  ask: number;
  askSize: number;
  last: number;
  lastSize: number;
  timestamp: string; // ISO 8601
}

export interface Robot {
  id: string;
  name: string;
  symbol: string;
  deposit: number;
  pnl: number;
  tradeCount: number;
  position: number; // positive=long, negative=short, 0=flat
}

export interface AccountSummary {
  deposit: number;
  free: number;
  inPosition: number;
  variationMargin: number;
}

export interface OhlcBar {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeMarker {
  time: number;
  position: 'aboveBar' | 'belowBar';
  color: string;
  shape: 'arrowDown' | 'arrowUp';
  text: string;
}

export interface InstrumentMeta {
  symbol: string;
  ticker: string;
  name: string;
}

export interface OpenOrder {
  order_id: string;
  symbol: string;
  side: 'buy' | 'sell';
  price: number;
  qty: number;
  comment?: string;
  created_at?: number;
}

export interface TradeFill {
  trade_id: string;
  symbol: string;
  side: 'buy' | 'sell';
  price: number;
  time: number; // unix seconds
}

export interface BacktestResult {
  equityCurve: Array<{ time: number; value: number }>;
  totalPnl: number;
  tradeCount: number;
}

export interface Strategy {
  id: string;
  name: string;
  symbol: string;
  params: Record<string, unknown>;
  scriptPath?: string;
}

export type ServiceId = 'auth' | 'md' | 'tx' | 'oms' | 'pos' | 'audit';
export type ServiceStatus = 'ok' | 'warn' | 'error';

// WS messages from M8 API — discriminated union
export interface Position {
  symbol: string;
  account_id: string;
  side: 'long' | 'short' | 'flat';
  quantity: number;
  avg_price: number;
  current_price: number;
  var_margin: number;
}

export interface OrderRequest {
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  order_type: 'limit' | 'market';
  price?: number;
}

export interface OrderResponse {
  order_id: string;
  status: string;
}

export interface OrderBookLevel {
  price: number;
  size: number;
}

export type WsIncoming =
  | { type: 'quote'; symbol: string; bid: number; bid_size: number; ask: number; ask_size: number; last: number; last_size: number; timestamp: string }
  | { type: 'service_status'; service: ServiceId; status: ServiceStatus }
  | { type: 'account'; deposit: number; free: number; in_position: number; variation_margin: number }
  | { type: 'robot_update'; robots: Robot[] }
  | { type: 'position_update'; positions: Position[] }
  | { type: 'ohlc_history'; symbol: string; bars: OhlcBar[] }
  | { type: 'ohlc_update'; symbol: string; time: number; open: number; high: number; low: number; close: number; volume: number }
  | { type: 'orderbook'; symbol: string; bids: OrderBookLevel[]; asks: OrderBookLevel[] }
  | { type: 'order_update'; orders: OpenOrder[] }
  | { type: 'trade_update'; trades: TradeFill[] };
