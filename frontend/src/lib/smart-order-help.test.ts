// Фраза «что произойдёт» — последнее, что оператор читает перед взводом заявки
// на реальные деньги. Перепутанная сторона сравнения здесь означает обещание
// не того, что случится, поэтому направления закреплены тестом.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { KIND_BY_ID, conditionText, preview, smartLegend, smartLevels,
         type Kind, type Side } from './smart-order-help';

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

describe('блоки «после сделки»', () => {
  it('every kind offers the SL/TP-after-fill pair', async () => {
    const { KINDS } = await import('./smart-order-help');
    for (const k of KINDS) {
      const keys = k.fields.map((f) => f.key);
      expect(keys, k.id).toContain('sl_offset');
      expect(keys, k.id).toContain('tp_offset');
    }
  });

  it('preview names the extra orders that will be placed', async () => {
    const { preview } = await import('./smart-order-help');
    const p = {
      kind: 'sl' as Kind, side: 'sell' as Side, qty: 1, code: 'RIU6',
      trigger: 88000, trailOffset: 0, watchId: '', childPrice: 0, price: 88500,
    };
    expect(preview(p).sentence).not.toContain('после сделки');
    const both = preview({ ...p, slOffset: 300, tpOffset: 500 }).sentence;
    expect(both).toContain('стоп 300');
    expect(both).toContain('тейк 500');
    expect(both).toContain('связке');
    const only = preview({ ...p, slOffset: 300 }).sentence;
    expect(only).toContain('стоп 300');
    expect(only).not.toContain('тейк');
  });

  it('kinds carry the new names', async () => {
    const { KIND_BY_ID } = await import('./smart-order-help');
    expect(KIND_BY_ID.sl.name).toBe('Условная');
    expect(KIND_BY_ID.tp.name).toBe('Лимитная');
    expect(KIND_BY_ID.trail_tp.name).toBe('Следящая');
    expect(KIND_BY_ID.on_fill.name).toBe('Зависимая');
  });
});

describe('smartLevels — уровни на графике', () => {
  const base = {
    so_id: 'x1', code: 'RIU6', side: 'sell' as Side, qty: 5, trigger_price: 90590,
    trail_offset: 90, peak: 0, activated: false, child_price: 0,
  };

  it('condition/limit kinds draw one level at the trigger', async () => {
    const { smartLevels } = await import('./smart-order-help');
    const [lv] = smartLevels({ ...base, kind: 'sl' });
    expect(lv.price).toBe(90590);
    expect(lv.title).toBe('▼ 5 к');
  });

  it('trailing shows the activation level while asleep', async () => {
    const { smartLevels } = await import('./smart-order-help');
    const lines = smartLevels({ ...base, kind: 'trail_tp' });
    expect(lines).toHaveLength(1);
    expect(lines[0].price).toBe(90590);
    expect(lines[0].dim).toBe(true);        // вспомогательная, не рабочая
    expect(lines[0].title).toContain('активация');
  });

  it('every line carries side and CONTRACT volume, and никогда — тип', async () => {
    // Шесть спящих следящих подписывались одинаково («СЛЕД активация») — на
    // графике не отличить, какая из них какая и на сколько контрактов. Тип же
    // называет ЛЕГЕНДА: дублировать его в каждой линии = закрывать свечи.
    const { smartLevels, KINDS } = await import('./smart-order-help');
    const all = [
      ...smartLevels({ ...base, kind: 'sl' }),
      ...smartLevels({ ...base, kind: 'tp' }),
      ...smartLevels({ ...base, kind: 'trail_tp' }),
      ...smartLevels({ ...base, kind: 'trail_tp', activated: true, peak: 91000 }),
      ...smartLevels({ ...base, kind: 'on_fill', child_price: 88000 }),
    ];
    expect(all.length).toBeGreaterThan(5);
    for (const lv of all) {
      expect(lv.title, lv.key).toContain('▼ 5 к');
      for (const k of KINDS) expect(lv.title, lv.key).not.toContain(k.short);
    }
    const buy = smartLevels({ ...base, kind: 'sl', side: 'buy', qty: 12 });
    expect(buy[0].title).toBe('▲ 12 к');
  });

  it('softColor тушит тон к фону графика и не трогает чистый цвет легенды', async () => {
    const { softColor, KIND_BY_ID } = await import('./smart-order-help');
    // mix=0 — чистый цвет, только прозрачность
    expect(softColor('#ff6b5a', 0, 0.65)).toBe('rgba(255, 107, 90, 0.65)');
    // mix=1 — полностью фон: ярких плашек не остаётся вовсе
    expect(softColor('#ff6b5a', 1, 0.8)).toBe('rgba(15, 15, 30, 0.8)');
    // рабочая доля: тон заметно темнее исходного по всем каналам
    const soft = softColor('#ff6b5a', 0.45, 0.85);
    const [r, g, b] = soft.match(/\d+/g)!.slice(0, 3).map(Number);
    expect(r).toBeLessThan(0xff);
    expect(g).toBeLessThan(0x6b);
    expect(b).toBeGreaterThan(0);
    expect(soft).toContain('0.85');
    expect(softColor('не цвет')).toBe('не цвет');          // мусор не ломает график
    expect(KIND_BY_ID.sl.color).toBe('#ff6b5a');           // легенда берёт чистый
  });

  it('trailing shows the live exit level and the peak once active', async () => {
    const { smartLevels, TRAIL_ACTIVE_COLOR } = await import('./smart-order-help');
    const lines = smartLevels({ ...base, kind: 'trail_tp', activated: true, peak: 91000 });
    const stop = lines.find((l) => l.key.endsWith(':stop'));
    const peak = lines.find((l) => l.key.endsWith(':peak'));
    expect(stop?.price).toBe(90910);        // продажа: пик 91000 − откат 90
    expect(stop?.color).toBe(TRAIL_ACTIVE_COLOR);
    expect(peak?.price).toBe(91000);
    expect(peak?.dim).toBe(true);
  });

  it('dependent kind draws nothing without an explicit child price', async () => {
    const { smartLevels } = await import('./smart-order-help');
    expect(smartLevels({ ...base, kind: 'on_fill' })).toEqual([]);
    const [lv] = smartLevels({ ...base, kind: 'on_fill', child_price: 88000 });
    expect(lv.price).toBe(88000);
  });
});

