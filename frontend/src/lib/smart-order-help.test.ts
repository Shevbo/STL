// Фраза «что произойдёт» — последнее, что оператор читает перед взводом заявки
// на реальные деньги. Перепутанная сторона сравнения здесь означает обещание
// не того, что случится, поэтому направления закреплены тестом.
import { describe, expect, it } from 'vitest';
import { conditionText, preview, type Kind, type Side } from './smart-order-help';

const base = {
  qty: 1, code: 'RIU6', trigger: 0, trailOffset: 0, watchId: '',
  childPrice: 0, price: 88_000, pointValue: 1.5681,
};
// toLocaleString('ru-RU') разделяет тысячи НЕРАЗРЫВНЫМ пробелом: в тесте
// сравниваем по обычному, иначе ловим не смысл, а невидимый символ.
const norm = (s: string) => s.replace(/[  ]/g, ' ');
const p = (kind: Kind, side: Side, over: Partial<typeof base> = {}) => {
  const r = preview({ ...base, ...over, kind, side } as any);
  return { ...r, sentence: norm(r.sentence), distance: norm(r.distance) };
};

describe('направление срабатывания', () => {
  it('стоп-лосс на продажу ждёт цену НИЖЕ уровня (защита лонга)', () => {
    const r = p('sl', 'sell', { trigger: 87_000 });
    expect(r.sentence).toContain('опустится до');
    expect(r.sentence).toContain('ПРОДАЖУ 1 контракт');
    expect(r.error).toBe('');
  });

  it('стоп-лосс на покупку ждёт цену ВЫШЕ уровня (защита шорта)', () => {
    expect(p('sl', 'buy', { trigger: 89_000 }).sentence).toContain('поднимется до');
  });

  it('тейк-профит зеркален стоп-лоссу', () => {
    expect(p('tp', 'sell', { trigger: 89_000 }).sentence).toContain('поднимется до');
    expect(p('tp', 'buy', { trigger: 87_000 }).sentence).toContain('опустится до');
  });
});

describe('расстояние до срабатывания', () => {
  it('считает пункты и рубли по количеству контрактов', () => {
    const r = p('sl', 'sell', { trigger: 87_000, qty: 2 });
    expect(r.distance).toContain('1 000 п.');
    expect(r.distance).toContain('3 136 ₽');   // 1000 п. × 1.5681 × 2
  });

  it('без ₽/пункт показывает только пункты и рублями не врёт', () => {
    const r = p('sl', 'sell', { trigger: 87_000, pointValue: 0 });
    expect(r.distance).toContain('п.');
    expect(r.distance).not.toContain('₽');
  });

  it('предупреждает, когда уровень уже пройден — сторож выстрелит сразу', () => {
    const r = p('sl', 'sell', { trigger: 89_000 });   // цена 88 000, стоп ВЫШЕ
    expect(r.distance).toContain('уровень пройден');
  });
});

describe('скользящий стоп', () => {
  it('без активации говорит, что слежение начнётся сразу', () => {
    const r = p('trail_tp', 'sell', { trailOffset: 300 });
    expect(r.sentence).toContain('начнёт следить сразу');
    expect(r.sentence).toContain('300 п.');
    expect(r.error).toBe('');
  });

  it('с активацией называет её уровень', () => {
    expect(p('trail_tp', 'sell', { trigger: 90_000, trailOffset: 300 }).sentence)
      .toContain('90 000');
  });

  it('требует отступ', () => {
    expect(p('trail_tp', 'sell', {}).error).toContain('отступ');
  });
});

describe('по исполнению', () => {
  it('говорит про событие, а не про уровень', () => {
    const r = p('on_fill', 'sell', { watchId: 'abc123' });
    expect(r.sentence).toContain('abc123');
    expect(r.distance).toContain('ждёт события');
    expect(r.error).toBe('');
  });

  it('требует наблюдаемую заявку', () => {
    expect(p('on_fill', 'sell', {}).error).toContain('следим');
  });
});

describe('отказы', () => {
  it('нулевое количество не взводится', () => {
    expect(p('sl', 'sell', { trigger: 87_000, qty: 0 }).error).toContain('больше нуля');
  });
  it('пустой инструмент не взводится', () => {
    expect(p('sl', 'sell', { trigger: 87_000, code: '' }).error).toContain('инструмент');
  });
  it('sl/tp без уровня не взводится', () => {
    expect(p('tp', 'sell', {}).error).toContain('уровень');
  });
});

describe('строка условия в списке', () => {
  it('показывает направление сравнения, а не голое число', () => {
    expect(norm(conditionText({ kind: 'sl', side: 'sell', trigger_price: 87_000 })))
      .toBe('цена ≤ 87 000');
    expect(norm(conditionText({ kind: 'sl', side: 'buy', trigger_price: 89_000 })))
      .toBe('цена ≥ 89 000');
  });

  it('у скользящего показывает пик, когда он уже есть', () => {
    const s = norm(conditionText({ kind: 'trail_tp', side: 'sell', trigger_price: 0,
                                  trail_offset: 300, activated: true, peak: 89_500 }));
    expect(s).toContain('следит сразу');
    expect(s).toContain('пик 89 500');
  });
});

describe('codeSuggestions', () => {
  it('sorts by usage frequency, then feed codes alphabetically', async () => {
    const { codeSuggestions } = await import('./smart-order-help');
    const orders = [
      { code: 'RIU6' }, { code: 'RIU6' }, { code: 'RIU6' },
      { code: 'BRU6' },
      { code: 'GZU6' }, { code: 'GZU6' },
    ];
    expect(codeSuggestions(orders, ['SiU6', 'BRU6', 'GDU6'])).toEqual(
      ['RIU6', 'GZU6', 'BRU6', 'GDU6', 'SiU6'],   // частые -> хвост фида по алфавиту, без дублей
    );
  });
  it('empty book falls back to feed codes', async () => {
    const { codeSuggestions } = await import('./smart-order-help');
    expect(codeSuggestions([], ['RIU6', 'BRU6'])).toEqual(['BRU6', 'RIU6']);
  });
});
