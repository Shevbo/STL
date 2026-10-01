// Радиация: сетка заявок вокруг цены постановки. Движок real-trade от
// 30.09.2026 (kind="grid"), экран наш. Числа здесь — зеркало grid_price /
// grid_levels / grid_stop_levels из trader/quik/smart_orders.py: разойдясь, они
// покажут оператору одни уровни, пока заявки стоят на других.
import { describe, it, expect } from 'vitest';
import {
  gridLevels, gridState, gridStopLevels, gridWorstCase, preview, type Kind,
} from './smart-order-help';

const G = { g_base: 84_000, g_step: 50, g_buys: 3, g_sells: 2, g_lot: 2, g_stop_pts: 100 };

describe('уровни сетки', () => {
  it('покупки вниз, продажи вверх, от цены постановки', () => {
    expect(gridLevels(G).map((x) => [x.price, x.side])).toEqual([
      [83_850, 'buy'], [83_900, 'buy'], [83_950, 'buy'],
      [84_050, 'sell'], [84_100, 'sell'],
    ]);
  });

  // Исполнилась покупка — на её месте встаёт продажа: это и есть тейк в один
  // шаг сетки. Экран обязан показывать, что стоит СЕЙЧАС.
  it('сторона на уровне переворачивается после исполнения', () => {
    const got = gridLevels({ ...G, g_live: { 'flip:-2': true } });
    expect(got.find((x) => x.level === -2)!.side).toBe('sell');
    expect(got.find((x) => x.level === -1)!.side).toBe('buy');
  });

  it('нет цены постановки или шага — уровней нет, а не ноль', () => {
    expect(gridLevels({ ...G, g_base: 0 })).toEqual([]);
    expect(gridLevels({ ...G, g_step: 0 })).toEqual([]);
  });

  it('односторонняя сетка законна', () => {
    expect(gridLevels({ ...G, g_sells: 0 }).every((x) => x.side === 'buy')).toBe(true);
  });
});

describe('стоп сетки', () => {
  it('за крайним уровнем с обеих сторон', () => {
    expect(gridStopLevels(G)).toEqual({ lo: 83_750, hi: 84_200 });
  });

  it('без стопа — нулей не выдаём за уровни', () => {
    expect(gridStopLevels({ ...G, g_stop_pts: 0 })).toEqual({ lo: 0, hi: 0 });
  });

  it('у пустой стороны стопа нет', () => {
    expect(gridStopLevels({ ...G, g_sells: 0 }).hi).toBe(0);
  });
});

describe('худший случай сетки', () => {
  // Оператор ставит объём на УРОВЕНЬ, а в рынке окажется объём × число уровней.
  // Не сказать это значит дать ему недооценить позицию в разы.
  it('считает полную позицию, а не объём одной заявки', () => {
    const w = gridWorstCase(G, 1.5681);
    expect(w.contracts).toBe(6);          // 2 контракта × 3 ступени вниз
    expect(w.side).toBe('buy');
    expect(w.riskRub).toBeCloseTo(w.riskPts * 1.5681, 6);
  });

  it('₽/пункт неизвестна — рублей НЕ выдумываем', () => {
    expect(gridWorstCase(G, 0).riskRub).toBeNull();
  });

  it('пустая сетка — ни контрактов, ни стороны', () => {
    expect(gridWorstCase({ g_base: 1, g_step: 1, g_lot: 0 })).toMatchObject(
      { contracts: 0, side: null });
  });
});

describe('состояние сетки словами', () => {
  it('позиция и сколько заявок стоит в стакане', () => {
    expect(gridState({ ...G, g_pos: -4, g_live: { '-1': 'a', '2': 'b', 'flip:-1': true } }))
      .toBe('шорт 4 · 2 заявки в стакане');
  });

  it('законченная сетка говорит об этом', () => {
    expect(gridState({ ...G, g_pos: 0, g_done: true })).toContain('сетка закончена');
  });
});

// Своя ветка превью обязательна: без неё новый вид падал бы в общий разбор и
// просил чужие поля — ровно так 30.09.2026 коридор требовал «заявку, за
// исполнением которой следим», и взвести его было нельзя вообще.
describe('превью радиации', () => {
  const base = {
    kind: 'grid' as Kind, side: 'buy' as const, qty: 1, code: 'RIZ6',
    trigger: 0, trailOffset: 0, watchId: '', childPrice: 0, price: 84_000,
    pointValue: 1.5681, gStep: 50, gBuys: 3, gSells: 2, gLot: 2, gStopPts: 100,
  };

  it('заполненная сетка взводится и называет худший случай', () => {
    const r = preview(base);
    expect(r.error).toBe('');
    expect(r.sentence).toContain('сетк');
    expect(r.sentence).not.toContain('следим');
    expect(r.distance).toContain('6');          // полная позиция, а не 2
  });

  it('без шага не взводится и говорит про шаг', () => {
    expect(preview({ ...base, gStep: 0 }).error).toContain('Шаг');
  });

  it('без единого уровня не взводится', () => {
    expect(preview({ ...base, gBuys: 0, gSells: 0 }).error).toContain('уровень');
  });

  it('без объёма на уровень не взводится', () => {
    expect(preview({ ...base, gLot: 0 }).error).toContain('бъём');
  });

  it('без стопа предупреждает, что сама сетка не выйдет', () => {
    expect(preview({ ...base, gStopPts: 0 }).sentence).toContain('сама не выйдет');
  });
});
