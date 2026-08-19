import { describe, it, expect } from 'vitest';
import { rowAnnGo } from './lab-analytics';

describe('годовая строки хитпарада', () => {
  it('линейная и одинаковая для старых и новых строк', () => {
    // Живой случай: +26.14% за 14 дней. В базе у старой строки лежало 458.97
    // (сложный процент), честная годовая — 6.81 доли = 681%.
    const r = rowAnnGo({ total_return: 0.2614, date_from: '2026-07-16', date_to: '2026-07-30' });
    expect(r).not.toBeNull();
    expect(r! * 100).toBeCloseTo(681.6, 0);
  });

  it('нет окна или нет доходности — нечем считать, не ноль', () => {
    expect(rowAnnGo({ total_return: 0.26, date_from: null, date_to: '2026-07-30' })).toBeNull();
    expect(rowAnnGo({ total_return: null, date_from: '2026-07-16', date_to: '2026-07-30' })).toBeNull();
    // Окно короче суток: делить не на что.
    expect(rowAnnGo({ total_return: 0.26, date_from: '2026-07-16', date_to: '2026-07-16' })).toBeNull();
  });

  it('убыток остаётся убытком', () => {
    expect(rowAnnGo({ total_return: -0.1, date_from: '2026-07-01', date_to: '2026-07-31' })!).toBeLessThan(0);
  });
});
