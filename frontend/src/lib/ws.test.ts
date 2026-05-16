// frontend/src/lib/ws.test.ts
import { describe, it, expect } from 'vitest';
import type { WsIncoming } from './types';

describe('WsIncoming message parsing', () => {
  it('parses quote message shape', () => {
    const raw = JSON.stringify({
      type: 'quote', symbol: 'GZM6@RTSX',
      bid: 100, bid_size: 5, ask: 101, ask_size: 3,
      last: 100.5, last_size: 2, timestamp: '2026-05-16T10:00:00Z',
    });
    const msg = JSON.parse(raw) as WsIncoming;
    expect(msg.type).toBe('quote');
    if (msg.type === 'quote') {
      expect(msg.bid).toBe(100);
      expect(msg.bid_size).toBe(5);
    }
  });

  it('parses service_status message', () => {
    const raw = JSON.stringify({ type: 'service_status', service: 'md', status: 'ok' });
    const msg = JSON.parse(raw) as WsIncoming;
    expect(msg.type).toBe('service_status');
    if (msg.type === 'service_status') {
      expect(msg.status).toBe('ok');
    }
  });

  it('parses account message', () => {
    const raw = JSON.stringify({
      type: 'account', deposit: 800000, free: 412000,
      in_position: 320000, variation_margin: 4200,
    });
    const msg = JSON.parse(raw) as WsIncoming;
    expect(msg.type).toBe('account');
    if (msg.type === 'account') {
      expect(msg.deposit).toBe(800000);
    }
  });

  it('parses robot_update message', () => {
    const raw = JSON.stringify({
      type: 'robot_update',
      robots: [{ id: 'r1', name: 'Test', symbol: 'GZM6@RTSX', deposit: 500000, pnl: 0, tradeCount: 0, position: 0 }],
    });
    const msg = JSON.parse(raw) as WsIncoming;
    expect(msg.type).toBe('robot_update');
    if (msg.type === 'robot_update') {
      expect(msg.robots).toHaveLength(1);
    }
  });
});
