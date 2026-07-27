// Финрез с открытой позицией + доходность в год: правила оператора 27.07.2026.
import { describe, expect, it } from 'vitest';
import { ANN_MIN_DAYS, annualizedPct, openVm } from './lab-analytics';

const DAY = 86400_000;

describe('openVm — ВМ открытой позиции (от входа)', () => {
  it('лонг в минусе: живой BRU6 3 конт. от 92.6533 при 86.69', () => {
    // (86.69 - 92.6533) * 3 * 780.308 = -13 960 ₽ (совпало с боем 27.07)
    expect(Math.round(openVm(3, 92.6533, 86.69, 780.308))).toBe(-13960);
  });

  it('шорт в плюсе: знак от позиции, не от цены', () => {
    expect(Math.round(openVm(-2, 90000, 89000, 1.560616))).toBe(3121);
  });

  it('нет позиции / нет средней / нет цены → 0, а не NaN', () => {
    expect(openVm(0, 92.6, 86.69, 780.308)).toBe(0);
    expect(openVm(3, 0, 86.69, 780.308)).toBe(0);
    expect(openVm(3, 92.6, null, 780.308)).toBe(0);
    expect(openVm(3, 92.6, 86.69, 0)).toBe(0);
  });
});

describe('annualizedPct — доходность в год', () => {
  const now = 1_785_000_000_000;

  it('+10% от ГО за 36.5 дня → ~100% годовых (линейно)', () => {
    const v = annualizedPct(10_000, 100_000, now - 36.5 * DAY, now);
    expect(v).not.toBeNull();
    expect(Math.round(v!)).toBe(100);
  });

  it('убыток даёт отрицательную годовую', () => {
    expect(annualizedPct(-5_000, 100_000, now - 73 * DAY, now)!).toBeCloseTo(-25, 6);
  });

  it('короткая история и нулевое ГО — честный null, а не бесконечность', () => {
    expect(annualizedPct(1_000, 100_000, now - (ANN_MIN_DAYS - 0.1) * DAY, now)).toBeNull();
    expect(annualizedPct(1_000, 0, now - 30 * DAY, now)).toBeNull();
    expect(annualizedPct(1_000, 100_000, 0, now)).toBeNull();
  });
});
