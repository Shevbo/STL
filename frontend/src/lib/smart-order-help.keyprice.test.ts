// Ключевая цена умной заявки для крупной колонки списка (15.09.2026). Цена у
// типов разная, и подпись обязана говорить, что это за цена; нет числа — пусто,
// а не придуманный ноль.
import { describe, it, expect } from 'vitest';
import { KINDS, STATUS_RU, defaultCode, isLive, keyPrice, sortBySideAndPrice } from './smart-order-help';

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


describe('статус native — защита под охраной терминала', () => {
  it('native живая: делить список по одному armed значит хоронить действующую защиту', () => {
    expect(isLive('armed')).toBe(true);
    expect(isLive('native')).toBe(true);
    expect(isLive('fired')).toBe(false);
    expect(isLive('cancelled')).toBe(false);
    expect(isLive(undefined)).toBe(false);
  });

  it('подпись говорит, КТО держит защиту, а не просто «взведена»', () => {
    expect(STATUS_RU.native).toBe('под охраной терминала');
    expect(STATUS_RU.armed).toBe('взведена');
  });

  it('native встаёт в лестницу уровней наравне с armed', () => {
    const list = [
      { side: 'sell', kind: 'sl', status: 'armed', trigger_price: 90000 },
      { side: 'sell', kind: 'sl', status: 'native', trigger_price: 91000 },
    ];
    expect(sortBySideAndPrice(list).map((x) => x.status)).toEqual(['native', 'armed']);
  });
});


describe('инструмент по умолчанию в форме', () => {
  const book = [{ code: 'RIZ6' }, { code: 'RIZ6' }, { code: 'RIZ6' }, { code: 'BRZ6' }];
  const feed = ['BRZ6', 'GDU6', 'RIZ6', 'SiZ6'];

  it('самый используемый из книги, а не символ с графика', () => {
    // 18.09.2026: форма подставляла GDU6 только потому, что на графике был газ.
    expect(defaultCode(book, feed, 'GDU6@RTSX')).toBe('RIZ6');
  });

  it('книга пуста — берём символ экрана, если такой код торгуется', () => {
    expect(defaultCode([], feed, 'GDU6@RTSX')).toBe('GDU6');
  });

  it('символа экрана нет в фиде — первый код фида, а не выдуманный', () => {
    expect(defaultCode([], feed, 'XXZ9@RTSX')).toBe('BRZ6');
  });

  it('нет ни книги, ни фида — пусто, оператор введёт сам', () => {
    expect(defaultCode([], [], '')).toBe('');
    expect(defaultCode(null, null, '')).toBe('');
  });
});


describe('подсказки полей называют единицу', () => {
  // 18.09.2026: оператор ввёл в поле активации тейка ЦЕНУ 84700 вместо пунктов,
  // движок сложил её с входом 83510 и поставил активацию 168210 — позиция 5 RIZ6
  // осталась без тейка. Поэтому каждая подсказка обязана сказать, ЦЕНА это или ПУНКТЫ.
  const fields = KINDS.flatMap((k) => k.fields.map((f) => ({ kind: k.id, ...f })));

  it('у поля уровня/цены в подсказке стоит слово ЦЕНА', () => {
    for (const f of fields.filter((x) => x.key === 'trigger_price' || x.key === 'child_price')) {
      expect(f.hint, `${f.kind}.${f.key}`).toMatch(/ЦЕНА/);
    }
  });

  it('у полей расстояния в подсказке стоит слово ПУНКТ', () => {
    for (const f of fields.filter((x) => ['trail_offset', 'trail_after', 'tp_trail'].includes(x.key))) {
      expect(f.hint, `${f.kind}.${f.key}`).toMatch(/ПУНКТ/);
    }
  });

  it('у стопа и тейка после сделки сказано про оба способа: пунктами ИЛИ ценой', () => {
    for (const f of fields.filter((x) => ['sl_offset', 'tp_offset'].includes(x.key))) {
      expect(f.hint, `${f.kind}.${f.key}`).toMatch(/ПУНКТ/);
      expect(f.hint, `${f.kind}.${f.key}`).toMatch(/ЦЕН/);
    }
  });

  it('у подтягивающей нет поля уровня активации: движок его не принимает', () => {
    const trailSl = KINDS.find((k) => k.id === 'trail_sl')!;
    expect(trailSl.fields.some((f) => f.key === 'trigger_price')).toBe(false);
  });
});
