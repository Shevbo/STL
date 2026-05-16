// frontend/src/lib/offline-player.ts
import type { Quote } from './types';
import { quotesStore } from './stores/quotes.svelte';

interface TickRecord {
  symbol: string;
  bid: number;
  bid_size: number;
  ask: number;
  ask_size: number;
  last: number;
  last_size: number;
  timestamp: string;
}

export class OfflinePlayer {
  private ticks: TickRecord[] = [];
  private idx = 0;
  private rafId: number | null = null;
  private lastRealTime = 0;
  private lastTickTime = 0;

  async load(file: File): Promise<void> {
    const text = await file.text();
    this.ticks = JSON.parse(text) as TickRecord[];
    this.idx = 0;
  }

  play(): void {
    if (!this.ticks.length) return;
    this.lastRealTime = performance.now();
    this.lastTickTime = Date.parse(this.ticks[0].timestamp);
    this.schedule();
  }

  private schedule(): void {
    this.rafId = requestAnimationFrame((now) => {
      const elapsed = now - this.lastRealTime;
      this.lastRealTime = now;
      const targetTime = this.lastTickTime + elapsed;

      while (this.idx < this.ticks.length) {
        const t = this.ticks[this.idx];
        const tickTime = Date.parse(t.timestamp);
        if (tickTime > targetTime) break;
        const q: Quote = {
          symbol: t.symbol,
          bid: t.bid, bidSize: t.bid_size,
          ask: t.ask, askSize: t.ask_size,
          last: t.last, lastSize: t.last_size,
          timestamp: t.timestamp,
        };
        quotesStore.update(t.symbol, q);
        this.lastTickTime = tickTime;
        this.idx++;
      }

      if (this.idx < this.ticks.length) this.schedule();
    });
  }

  stop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }
}
