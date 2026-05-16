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
export type WsIncoming =
  | { type: 'quote'; symbol: string; bid: number; bid_size: number; ask: number; ask_size: number; last: number; last_size: number; timestamp: string }
  | { type: 'service_status'; service: ServiceId; status: ServiceStatus }
  | { type: 'account'; deposit: number; free: number; in_position: number; variation_margin: number }
  | { type: 'robot_update'; robots: Robot[] };
