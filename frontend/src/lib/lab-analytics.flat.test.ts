import { describe, it, expect } from 'vitest';
import { fromLastFlat, tradeEvents } from './lab-analytics';

const row = (side: string, qty: number, pos_after: number) => ({ side, qty, pos_after });

describe('журнал: кусок с доказуемого нуля', () => {
  it('окно, которое и так начинается с нуля, отдаётся целиком', () => {
    const rows = [row('buy', 2, 2), row('buy', 2, 4), row('sell', 4, 0)];
    expect(fromLastFlat(rows)).toHaveLength(3);
  });

  it('обрезанное лимитом окно режется до ПЕРВОГО нуля', () => {
    // Первая строка — середина позиции (историю срезал limit=1000).
    const rows = [row('buy', 1, 18), row('sell', 18, 0), row('buy', 2, 2), row('buy', 2, 4)];
    const got = fromLastFlat(rows);
    expect(got).toHaveLength(2);
    expect(got[0].pos_after).toBe(2);
  });

  it('нуля в окне нет — источнику доверять нельзя, пусто', () => {
    const rows = [row('buy', 1, 18), row('buy', 1, 19), row('sell', 1, 18)];
    expect(fromLastFlat(rows)).toEqual([]);
  });

  it('пустой журнал не роняет', () => {
    expect(fromLastFlat([])).toEqual([]);
  });
});

describe('ярлыки TP/SL', () => {
  // Живой случай 30.07.2026 16:11: позиция 34 по средней 89562.94 закрыта
  // частями по 89580..89610 — ВЫШЕ средней, то есть прибыль. График, стартовав с
  // середины позиции, красил это в SL.
  const F = (side: string, qty: number, price: number, time: number) => ({ side, qty, price, time } as any);

  it('замер с НУЛЯ: закрытие выше средней = TP', () => {
    const fills = [F('buy', 34, 89562.94, 1000), F('sell', 34, 89590, 2000)];
    const evs = tradeEvents(fills, 60, 1.58714, 'RIU6', true);
    const close = evs[1];
    expect(close.close?.exit).toBe('TP');
    expect(close.close!.pnl).toBeGreaterThan(0);
  });

  it('старт С СЕРЕДИНЫ позиции врёт: то же закрытие читается как вход', () => {
    // Без открывающих филлов replay считает первый филл ОТКРЫТИЕМ шорта,
    // и настоящего закрытия в окне уже нет — отсюда и брались ложные ярлыки.
    const evs = tradeEvents([F('sell', 34, 89590, 2000)], 60, 1.58714, 'RIU6', true);
    expect(evs[0].kind).toBe('open');
    expect(evs[0].close).toBeUndefined();
  });
});
