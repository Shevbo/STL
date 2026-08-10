import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import { fromLastFlat, tradeEvents, groupByOrder } from './lab-analytics';

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

describe('ярлыки закрытия: знак результата, а не причина', () => {
  // Живой случай 30.07.2026 16:11: позиция 34 по средней 89562.94 закрыта
  // частями по 89580..89610 — ВЫШЕ средней, то есть прибыль. График, стартовав с
  // середины позиции, красил это в SL.
  const F = (side: string, qty: number, price: number, time: number) => ({ side, qty, price, time } as any);

  it('замер с НУЛЯ: закрытие выше средней = в плюс', () => {
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

describe('частичные исполнения одного ордера', () => {
  const L = (ts: number, side: string, qty: number, price: number, order: string, pos: number) =>
    ({ ts_ms: ts * 1000, side, qty, price, order_num: order, pos_after: pos }) as any;

  it('один ордер = одна сделка, цена средневзвешенная', () => {
    // Живой agent-usopen 30.07.2026 16:57: ордер …612775 исполнился 1+1+1+2.
    const got = groupByOrder([
      L(1000, 'sell', 1, 88950, 'A', -1),
      L(1000, 'sell', 1, 88950, 'A', -2),
      L(1000, 'sell', 1, 88940, 'A', -3),
      L(1001, 'sell', 2, 88940, 'A', -5),
    ]);
    expect(got).toHaveLength(1);
    expect(got[0].qty).toBe(5);
    expect(got[0].price).toBeCloseTo((88950 * 2 + 88940 * 3) / 5, 6);
    expect(got[0].time).toBe(1001);            // время последнего исполнения
  });

  it('без схлопывания вход одним ордером читался как усреднение', () => {
    const parts = [
      L(1000, 'sell', 1, 88950, 'A', -1),
      L(1000, 'sell', 1, 88950, 'A', -2),
      L(1000, 'sell', 1, 88940, 'A', -3),
      L(1001, 'sell', 2, 88940, 'A', -5),
    ];
    const raw = tradeEvents(parts.map((r: any) => ({
      time: Math.floor(r.ts_ms / 1000), side: r.side, qty: r.qty, price: r.price })) as any,
      60, 1.58714, 'RIU6', true);
    // Части одного ордера читались как добор: «усреднение», потом дважды «усиление»
    // (цена ушла в сторону позиции) — четыре решения там, где было одно.
    expect(raw.map((e: any) => e.kind)).toEqual(['open', 'average', 'enforce', 'enforce']);

    const grouped = tradeEvents(groupByOrder(parts) as any, 60, 1.58714, 'RIU6', true);
    expect(grouped.map((e: any) => e.kind)).toEqual(['open']);
    expect(grouped[0].qty).toBe(5);
  });

  it('разные ордера остаются разными сделками и идут по времени', () => {
    const got = groupByOrder([
      L(2000, 'buy', 3, 89220, 'B', -2),
      L(1000, 'sell', 5, 88940, 'A', -5),
      L(2001, 'buy', 2, 89220, 'B', 0),
    ]);
    expect(got.map((g) => g.order_id)).toEqual(['A', 'B']);
    expect(got[1].qty).toBe(5);
  });

  it('строка без номера ордера не сливается с чужой', () => {
    const got = groupByOrder([
      { ts_ms: 1000, side: 'buy', qty: 1, price: 100, pos_after: 1, seq: 1 } as any,
      { ts_ms: 1001, side: 'buy', qty: 1, price: 101, pos_after: 2, seq: 2 } as any,
    ]);
    expect(got).toHaveLength(2);
  });
});

// ── Минус без стопа — это CLOSE, а не SL ─────────────────────────────────────
// Тейк у стратегий реестра есть всегда (tp_atr), поэтому плюс называем TP. Стоп
// по умолчанию ВЫКЛЮЧЕН, и вот тут врать нельзя: у робота lxk22 стоп выключен,
// закрытие по сигналу MACD на −36 руб подписали Stop-Loss, и оператор пошёл
// искать заявку, которой нет (письмо real-trade, 10.08.2026).
describe('ярлык закрытия: TP / SL / CLOSE', () => {
  const close = (px: number, hasStop: boolean) => {
    const fills = [
      { time: 1, side: 'buy', qty: 1, price: 100, order_id: 'a' },
      { time: 2, side: 'sell', qty: 1, price: px, order_id: 'b' },
    ];
    return tradeEvents(fills as never, 60, 1, '', true, hasStop).find((e) => e.close)!.close!;
  };

  it('плюс — это TP: тейк у стратегии есть всегда', () => {
    expect(close(110, false).exit).toBe('TP');
    expect(close(110, false).exitLabel).toBe('Полный TP');
  });

  it('минус со ВКЛЮЧЁННЫМ стопом — SL', () => {
    expect(close(90, true).exit).toBe('SL');
    expect(close(90, true).exitLabel).toBe('Полный SL');
  });

  it('минус без стопа — CLOSE, стопом не называем', () => {
    const c = close(90, false);
    expect(c.exit).toBe('CLOSE');
    expect(c.exitLabel).toBe('Полное закрытие');
    expect(c.exitLabel).not.toMatch(/SL|Stop/);
  });

  it('умолчание безопасное: не знаем про стоп — не утверждаем, что он сработал', () => {
    const fills = [
      { time: 1, side: 'buy', qty: 1, price: 100, order_id: 'a' },
      { time: 2, side: 'sell', qty: 1, price: 90, order_id: 'b' },
    ];
    const c = tradeEvents(fills as never, 60).find((e) => e.close)!.close!;
    expect(c.exit).toBe('CLOSE');
  });

  it('в исходнике классификатора нет слов Stop-Loss / Take-Profit', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/lib/lab-analytics.ts'), 'utf8');
    const fn = src.match(/function exitLabel\([\s\S]*?[\n\r]\}/)![0];
    expect(fn).not.toMatch(/Stop-Loss|Take-Profit/);
  });
});
