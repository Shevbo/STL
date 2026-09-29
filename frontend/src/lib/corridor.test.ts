// Коридор: перевод трёх кликов по графику в параметры заявки.
//
// Заказ оператора 29.09.2026 — ставить канал линиями, а не цифрами. Ошибиться
// здесь можно молча: коридор уедет на наклон или на полшага, и увидит это
// только рынок. Каждый тест ниже сторожит ровно одну из названных real-trade
// ловушек перевода.
import { describe, it, expect } from 'vitest';
import {
  corridorBounds, corridorFromClicks, corridorSlope, corridorState,
  corridorWidth, snapPrice, snapToBar,
} from './smart-order-help';

const T0 = 1_790_600_000_000;
const MIN = 60_000;

describe('коридор: стенки', () => {
  it('нижняя ПАРАЛЛЕЛЬНА верхней, а не сходится клином', () => {
    const g = { c_t1_ms: T0, c_p1: 83_000, c_t2_ms: T0 + 60 * MIN, c_p2: 83_600, c_low: 82_400 };
    const width = g.c_p1 - g.c_low;
    for (const mins of [0, 30, 60, 240]) {
      const b = corridorBounds(g, T0 + mins * MIN);
      expect(b.top - b.low).toBeCloseTo(width, 6);   // ширина неизменна
    }
  });

  it('линия продолжается и ЗА вторую точку', () => {
    const g = { c_t1_ms: T0, c_p1: 83_000, c_t2_ms: T0 + 60 * MIN, c_p2: 83_600, c_low: 82_400 };
    expect(corridorBounds(g, T0 + 120 * MIN).top).toBeCloseTo(84_200, 6);
  });

  it('равные цены точек — горизонтальный коридор', () => {
    const g = { c_t1_ms: T0, c_p1: 83_000, c_t2_ms: T0 + 60 * MIN, c_p2: 83_000, c_low: 82_400 };
    expect(corridorSlope(g)).toBe(0);
    expect(corridorBounds(g, T0 + 999 * MIN).top).toBe(83_000);
  });

  // Деление на ноль вместо наклона дало бы Infinity и стенку в NaN.
  it('точки в одном времени — канал горизонтальный, а не Infinity', () => {
    expect(corridorSlope({ c_t1_ms: T0, c_p1: 83_000, c_t2_ms: T0, c_p2: 83_600 })).toBe(0);
  });
});

describe('коридор: три клика', () => {
  const bars = Array.from({ length: 300 }, (_, i) => T0 + i * MIN);

  it('третий клик В ЛЮБОМ времени: цена приводится к первой точке', () => {
    // Наклон +10 пунктов в минуту. Нижнюю кликнули на 60-й минуте по 83 000 —
    // в момент первой точки это 82 400. Без пересчёта коридор уехал бы на 600.
    const r = corridorFromClicks([
      { ms: T0, price: 83_000 },
      { ms: T0 + 60 * MIN, price: 83_600 },
      { ms: T0 + 60 * MIN, price: 83_000 },
    ], 10, bars);
    expect(r.error).toBeNull();
    expect(r.geom!.c_low).toBe(82_400);
    // И стенка под третьим кликом действительно проходит там, куда ткнули.
    expect(corridorBounds(r.geom!, T0 + 60 * MIN).low).toBeCloseTo(83_000, 6);
  });

  it('клики справа налево: точки переставляются, движок требует t2 > t1', () => {
    const r = corridorFromClicks([
      { ms: T0 + 60 * MIN, price: 83_600 },
      { ms: T0, price: 83_000 },
      { ms: T0, price: 82_400 },
    ], 10, bars);
    expect(r.error).toBeNull();
    expect(r.geom!.c_t1_ms).toBeLessThan(r.geom!.c_t2_ms);
    expect(r.geom!.c_p1).toBe(83_000);
    expect(r.geom!.c_p2).toBe(83_600);
  });

  it('цена квантуется по шагу инструмента', () => {
    const r = corridorFromClicks([
      { ms: T0, price: 83_004 },
      { ms: T0 + 60 * MIN, price: 83_597 },
      { ms: T0, price: 82_403 },
    ], 10, bars);
    expect(r.geom!.c_p1).toBe(83_000);
    expect(r.geom!.c_p2).toBe(83_600);
    expect(r.geom!.c_low).toBe(82_400);
  });

  it('время берётся из БАРА, а не из пикселя', () => {
    const r = corridorFromClicks([
      { ms: T0 + 17_000, price: 83_000 },          // ткнули между барами
      { ms: T0 + 60 * MIN + 41_000, price: 83_600 },
      { ms: T0, price: 82_400 },
    ], 10, bars);
    // Оба времени сели НА СЕТКУ БАРОВ, каждое на ближайший: 17 с после бара —
    // назад к нему, 41 с — вперёд к следующему.
    expect(r.geom!.c_t1_ms).toBe(T0);
    expect(r.geom!.c_t2_ms).toBe(T0 + 61 * MIN);
    for (const t of [r.geom!.c_t1_ms, r.geom!.c_t2_ms]) expect(bars).toContain(t);
  });

  it('обе точки в одном баре — отказ, а не наклон из деления на ноль', () => {
    const r = corridorFromClicks([
      { ms: T0, price: 83_000 }, { ms: T0 + 5_000, price: 83_600 }, { ms: T0, price: 82_400 },
    ], 10, bars);
    expect(r.geom).toBeNull();
    expect(r.error).toContain('один бар');
  });

  it('нижняя выше верхней — отказ теми же словами, что у движка', () => {
    const r = corridorFromClicks([
      { ms: T0, price: 83_000 }, { ms: T0 + 60 * MIN, price: 83_600 }, { ms: T0, price: 84_000 },
    ], 10, bars);
    expect(r.geom).toBeNull();
    expect(r.error).toContain('НИЖЕ верхней');
  });

  it('кликов меньше трёх — отказ, а не половина коридора', () => {
    expect(corridorFromClicks([{ ms: T0, price: 83_000 }], 10, bars).geom).toBeNull();
  });
});

describe('коридор: шаг цены', () => {
  it('дробный шаг не округляется к целым (BR — 0.01)', () => {
    expect(snapPrice(95.174, 0.01)).toBe(95.17);
    expect(snapPrice(95.176, 0.01)).toBe(95.18);
  });

  it('шаг неизвестен — цену не трогаем', () => {
    expect(snapPrice(95.174, 0)).toBe(95.174);
  });

  it('баров нет — клик остаётся собой, а не превращается в ноль', () => {
    expect(snapToBar(T0 + 123, [])).toBe(T0 + 123);
  });
});

describe('коридор: что показать оператору', () => {
  const g = { c_t1_ms: T0, c_p1: 83_000, c_t2_ms: T0 + 60 * MIN, c_p2: 83_600, c_low: 82_400 };

  it('ширина канала в пунктах и в рублях', () => {
    expect(corridorWidth(g, 1.5681)).toEqual({ pts: 600, rub: 600 * 1.5681 });
  });

  it('₽/пункт неизвестна — рублей НЕТ, а не ноль рублей', () => {
    expect(corridorWidth(g, 0).rub).toBeNull();
  });

  it('состояние читается словами и не спорит со сторожем', () => {
    expect(corridorState({ c_pos: -2, c_flips: 1, c_flips_max: 5 })).toBe('шорт 2 · переворотов 1 из 5');
    expect(corridorState({ c_pos: 3, c_flips: 0, c_flips_max: 0 })).toBe('лонг 3 · переворотов 0 (без предела)');
    expect(corridorState({ c_pos: 0, c_done: true })).toContain('коридор закончен');
  });
});
