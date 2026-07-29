import { describe, it, expect } from 'vitest';
import { splitFilterRuns } from './lab-analytics';

const row = (min_gap_pts: number, cooldown_min: number, net: number) =>
  ({ params: { min_gap_pts, cooldown_min, qty: 2 }, net_profit: net });

describe('splitFilterRuns', () => {
  it('находит ветки по параметрам, а не по порядку строк', () => {
    const s = splitFilterRuns([row(0, 0, 120_000), row(200, 1, 87_000)])!;
    expect(s.off.net_profit).toBe(120_000);
    expect(s.on.net_profit).toBe(87_000);
  });
  it('порядок не важен', () => {
    const s = splitFilterRuns([row(200, 1, 87_000), row(0, 0, 120_000)])!;
    expect(s.on.params.min_gap_pts).toBe(200);
    expect(s.off.params.min_gap_pts).toBe(0);
  });
  it('только остывание тоже считается фильтром', () => {
    const s = splitFilterRuns([row(0, 5, 90_000), row(0, 0, 100_000)])!;
    expect(s.on.params.cooldown_min).toBe(5);
  });
  it('нет второй ветки — не эффект, а null', () => {
    expect(splitFilterRuns([row(200, 1, 87_000)])).toBeNull();
    expect(splitFilterRuns([])).toBeNull();
    expect(splitFilterRuns([row(0, 0, 1), row(0, 0, 2)])).toBeNull();
  });
});
