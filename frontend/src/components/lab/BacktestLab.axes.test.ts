import { describe, it, expect } from 'vitest';
import { deadAxes, collapseTies } from './BacktestLab.svelte';

const row = (params: any, net: number, trades = 100) =>
  ({ params, result: { net_profit: net, total_trades: trades } });

describe('мёртвые оси перебора', () => {
  it('ось, которая не сдвинула результат ни разу', () => {
    // sl_frac перебирали, tp_atr тоже — но при tp_atr=0 стопа нет (реальный случай).
    const rows = [
      row({ tp_atr: 0, sl_frac: 0 }, -100), row({ tp_atr: 0, sl_frac: 50 }, -100),
      row({ tp_atr: 10, sl_frac: 0 }, -80), row({ tp_atr: 10, sl_frac: 50 }, -80),
    ];
    expect(deadAxes(rows)).toEqual(['sl_frac']);
  });

  it('живая ось не попадает в список', () => {
    const rows = [
      row({ tp_atr: 0, sl_frac: 0 }, -100), row({ tp_atr: 0, sl_frac: 50 }, -90),
      row({ tp_atr: 10, sl_frac: 0 }, -80), row({ tp_atr: 10, sl_frac: 50 }, -70),
    ];
    expect(deadAxes(rows)).toEqual([]);
  });

  it('неперебираемые параметры не считаются мёртвыми', () => {
    // qty одинаков во всех строках — это не ось, а константа.
    const rows = [row({ qty: 1, tp_atr: 0 }, -100), row({ qty: 1, tp_atr: 10 }, -80)];
    expect(deadAxes(rows)).toEqual([]);
  });

  it('symbol никогда не ось', () => {
    const rows = [row({ symbol: 'RIU6', tp_atr: 0 }, -100), row({ symbol: 'GZU6', tp_atr: 0 }, -100)];
    expect(deadAxes(rows)).toEqual([]);
  });

  it('одна строка — судить не о чем', () => {
    expect(deadAxes([row({ tp_atr: 0 }, -100)])).toEqual([]);
    expect(deadAxes([])).toEqual([]);
  });
});

describe('схлопывание одинаковых результатов', () => {
  it('одинаковые деньги и сделки — один кандидат, а не N', () => {
    const rows = [
      row({ tp_atr: 0, sl_frac: 0 }, -100), row({ tp_atr: 0, sl_frac: 50 }, -100),
      row({ tp_atr: 0, sl_frac: 75 }, -100), row({ tp_atr: 10, sl_frac: 0 }, -80),
    ];
    const g = collapseTies(rows);
    expect(g).toHaveLength(2);
    expect(g[0].n).toBe(3);
    expect(g[0].varied).toEqual(['sl_frac']);   // именно он размножил строку
    expect(g[1].n).toBe(1);
    expect(g[1].varied).toEqual([]);
  });

  it('одинаковая прибыль при РАЗНОМ числе сделок — разные кандидаты', () => {
    const g = collapseTies([row({ a: 1 }, -100, 10), row({ a: 2 }, -100, 20)]);
    expect(g).toHaveLength(2);
  });

  it('схлопываются только идущие ПОДРЯД (список уже отсортирован рангом)', () => {
    const g = collapseTies([row({ a: 1 }, -100), row({ a: 2 }, -80), row({ a: 3 }, -100)]);
    expect(g.map((x) => x.n)).toEqual([1, 1, 1]);
  });

  it('порядок и лидер сохраняются', () => {
    const rows = [row({ a: 1 }, 500), row({ a: 2 }, 500), row({ a: 3 }, 100)];
    const g = collapseTies(rows);
    expect(g[0].row).toBe(rows[0]);            // клик откроет ПЕРВУЮ из группы
    expect(g[1].row.result.net_profit).toBe(100);
  });
});
