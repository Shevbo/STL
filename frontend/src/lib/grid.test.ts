// Радиация: сетка заявок вокруг цены постановки. Движок real-trade от
// 30.09.2026 (kind="grid"), экран наш. Числа здесь — зеркало grid_price /
// grid_levels / grid_stop_levels из trader/quik/smart_orders.py: разойдясь, они
// покажут оператору одни уровни, пока заявки стоят на других.
import { describe, it, expect } from 'vitest';
import {
  gridLevels, gridLevelsSummary, gridState, gridStopLevels, gridWorstCase,
  isTwoSided, preview, type Kind,
} from './smart-order-help';

const G = { g_base: 84_000, g_step: 50, g_buys: 3, g_sells: 2, g_lot: 2, g_stop_pts: 100 };

describe('уровни сетки', () => {
  // ИСПРАВЛЕНИЕ real-trade 01.10.2026: первая версия механики была ошибкой
  // КОНСТРУКЦИИ, а не описания. Встречная заявка вставала на ТОЙ ЖЕ цене —
  // круг с нулевой прибылью и двойной комиссией; уровень −1 отработал так
  // трижды подряд, и заметил это оператор, а не тесты: тесты закрепляли ту же
  // ошибку. Поэтому здесь проверяется ИСПРАВЛЕННАЯ модель.

  it('цены считаются от базы, ноль уровнем не является', () => {
    expect(gridLevels(G).map((x) => x.price)).toEqual([
      83_850, 83_900, 83_950, 84_050, 84_100,
    ]);
    expect(gridLevels(G).some((x) => x.level === 0)).toBe(false);
  });

  // Сторона — по какую сторону РЫНКА уровень сейчас. Постоянной лестницы
  // сторон у сетки нет, и это главное, что было описано неверно.
  it('сторона определяется рынком, а не номером уровня', () => {
    const atTop = gridLevels(G, 84_090);          // рынок выше трёх уровней
    expect(atTop.find((x) => x.price === 83_850)!.side).toBe('buy');
    expect(atTop.find((x) => x.price === 84_050)!.side).toBe('buy');
    expect(atTop.find((x) => x.price === 84_100)!.side).toBe('sell');
  });

  it('рынок неизвестен — стороны НЕ выдумываем', () => {
    expect(gridLevels(G).every((x) => x.side === null)).toBe(true);
  });

  // Три состояния, и погашенный нельзя путать с несуществующим: он вернётся,
  // когда отработает сосед.
  it('состояние уровня читается из g_live', () => {
    const got = gridLevels({ ...G, g_live: { '-1': 'cid', 'flip:-2': true } });
    expect(got.find((x) => x.level === -1)!.state).toBe('live');
    expect(got.find((x) => x.level === -2)!.state).toBe('spent');
    expect(got.find((x) => x.level === 2)!.state).toBe('off');
  });

  it('сводка называет каждое состояние своим словом', () => {
    const sum = gridLevelsSummary(gridLevels({ ...G, g_live: { '-1': 'c', 'flip:-2': true } }));
    expect(sum).toContain('1 стоит');
    expect(sum).toContain('1 погашен');
    expect(sum).toContain('не выставлен');
  });

  it('нет цены постановки или шага — уровней нет, а не ноль', () => {
    expect(gridLevels({ ...G, g_base: 0 })).toEqual([]);
    expect(gridLevels({ ...G, g_step: 0 })).toEqual([]);
  });

  it('односторонняя сетка законна', () => {
    expect(gridLevels({ ...G, g_sells: 0 })).toHaveLength(3);
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
  it('позиция и разбор уровней по состояниям', () => {
    const t = gridState({ ...G, g_pos: -4, g_live: { '-1': 'a', '2': 'b', 'flip:-3': true } });
    expect(t).toContain('шорт 4');
    expect(t).toContain('2 стоят');
    expect(t).toContain('1 погашен');
    // Слова «тейк» у сетки нет как понятия: встречная заявка на той же цене —
    // это и была ошибка конструкции, которую чинили 01.10.2026.
    expect(t).not.toContain('тейк');
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

// Сторона у двусторонних типов — служебная: движок ставит её под каждую сделку
// сам. «Купить/Продать» на форме обещает выбор, которого нет, и оператор вправе
// решить, что направление выбрал (01.10.2026).
describe('кто выбирает сторону', () => {
  it('фигуры и сетка торгуют в обе стороны', () => {
    for (const k of ['corridor', 'triangle', 'grid'] as Kind[]) {
      expect(isTwoSided(k), k).toBe(true);
    }
  });

  it('у односторонних типов выбор стороны остаётся', () => {
    for (const k of ['sl', 'tp', 'trail_tp', 'trail_sl', 'on_fill'] as Kind[]) {
      expect(isTwoSided(k), k).toBe(false);
    }
  });

  // Правило живёт в одном месте и для формы, и для карточки: разойдясь, экран
  // спрятал бы кнопки, но подписал заявку «ПРОДАЖА» — хуже обоих вариантов.
  it('список покрывает ровно те типы, что ведут позицию сами', () => {
    expect(isTwoSided('')).toBe(false);
    expect(isTwoSided(null)).toBe(false);
  });
});
