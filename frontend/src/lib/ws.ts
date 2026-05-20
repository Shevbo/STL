// frontend/src/lib/ws.ts
import type { WsIncoming, ServiceId } from './types';
import { quotesStore } from './stores/quotes.svelte';
import { servicesStore } from './stores/services.svelte';
import { accountStore } from './stores/account.svelte';
import { robotsStore } from './stores/robots.svelte';
import { positionsStore } from './stores/positions.svelte';
import { candlesStore } from './stores/candles.svelte';
import { orderbookStore } from './stores/orderbook.svelte';

export class WsClient {
  private ws: WebSocket | null = null;
  private pending: WsIncoming[] = [];
  private rafId: number | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly url: string) {}

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => servicesStore.set('md', 'ok');
    this.ws.onclose = () => {
      servicesStore.set('md', 'error');
      this.scheduleReconnect();
    };
    this.ws.onerror = () => servicesStore.set('md', 'warn');
    this.ws.onmessage = (evt: MessageEvent) => {
      const msg = JSON.parse(evt.data as string) as WsIncoming;
      this.pending.push(msg);
      if (this.rafId === null) {
        this.rafId = requestAnimationFrame(() => this.flush());
      }
    };
  }

  private flush(): void {
    this.rafId = null;
    const batch = this.pending.splice(0);
    for (const msg of batch) {
      if (msg.type === 'quote') {
        quotesStore.update(msg.symbol, {
          symbol: msg.symbol,
          bid: msg.bid, bidSize: msg.bid_size,
          ask: msg.ask, askSize: msg.ask_size,
          last: msg.last, lastSize: msg.last_size,
          timestamp: msg.timestamp,
        });
      } else if (msg.type === 'service_status') {
        servicesStore.set(msg.service as ServiceId, msg.status);
      } else if (msg.type === 'account') {
        accountStore.set({
          deposit: msg.deposit, free: msg.free,
          inPosition: msg.in_position, variationMargin: msg.variation_margin,
        });
      } else if (msg.type === 'robot_update') {
        robotsStore.set(msg.robots);
      } else if (msg.type === 'position_update') {
        positionsStore.set(msg.positions);
      } else if (msg.type === 'ohlc_history') {
        candlesStore.setHistory(msg.symbol, msg.bars);
      } else if (msg.type === 'ohlc_update') {
        candlesStore.upsertBar(msg.symbol, {
          time: msg.time, open: msg.open, high: msg.high,
          low: msg.low, close: msg.close, volume: msg.volume,
        });
      } else if (msg.type === 'orderbook') {
        orderbookStore.set(msg.symbol, { bids: msg.bids, asks: msg.asks });
      }
    }
  }

  private scheduleReconnect(): void {
    this.reconnectTimer = setTimeout(() => this.connect(), 2000);
  }

  disconnect(): void {
    if (this.rafId !== null) { cancelAnimationFrame(this.rafId); this.rafId = null; }
    if (this.reconnectTimer !== null) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    this.ws?.close(1000);
    this.ws = null;
  }
}
