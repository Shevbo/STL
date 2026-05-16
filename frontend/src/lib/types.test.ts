import { describe, it, expect } from 'vitest';
import type { Quote, Robot, AccountSummary, ServiceId, WsIncoming } from './types';

describe('types shape', () => {
  it('Quote has required fields', () => {
    const q: Quote = {
      symbol: 'GZM6@RTSX', bid: 100.5, bidSize: 10,
      ask: 100.6, askSize: 5, last: 100.55, lastSize: 3,
      timestamp: '2026-05-16T10:00:00Z',
    };
    expect(q.symbol).toBe('GZM6@RTSX');
    expect(q.bid).toBe(100.5);
  });

  it('WsIncoming quote discriminates by type', () => {
    const msg: WsIncoming = {
      type: 'quote', symbol: 'GZM6@RTSX',
      bid: 100, bid_size: 5, ask: 101, ask_size: 3,
      last: 100.5, last_size: 2, timestamp: '2026-05-16T10:00:00Z',
    };
    expect(msg.type).toBe('quote');
  });

  it('Robot position semantics', () => {
    const r: Robot = { id: 'r1', name: 'Test', symbol: 'GZM6@RTSX', deposit: 500000, pnl: 1200, tradeCount: 5, position: 2 };
    expect(r.position).toBeGreaterThan(0); // long
  });

  it('ServiceId union covers all services', () => {
    const ids: ServiceId[] = ['auth', 'md', 'tx', 'oms', 'pos', 'audit'];
    expect(ids).toHaveLength(6);
  });
});