// ── Плашки заявок не должны лежать поверх свечей ─────────────────────────────
// lightweight-charts рисует title ценовой линии плашкой ЦВЕТА ЛИНИИ прямо на
// холсте у правого края. Семь взведённых заявок закрывали собой четверть
// графика (жалоба оператора дважды, 08 и 09.08.2026), а ни фон, ни положение
// плашки библиотека настраивать не даёт. Единственный способ — не передавать
// title вовсе. Правка живёт в разметке, юнит-тестом её не поймать, поэтому
// сторожим сам исходник.
describe('умные заявки не перекрывают график', () => {
  const charts = ['src/components/ChartFrame.svelte', 'src/components/MiniChart.svelte'];

  it('ни один график не отдаёт подпись уровня на холст', () => {
    for (const f of charts) {
      const src = readFileSync(resolve(process.cwd(), f), 'utf8');
      expect(src, f).not.toMatch(/title:\s*lv\.title/);
    }
  });

  it('ценник в шкале только у боевых уровней, не у вспомогательных', () => {
    for (const f of charts) {
      const src = readFileSync(resolve(process.cwd(), f), 'utf8');
      expect(src, f).toContain('axisLabelVisible: !lv.dim');
    }
  });
});

// ── Стоп и тейк «после сделки» на графике ────────────────────────────────────
// Оператор выставляет их в форме и рискует по ним деньгами, а на графике их не
// было вовсе. Уровни считаются однозначно, знаки сверены с движком
// (trader/quik/smart_orders.py, _protective): стоп ПРОТИВ входа, тейк В ПОЛЬЗУ.
describe('защитные уровни после входа', () => {
  const base = { so_id: 'x', code: 'RIU6', qty: 5, sl_offset: 0, tp_offset: 0,
                 trigger_price: 0, child_price: 0, trail_offset: 0, peak: 0, activated: false };
  const at = (lv: any[], suffix: string) => lv.find((l) => l.key.endsWith(suffix));

  it('покупка: стоп ниже входа, тейк выше', () => {
    const lv = smartLevels({ ...base, kind: 'sl', side: 'buy',
                             trigger_price: 89_000, sl_offset: 300, tp_offset: 500 });
    expect(at(lv, ':sl')!.price).toBe(88_700);
    expect(at(lv, ':tp')!.price).toBe(89_500);
  });

  it('продажа: зеркально — стоп выше входа, тейк ниже', () => {
    const lv = smartLevels({ ...base, kind: 'sl', side: 'sell',
                             trigger_price: 89_000, sl_offset: 300, tp_offset: 500 });
    expect(at(lv, ':sl')!.price).toBe(89_300);
    expect(at(lv, ':tp')!.price).toBe(88_500);
  });

  it('нулевой отступ уровня не даёт', () => {
    const lv = smartLevels({ ...base, kind: 'tp', side: 'buy', trigger_price: 89_000 });
    expect(lv.filter((l) => l.key.includes(':sl') || l.key.includes(':tp'))).toHaveLength(0);
  });

  it('следящая: до активации считаем от уровня активации, после — от уровня отката', () => {
    const sleeping = smartLevels({ ...base, kind: 'trail_tp', side: 'sell',
                                   trigger_price: 90_000, trail_offset: 200, sl_offset: 100 });
    expect(at(sleeping, ':sl')!.price).toBe(90_100);
    const awake = smartLevels({ ...base, kind: 'trail_tp', side: 'sell', activated: true,
                                peak: 91_000, trail_offset: 200, sl_offset: 100 });
    expect(at(awake, ':sl')!.price).toBe(90_800 + 100);   // откат 90 800, стоп над ним
  });

  it('это ВСПОМОГАТЕЛЬНЫЕ линии: цена ещё не факт, пока родитель не сработал', () => {
    const lv = smartLevels({ ...base, kind: 'sl', side: 'buy',
                             trigger_price: 89_000, sl_offset: 300 });
    expect(at(lv, ':sl')!.dim).toBe(true);
  });

  it('цвет проекции равен цвету заявки, которой она станет', () => {
    const lv = smartLevels({ ...base, kind: 'on_fill', side: 'buy',
                             child_price: 89_000, sl_offset: 300, tp_offset: 500 });
    expect(at(lv, ':sl')!.color).toBe(KIND_BY_ID.sl.color);
    expect(at(lv, ':tp')!.color).toBe(KIND_BY_ID.tp.color);
  });

  it('легенда называет их один раз, а не по разу на заявку', () => {
    const o = { ...base, kind: 'sl', side: 'buy', trigger_price: 89_000, sl_offset: 300 };
    const leg = smartLegend([o, { ...o, so_id: 'y' }, { ...o, so_id: 'z' }]);
    expect(leg.filter((l) => l.text.includes('после входа'))).toHaveLength(1);
  });
});
