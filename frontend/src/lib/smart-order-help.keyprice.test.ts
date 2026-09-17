// Ключевая цена умной заявки для крупной колонки списка (15.09.2026). Цена у
// типов разная, и подпись обязана говорить, что это за цена; нет числа — пусто,
// а не придуманный ноль.
import { describe, it, expect } from 'vitest';
import { keyPrice, sortBySideAndPrice } from './smart-order-help';

describe('keyPrice', () => {
  it('следящая до пробоя: уровень активации', () => {
    expect(keyPrice({ kind: 'trail_tp', side: 'buy', trigger_price: 86300, trail_offset: 50 }))
      .toEqual({ label: 'активация', price: 86300 });
  });

  it('следящая в слежении: цена выхода от пика, со стороной', () => {
    expect(keyPrice({ kind: 'trail_tp', side: 'buy', activated: true, peak: 86000, trail_offset: 50, trigger_price: 86300 }))
      .toEqual({ label: 'выход', price: 86050 });
    expect(keyPrice({ kind: 'trail_tp', side: 'sell', activated: true, peak: 90400, trail_offset: 90, trigger_price: 90290 }))
      .toEqual({ label: 'выход', price: 90310 });
  });

  it('следящая без уровня активации — числа нет, не ноль', () => {
    expect(keyPrice({ kind: 'trail_tp', side: 'sell', trigger_price: 0, trail_offset: 50 }))
      .toEqual({ label: 'следит сразу', price: null });
  });

  it('стоп и тейк: уровень срабатывания с направлением', () => {
    expect(keyPrice({ kind: 'sl', side: 'sell', trigger_price: 87420 })).toEqual({ label: 'стоп ≤', price: 87420 });
    expect(keyPrice({ kind: 'tp', side: 'sell', trigger_price: 88470 })).toEqual({ label: 'тейк ≥', price: 88470 });
  });

  it('зависимая: своя цена или по рынку', () => {
    expect(keyPrice({ kind: 'on_fill', side: 'buy', child_price: 85000 })).toEqual({ label: 'цена', price: 85000 });
    expect(keyPrice({ kind: 'on_fill', side: 'buy', child_price: 0 })).toEqual({ label: 'по рынку', price: null });
  });

  it('подтягивающий стоп до позиции — числа нет', () => {
    expect(keyPrice({ kind: 'trail_sl', side: 'sell', peak: 0, trail_offset: 300 }))
      .toEqual({ label: 'ждёт позицию', price: null });
    expect(keyPrice({ kind: 'trail_sl', side: 'sell', peak: 88000, trail_offset: 300 }))
      .toEqual({ label: 'выход', price: 87700 });
  });
});


describe('порядок заявок на экране', () => {
  const o = (side: string, kind: string, extra: Record<string, unknown> = {}) =>
    ({ side, kind, ...extra });

  it('продажи сверху, покупки снизу, внутри стороны цена по убыванию', () => {
    const list = [
      o('buy', 'sl', { trigger_price: 88000 }),
      o('sell', 'tp', { trigger_price: 90500 }),
      o('buy', 'tp', { trigger_price: 89000 }),
      o('sell', 'trail_tp', { trigger_price: 91000 }),
    ];
    expect(sortBySideAndPrice(list).map((x) => [x.side, keyPrice(x).price]))
      .toEqual([['sell', 91000], ['sell', 90500], ['buy', 89000], ['buy', 88000]]);
  });

  it('у следящей в слежении цена берётся от пика, а не от уровня активации', () => {
    const list = [
      o('sell', 'trail_tp', { trigger_price: 90000 }),
      o('sell', 'trail_tp', { activated: true, peak: 92000, trail_offset: 100, trigger_price: 89000 }),
    ];
    // Вторая активна и выйдет на 91900 — она выше первой, хотя её активация ниже.
    expect(sortBySideAndPrice(list).map((x) => keyPrice(x).price)).toEqual([91900, 90000]);
  });

  it('заявки без своей цены уходят в конец своей стороны, а не наверх', () => {
    const list = [
      o('sell', 'on_fill', { child_price: 0 }),
      o('sell', 'sl', { trigger_price: 87000 }),
      o('buy', 'trail_sl', { peak: 0, trail_offset: 300 }),
      o('buy', 'tp', { trigger_price: 86000 }),
    ];
    expect(sortBySideAndPrice(list).map((x) => x.kind))
      .toEqual(['sl', 'on_fill', 'tp', 'trail_sl']);
  });

  it('пустой список и отсутствие списка не ломают экран', () => {
    expect(sortBySideAndPrice([])).toEqual([]);
    expect(sortBySideAndPrice(null)).toEqual([]);
  });
});
