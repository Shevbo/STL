import { describe, it, expect } from 'vitest';
import { OfflinePlayer } from './offline-player';

describe('OfflinePlayer', () => {
  it('constructs without error', () => {
    const p = new OfflinePlayer();
    expect(p).toBeDefined();
  });

  it('stop() is safe before play()', () => {
    const p = new OfflinePlayer();
    expect(() => p.stop()).not.toThrow();
  });

  it('stop() is idempotent', () => {
    const p = new OfflinePlayer();
    p.stop();
    p.stop();
    expect(p).toBeDefined();
  });
});
