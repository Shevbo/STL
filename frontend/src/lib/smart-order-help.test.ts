// Фраза «что произойдёт» — последнее, что оператор читает перед взводом заявки
// на реальные деньги. Перепутанная сторона сравнения здесь означает обещание
// не того, что случится, поэтому направления закреплены тестом.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { KIND_BY_ID, afterFillFacts, afterFillPreview, defaultCode, closingSide, canRearm, conditionText, entryOrders, isLive, manualPositions, statusRu, ocoNameOf, nativeStopIndex, preview, protectionPair, shortCodes, smartLegend, smartLevels, stopOrderRow, stopOrderWhy,
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
    // Частые ИЗ ТОРГУЮЩИХСЯ, затем хвост фида по алфавиту, без дублей. RIU6 и GZU6
    // в фиде нет (контракт истёк) — в подсказках им не место (real-trade 18.09).
    expect(codeSuggestions(orders, ['SiU6', 'BRU6', 'GDU6'])).toEqual(
      ['BRU6', 'GDU6', 'SiU6'],
    );
    // Фида нет — подсказок нет. Прежде показывалась история, и в списке снова
    // всплывал истёкший RIU6 (оператор 18.09.2026: «в перечне контрактов не
    // должно быть несуществующих»).
    expect(codeSuggestions(orders, [])).toEqual([]);
  });
  it('empty book falls back to feed codes', async () => {
    const { codeSuggestions } = await import('./smart-order-help');
    expect(codeSuggestions([], ['RIU6', 'BRU6'])).toEqual(['BRU6', 'RIU6']);
  });
});

describe('блоки «после сделки»', () => {
  // ВСЕМ, КРОМЕ ПОДТЯГИВАЮЩЕЙ: она выходит из позиции, блоки после сделки в неё
  // входят, и движок отвечает 422 (smart_orders.py:135, аудит 18.09.2026).
  it('every ENTERING kind offers the SL/TP-after-fill pair', async () => {
    const { KINDS } = await import('./smart-order-help');
    for (const k of KINDS.filter((x) => x.id !== 'trail_sl')) {
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
    expect(lines[0].title).toContain('акт.');   // подпись на линии короткая
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

// ── Подпись на линии: видна и не глухая ──────────────────────────────────────
// В lightweight-charts подпись на линии и ценник в шкале живут в ОДНОЙ ветке:
// `if (!labelVisible) return` стоит до отрисовки подписи. Выключив ценник у
// вспомогательных уровней, мы погасили и их подписи — на графике остались
// волосяные линии без единого слова (жалоба оператора 09.08.2026).
// Фон плашки при этом берётся из axisLabelColor и лишь потом из color, поэтому
// линию можно держать насыщенной, а плашку — прозрачной.
describe('подписи уровней видны и полупрозрачны', () => {
  const charts = ['src/components/ChartFrame.svelte', 'src/components/MiniChart.svelte'];
  const read = (f: string) => readFileSync(resolve(process.cwd(), f), 'utf8');

  it('подпись на линии передаётся', () => {
    for (const f of charts) expect(read(f), f).toMatch(/title:\s*lv\.title/);
  });

  it('ценник в шкале НЕ выключается: вместе с ним гаснет и подпись', () => {
    for (const f of charts) {
      expect(read(f), f).toMatch(/axisLabelVisible:\s*true/);
      expect(read(f), f).not.toMatch(/axisLabelVisible:\s*!/);
    }
  });

  it('плашка полупрозрачна, а линия — нет', () => {
    for (const f of charts) {
      const src = read(f);
      const plaque = src.match(/axisLabelColor:\s*softColor\([^)]*\)/)![0];
      const line = src.match(/[\n\r]\s*color:\s*softColor\(lv\.color,[^)]*\)/)![0];
      const alpha = (t: string) => [...t.matchAll(/0?\.\d+|1/g)].map((x) => Number(x[0]));
      expect(Math.max(...alpha(plaque)), 'плашка ' + f).toBeLessThan(0.7);
      expect(Math.max(...alpha(line)), 'линия ' + f).toBeGreaterThanOrEqual(0.8);
    }
  });
});

// ── Направление защитных заявок и номера связок ──────────────────────────────
describe('стрелка защитной линии — сторона ВЫХОДА', () => {
  const base = { so_id: 'p1', code: 'RIU6', qty: 5, kind: 'sl', trigger_price: 89_000,
                 child_price: 0, trail_offset: 0, peak: 0, activated: false,
                 sl_offset: 300, tp_offset: 500 };
  const pick = (o: any, suf: string) => smartLevels(o).find((l) => l.key.endsWith(suf))!;

  it('родитель ПОКУПАЕТ — стоп и тейк ПРОДАЮТ', () => {
    // Движок: exit_side = "sell" if parent.side == "buy". Подписать защитную
    // линию стрелкой родителя значит нарисовать покупку там, где уйдёт продажа.
    const o = { ...base, side: 'buy' };
    expect(pick(o, ':sl').title).toContain('▼');
    expect(pick(o, ':tp').title).toContain('▼');
    // а сам УРОВЕНЬ по-прежнему считается от стороны родителя
    expect(pick(o, ':sl').price).toBe(88_700);
    expect(pick(o, ':tp').price).toBe(89_500);
  });

  it('родитель ПРОДАЁТ — стоп и тейк ПОКУПАЮТ', () => {
    const o = { ...base, side: 'sell' };
    expect(pick(o, ':sl').title).toContain('▲');
    expect(pick(o, ':tp').title).toContain('▲');
    expect(pick(o, ':sl').price).toBe(89_300);
    expect(pick(o, ':tp').price).toBe(88_500);
  });

  it('у САМОЙ заявки стрелка своя, не перевёрнутая', () => {
    const own = smartLevels({ ...base, side: 'buy' }).find((l) => l.key === 'p1')!;
    expect(own.title).toContain('▲');
  });
});

describe('двузначные номера связок', () => {
  const o = (id: string, extra: any = {}) => ({
    so_id: id, code: 'RIU6', kind: 'sl', side: 'buy', qty: 1, trigger_price: 100,
    sl_offset: 0, tp_offset: 0, child_price: 0, trail_offset: 0, peak: 0,
    activated: false, ...extra,
  });

  it('номер всегда двузначный', () => {
    for (const id of ['a', 'zzzz', 'so-2026-08-09-abcdef', '']) {
      const n = Number(shortCodes([o(id)])[id]);
      expect(n, id).toBeGreaterThanOrEqual(10);
      expect(n, id).toBeLessThanOrEqual(99);
    }
  });

  it('один и тот же so_id даёт один и тот же номер — он не «плавает»', () => {
    const a = shortCodes([o('x'), o('y')])['x'];
    const b = shortCodes([o('x'), o('y')])['x'];
    expect(a).toBe(b);
  });

  it('номера в книге не повторяются', () => {
    const many = Array.from({ length: 40 }, (_, i) => o('order-' + i));
    const codes = Object.values(shortCodes(many));
    expect(new Set(codes).size).toBe(40);
  });

  it('защитный ребёнок носит номер РОДИТЕЛЯ: одна связка — один номер', () => {
    const parent = o('p'), child = o('c', { parent_id: 'p', kind: 'tp' });
    const codes = shortCodes([parent, child]);
    expect(codes['c']).toBe(codes['p']);
  });

  it('заявок нет — карта пуста', () => {
    expect(shortCodes([])).toEqual({});
  });
});

// «Подтягивающая» (trail_sl, 12.08.2026): выходит из УЖЕ ОТКРЫТОЙ позиции.
// Путать её со «следящей» нельзя — та ВХОДИТ и спит до активации.
describe('подтягивающая', () => {
  const base = { kind: 'trail_sl' as const, side: 'sell' as const, qty: 2, code: 'RIU6',
    trigger: 0, trailOffset: 300, watchId: '', childPrice: 0, price: 88000, pointValue: 1.5 };

  it('у неё нет поля активации: движок такую заявку не примет', () => {
    const keys = KIND_BY_ID.trail_sl.fields.map((f) => f.key);
    expect(keys).not.toContain('trigger_price');
    expect(keys[0]).toBe('trail_offset');
  });

  it('фраза говорит про уже открытую позицию и уровень выхода', () => {
    const p = preview(base);
    expect(p.error).toBe('');
    expect(p.sentence).toContain('с первого тика');
    expect(p.sentence).toContain('ПРОДАЖУ 2');
    expect(p.distance.replace(/[\s  ]/g, ' ')).toContain('87 700');  // 88000-300
  });

  it('без отступа взводить нельзя', () => {
    expect(preview({ ...base, trailOffset: 0 }).error).toContain('отступ');
  });

  it('стоп и подтягивающая после сделки вместе запрещены', () => {
    const both = preview({ ...base, slOffset: 100, trailAfter: 200 });
    expect(both.error).toContain('вместе нельзя');
    // по отдельности — можно, и обе названы во фразе
    expect(preview({ ...base, trailAfter: 200 }).error).toBe('');
    expect(preview({ ...base, trailAfter: 200 }).sentence).toContain('подтягивающая 200');
    expect(preview({ ...base, slOffset: 100, tpOffset: 300 }).error).toBe('');
  });

  it('на графике только уровень выхода и лучшая цена, без активации', () => {
    const armed = smartLevels({ so_id: 'x', kind: 'trail_sl', side: 'sell', qty: 2,
      trail_offset: 300, peak: 88500, trigger_price: 0 });
    expect(armed.map((l) => l.key)).toEqual(['x:stop', 'x:peak']);
    expect(armed[0].price).toBe(88200);
    // пика ещё нет — рисовать нечего, выдуманный уровень хуже пустого места
    expect(smartLevels({ so_id: 'x', kind: 'trail_sl', side: 'sell', qty: 2,
      trail_offset: 300, peak: 0 })).toHaveLength(0);
  });

  it('в списке видно, что она стережёт позицию, а не ждёт активации', () => {
    const t = conditionText({ kind: 'trail_sl', side: 'sell', trail_offset: 300, peak: 88500 });
    expect(t).toContain('стережёт позицию');
    expect(t.replace(/[\s  ]/g, ' ')).toContain('88 200');
    expect(t).not.toContain('активация');
  });
});

// Умная заявка — МАНУАЛЬНЫЙ класс: закрывать ею контракты роботов нельзя.
describe('открытые позиции для формы', () => {
  const status = { health: { positions: [
    { sec: 'RIU6', net: 24, avg: 85615 },
    { sec: 'GZU6', net: -3, avg: 21000 },
    { sec: 'BRU6', net: 0, avg: 0 },
  ] } };
  const mirror = { robots: [
    { symbol: 'RIU6', position: '-5', paper: false },
    { symbol: 'RIU6', position: '30', paper: false },
    { symbol: 'RIU6', position: '-7', paper: true },     // бумажный на бирже ничего не держит
    { symbol: 'GZU6', position: null, paper: false },
  ] };

  it('ручное = нетто счёта минус позиции РЕАЛЬНЫХ роботов', () => {
    const rows = manualPositions(status, mirror);
    const ri = rows.find((r) => r.code === 'RIU6')!;
    expect([ri.net, ri.robots, ri.manual]).toEqual([24, 25, -1]);
    const gz = rows.find((r) => r.code === 'GZU6')!;
    expect([gz.net, gz.robots, gz.manual]).toEqual([-3, 0, -3]);
    expect(rows.find((r) => r.code === 'BRU6')).toBeUndefined();  // нулевую не показываем
    expect(rows[0].code).toBe('GZU6');                            // крупнейшее ручное сверху
  });

  it('сторона ЗАКРЫТИЯ обратна знаку позиции', () => {
    expect(closingSide(5)).toBe('sell');
    expect(closingSide(-5)).toBe('buy');
  });

  it('пустые данные не роняют форму', () => {
    expect(manualPositions(null, null)).toEqual([]);
    expect(manualPositions({}, { robots: [{ symbol: 'X' }] })).toEqual([]);
  });
});


describe('следящий тейк после сделки (tp_trail, real-trade 17.09)', () => {
  const base = { kind: 'sl' as const, side: 'buy' as const, code: 'RIZ6', qty: 1, trigger: 90000,
                 trailOffset: 0, price: 89000, watchId: '', childPrice: 0 };

  it('без уровня активации не пускает до кнопки: движок вернул бы 422', () => {
    expect(preview({ ...base, tpOffset: 0, tpTrail: 50 } as any).error).toMatch(/уровень активации/);
  });

  it('фраза называет следящий тейк с активацией и откатом', () => {
    const r = preview({ ...base, tpOffset: 700, tpTrail: 50 } as any);
    expect(r.error).toBe('');
    expect(r.sentence).toMatch(/следящий тейк: активация через 700.*откат 50/);
  });

  // 18.09: активация ценой гасила кнопку взвода намертво, хотя движок такую
  // заявку принимает (smart_orders.py:169) — форма была строже движка.
  it('активация ценой уровня — такой же полноценный уровень, как пункты', () => {
    const r = preview({ ...base, tpOffset: 0, tpPrice: 83456, tpTrail: 50 } as any);
    expect(r.error).toBe('');
    expect(norm(r.sentence)).toMatch(/следящий тейк: активация с 83 456.*откат 50/);
  });

  it('стоп ценой уровня тоже виден форме: с подтягивающей его не пускают', () => {
    expect(preview({ ...base, slPrice: 82000, trailAfter: 200 } as any).error).toContain('вместе нельзя');
    expect(norm(preview({ ...base, slPrice: 82000 } as any).sentence)).toMatch(/стоп на 82 000/);
  });

  it('без отката тейк остаётся фиксированным, как было', () => {
    const r = preview({ ...base, tpOffset: 700, tpTrail: 0 } as any);
    expect(r.sentence).toMatch(/тейк 700/);
    expect(r.sentence).not.toMatch(/следящий/);
  });
});

describe('блок после сделки: пункты или цена уровня (real-trade 18.09)', () => {
  const pv = (over: Partial<Parameters<typeof afterFillPreview>[0]>) =>
    afterFillPreview({
      side: 'buy', price: 83_500, slOffset: '', slPrice: '', tpOffset: '', tpPrice: '',
      tpMode: 'fixed', afterMode: 'sl', ...over,
    });

  it('пункты считаются от предполагаемого входа и честно это говорят', () => {
    const r = pv({ slOffset: '390', tpOffset: '700' });
    expect(norm(r.sl)).toContain('при входе около 83 500');
    expect(norm(r.sl)).toContain('83 110');
    expect(norm(r.tp)).toContain('84 200');
  });

  it('цена уровня показывается как точный уровень, без «около»', () => {
    const r = pv({ slPrice: '83100', tpPrice: '83690' });
    expect(norm(r.sl)).toBe('стоп ровно на 83 100');
    expect(norm(r.tp)).toBe('тейк на 83 690');
    expect(r.tp).not.toContain('около');
  });

  it('следящий тейк называет уровень точкой начала слежения', () => {
    expect(norm(pv({ tpPrice: '83690', tpMode: 'trail' }).tp)).toBe('тейк начнёт следить с 83 690');
  });

  it('для шорта пункты откладываются в другую сторону', () => {
    const r = pv({ side: 'sell', slOffset: '390', tpOffset: '700' });
    expect(norm(r.sl)).toContain('83 890');
    expect(norm(r.tp)).toContain('82 800');
  });

  it('подтягивающая вместо стопа: строки стопа нет', () => {
    expect(pv({ slOffset: '390', afterMode: 'trail' }).sl).toBe('');
  });
});


describe('инструмент по умолчанию: только живой контракт (real-trade 18.09)', () => {
  const history = [{ code: 'RIU6' }, { code: 'RIU6' }, { code: 'RIU6' }, { code: 'RIZ6' }];

  it('истёкший контракт из истории не подставляется, даже если он самый частый', () => {
    expect(defaultCode(history, ['RIZ6', 'GZZ6'])).toBe('RIZ6');
  });

  it('среди живых по-прежнему выигрывает самый используемый', () => {
    const h = [{ code: 'GZZ6' }, { code: 'GZZ6' }, { code: 'RIZ6' }];
    expect(defaultCode(h, ['RIZ6', 'GZZ6'])).toBe('GZZ6');
  });

  it('фида ещё нет — поле остаётся ПУСТЫМ, истёкший контракт не подставляем', () => {
    expect(defaultCode(history, [])).toBe('');
  });

  it('истории нет — берём символ экрана, если он торгуется', () => {
    expect(defaultCode([], ['RIZ6', 'GZZ6'], 'GZZ6@SPBFUT')).toBe('GZZ6');
  });
});

// Клик по <button> внутри <label> браузер дублирует на связанный контрол:
// переключатели «Стоп/Подтягивающая» и «Фикс./Следящий» срабатывали через раз
// и «нажимались с третьей попытки» (оператор, 18.09.2026). Поля формы с
// кнопками внутри обязаны быть div, а не label.
describe('форма умных заявок: кнопки не живут внутри label', () => {
  const src = readFileSync(resolve(process.cwd(), 'src/components/orders/SmartOrders.svelte'), 'utf8');
  const form = src.slice(src.indexOf('<div class="so-fields">'), src.indexOf('<style>'));

  it('ни один label не оборачивает кнопку', () => {
    for (const block of form.split(/<label\b/).slice(1)) {
      const body = block.slice(0, block.indexOf('</label>'));
      expect(body, `label с кнопкой внутри:\n${body.slice(0, 200)}`).not.toMatch(/<button\b/);
    }
  });

  it('переключатели различимы: у выбранного положения свой фон, а не соседний тон', () => {
    expect(src).toMatch(/\.so-seg button\.on\s*\{[^}]*box-shadow/);
  });
});

// Оператор 18.09: «стоп может быть фиксированным, а тейк следящим — у тебя на
// форме нет этой опции». Связка законна (native_protect.py: стоп + любой тейк =
// одна запись TAKE_PROFIT_AND_STOP_LIMIT_ORDER), не читалась только подпись.
describe('пара защитников после сделки называется вслух', () => {
  const pp = (o: Partial<Parameters<typeof protectionPair>[0]>) =>
    protectionPair({ afterMode: 'sl', tpMode: 'fixed', hasStop: true, hasTake: true, ...o });

  it('фиксированный стоп и следящий тейк — законная пара, обе ноги названы', () => {
    const s = pp({ tpMode: 'trail' });
    expect(s).toContain('фиксированный стоп + следящий тейк');
    expect(s).toContain('две ноги');
  });

  it('подтягивающийся стоп у QUIK невыразим и остаётся сторожу STL', () => {
    expect(pp({ afterMode: 'trail' })).toContain('сторожу STL');
    expect(pp({ afterMode: 'trail', hasTake: false })).toContain('остаётся сторожу STL');
  });

  it('одна нога — это одна нога, а пустая защита названа пустой', () => {
    expect(pp({ hasStop: false, tpMode: 'trail' })).toMatch(/^следящий тейк\./);
    expect(pp({ hasTake: false })).toMatch(/^фиксированный стоп\./);
    expect(pp({ hasStop: false, hasTake: false })).toContain('Защиты после сделки нет');
  });
});

// КОРЕНЬ «глюков» формы (оператор 18.09): bind:value на <input type="number">
// кладёт в состояние ЧИСЛО, а не строку. Хелперы звали .trim(), падали с
// TypeError внутри $derived — и рвали реактивность ВСЕГО экрана: переключатели
// переставали нажиматься, заполненный стоп выглядел выключенным, итоговая
// строка врала «защиты нет». Поэтому числа тут первоклассный вход, не строки.
describe('поля формы приходят числами, а не строками', () => {
  it('afterFillPreview не падает на числах и считает те же уровни', () => {
    const asNum = afterFillPreview({
      side: 'buy', price: 83_500, slOffset: 390, slPrice: '', tpOffset: '', tpPrice: 83_690,
      tpMode: 'trail', afterMode: 'sl',
    });
    expect(norm(asNum.sl)).toContain('83 110');
    expect(norm(asNum.tp)).toBe('тейк начнёт следить с 83 690');
  });

  it('пустое поле приходит как null и не превращается в ноль-уровень', () => {
    const r = afterFillPreview({
      side: 'buy', price: 83_500, slOffset: null, slPrice: null, tpOffset: null, tpPrice: null,
      tpMode: 'fixed', afterMode: 'sl',
    });
    expect(r.sl).toBe('');
    expect(r.tp).toBe('');
  });

  it('экран не зовёт .trim() на значении числового поля', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/components/orders/SmartOrders.svelte'), 'utf8');
    const tr = src.match(/const tr = .*/)![0];
    expect(tr).toContain('String(');
  });
});

// Аудит экрана 18.09.2026: форма обещала то, чего движок не делает.
describe('форма не обещает того, чего движок не делает', () => {
  it('у подтягивающей блоков после сделки НЕТ: движок отвечает 422', () => {
    // trader/quik/smart_orders.py:135 — она ВЫХОДИТ из позиции, блоки ВХОДЯТ.
    const keys = KIND_BY_ID.trail_sl.fields.map((f) => f.key);
    expect(keys).not.toContain('sl_offset');
    expect(keys).not.toContain('tp_offset');
    expect(keys).not.toContain('trail_after');
  });

  it('входящие типы блоки после сделки сохраняют', () => {
    for (const id of ['sl', 'tp', 'trail_tp', 'on_fill'] as const) {
      const keys = KIND_BY_ID[id].fields.map((f) => f.key);
      expect(keys, id).toContain('sl_offset');
      expect(keys, id).toContain('tp_offset');
    }
  });

  it('с подтягивающей терминал не берёт и тейк — охрану ему не обещаем', () => {
    // native_protect.py:54: trail_after > 0 -> None, вся пара остаётся в STL.
    const s = protectionPair({ afterMode: 'trail', tpMode: 'trail', hasStop: true, hasTake: true });
    expect(s).toContain('Оба остаются сторожу STL');
    expect(s).not.toContain('под охрану терминала');
  });

  it('арминг шлёт только поля своего типа', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/components/orders/SmartOrders.svelte'), 'utf8');
    const body = src.slice(src.indexOf('async function arm()'), src.indexOf('good_till_ms'));
    expect(body).toContain('meta.fields.map((f) => f.key)');
    // Каждое числовое поле тела проходит через гвард своего ключа.
    for (const k of ['trigger_price', 'trail_offset', 'sl_offset', 'tp_offset', 'trail_after']) {
      expect(body, k).toContain(`${k}: only(`);
    }
  });
});

// Оператор 18.09: «у 4117394fd0 не описан следящий тейк». Тейк был задан ЦЕНОЙ
// уровня, а карточка перечисляла только пунктовые блоки и молчала про него.
describe('карточка перечисляет ВСЕ блоки после сделки', () => {
  it('тейк, заданный ценой, назван — и назван следящим, если есть откат', () => {
    const s = norm(afterFillFacts({ sl_offset: 300, tp_price: 83_610, tp_trail: 50 }));
    expect(s).toContain('стоп 300 п. от её цены');
    expect(s).toContain('следящий тейк: активация на 83 610, откат 50 п.');
    expect(s).toContain('в одной связке');
  });

  it('стоп ценой уровня и подтягивающая тоже видны', () => {
    expect(norm(afterFillFacts({ sl_price: 82_000 }))).toBe('стоп на 82 000');
    expect(norm(afterFillFacts({ trail_after: 200 }))).toBe('подтягивающийся стоп 200 п. от лучшей цены');
  });

  it('блоков нет — строки нет', () => {
    expect(afterFillFacts({})).toBe('');
    expect(afterFillFacts({ sl_offset: 0, tp_offset: 0 })).toBe('');
  });
});

// Оператор 18.09: «Связка OCO не подтягивается при нажатии "Закрыть свою
// позицию" — должна браться у заявки, по которой набрана позиция».
describe('связка выхода берётся у входа', () => {
  const o = (over: any) => ({ so_id: 'x', code: 'RIZ6', side: 'buy' as Side, qty: 1,
                              status: 'fired', fired_ms: 1, ...over });

  it('лонг набран покупками: берём их, продажи не берём', () => {
    const book = [o({ so_id: 'a', fired_ms: 10 }), o({ so_id: 'b', side: 'sell', fired_ms: 20 }),
                  o({ so_id: 'c', fired_ms: 30 })];
    expect(entryOrders(book, 'RIZ6', 5).map((x) => x.so_id)).toEqual(['c', 'a']);  // свежие сверху
  });

  it('шорт набран продажами', () => {
    const book = [o({ so_id: 'a' }), o({ so_id: 'b', side: 'sell' })];
    expect(entryOrders(book, 'RIZ6', -5).map((x) => x.so_id)).toEqual(['b']);
  });

  it('несработавшие и защитные дети позицию не набирали', () => {
    const book = [o({ so_id: 'armed', status: 'armed', fired_ms: 0 }),
                  o({ so_id: 'child', parent_id: 'a' }),
                  o({ so_id: 'real' })];
    expect(entryOrders(book, 'RIZ6', 5).map((x) => x.so_id)).toEqual(['real']);
  });

  it('чужой инструмент и пустая позиция не дают кандидатов', () => {
    expect(entryOrders([o({})], 'GZZ6', 5)).toEqual([]);
    expect(entryOrders([o({})], 'RIZ6', 0)).toEqual([]);
  });

  it('имя связки: своё, если есть, иначе id входа', () => {
    expect(ocoNameOf({ so_id: 'abc', oco_group: 'bracket-1' })).toBe('bracket-1');
    expect(ocoNameOf({ so_id: 'abc', oco_group: '  ' })).toBe('abc');
    expect(ocoNameOf({ so_id: 'abc' })).toBe('abc');
  });
});

// real-trade 18.09: под охрану терминала уезжает и одиночный стоп/тейк на уже
// открытую позицию. Такую заявку ведёт САМ QUIK — подпись «хранится на STL ·
// сторож раз в секунду» называла бы неверного сторожа.
describe('карточка называет верного сторожа', () => {
  const src = readFileSync(resolve(process.cwd(), 'src/components/orders/SmartOrders.svelte'), 'utf8');

  it('под охраной терминала сказано про терминал, а не про сторожа STL', () => {
    const i = src.indexOf('ведёт терминал QUIK');
    expect(i, 'подписи про терминал нет').toBeGreaterThan(0);
    // Она стоит в ветке native, а «хранится на STL» — в противоположной.
    const block = src.slice(i - 400, i + 400);
    expect(block).toContain("o.status === 'native'");
    expect(block).toContain('хранится на STL');
  });

  it('native считается живым статусом: терминал стережёт и без STL', () => {
    expect(isLive('native')).toBe(true);
    expect(isLive('fired')).toBe(false);
  });
});

// Оператор 18.09: в терминале три активные стоп-заявки от STL, а на экране
// ORDERS их нет вовсе — маршрут /orders/stop-orders не читал никто.
describe('стоп-заявки терминала', () => {
  it('известные поля берутся по точному имени, прочее уходит в сырое раскрытие', () => {
    const r = stopOrderRow({
      stop_order_num: '310471054', sec_code: 'RIZ6', class_code: 'SPBFUT',
      qty: '10', condition_price: '84510', price: '84490', stop_order_kind: 'SIMPLE_STOP_ORDER',
      flags: '1', brokerref: 'stl-so-afa462a78',
    });
    expect(r.num).toBe('310471054');
    expect(r.code).toBe('RIZ6');
    expect(r.qty).toBe(10);
    expect(r.cond).toBe(84510);
    expect(r.state).toBe('активна');              // flags расшифрованы сериями S1/S2
    expect(r.ours).toBe('afa462a78');             // метка из brokerref (S1: тег дошёл)
    expect(r.rest).toEqual([]);                   // непрочитанных полей в этой строке нет
  });

  it('наша запись узнаётся по метке stl-so в ЛЮБОМ поле, не только по номеру', () => {
    // В каком поле QUIK отдаёт комментарий, на нашем терминале не проверено.
    expect(stopOrderRow({ some_unknown_column: 'stl-so-e73e0d1b4' }).ours).toBe('e73e0d1b4');
    expect(stopOrderRow({ sec_code: 'RIZ6' }).ours).toBeNull();
  });

  it('номер связывает запись с нашей умной заявкой', () => {
    const idx = nativeStopIndex([{ so_id: 'ff52047ab0', native_stop_num: '310471054' }]);
    expect(stopOrderRow({ stop_order_num: '310471054' }, idx).ours).toBe('ff52047ab0');
  });

  it('отсутствующее поле остаётся пустым, а не превращается в ноль', () => {
    const r = stopOrderRow({ stop_order_num: '1' });
    expect(r.qty).toBeNull();
    expect(r.cond).toBeNull();
    expect(r.code).toBeNull();
  });
});

// Состояние стоп-заявки НЕ угадано: числа взяты из docs/design/execution-module.md,
// серии S1 и S2 на живом счёте (25/26 постановка и снятие, 29->28 срабатывание,
// 30 и 4126 снятие вместе с базовой).
describe('состояние стоп-заявки терминала по проверенным флагам', () => {
  const st = (flags: number) => stopOrderRow({ flags: String(flags) }).state;

  it('бит0 — активна', () => {
    expect(st(25)).toBe('активна');
    expect(st(29)).toBe('активна');
  });

  it('бит1 — снята, и снятая остаётся в таблице до конца сессии', () => {
    expect(st(26)).toBe('снята');
    expect(st(30)).toBe('снята');
    expect(st(4126)).toBe('снята');
  });

  it('бит0 снят, бита1 нет — исполнена, а не снята', () => {
    expect(st(28)).toBe('исполнена');
  });

  it('флагов нет — состояние неизвестно, и такую строку не прячем', () => {
    const r = stopOrderRow({ stop_order_num: '1' });
    expect(r.state).toBeNull();
    expect(r.done).toBe(false);
  });

  it('прячем только то, что точно отработало', () => {
    expect(stopOrderRow({ flags: '29' }).done).toBe(false);
    expect(stopOrderRow({ flags: '26' }).done).toBe(true);
    expect(stopOrderRow({ flags: '28' }).done).toBe(true);
  });
});

// Оператор 18.09: «ты забыл указать направление заявки». Номер тоже не рисовался:
// терминал вернул его в order_num (execution-module.md, S1), а не stop_order_num.
describe('направление и номер стоп-заявки терминала', () => {
  it('номер берётся из order_num / ordernum, а не только из stop_order_num', () => {
    expect(stopOrderRow({ order_num: '1012419293' }).num).toBe('1012419293');
    expect(stopOrderRow({ ordernum: '310471054' }).num).toBe('310471054');
  });

  it('явное поле операции сильнее флагов', () => {
    expect(stopOrderRow({ operation: 'S', flags: '25' }).dir).toBe('sell');
    expect(stopOrderRow({ operation: 'B', flags: '29' }).dir).toBe('buy');
  });

  it('без явного поля — бит2 flags: S1 покупка 25, S2 продажа 29', () => {
    expect(stopOrderRow({ flags: '25' }).dir).toBe('buy');
    expect(stopOrderRow({ flags: '29' }).dir).toBe('sell');
  });

  it('флагов нет — направление неизвестно, а не «покупка»', () => {
    expect(stopOrderRow({ order_num: '1' }).dir).toBeNull();
  });
});

describe('дочерняя запись объясняет себя', () => {
  const row = { dir: 'sell' as Side, cond: 84_510, price: 84_490, ours: 'abc' };

  it('стоп на продажу ждёт падения и называет расстояние', () => {
    const so = { kind: 'sl' as Kind, side: 'buy' as Side, sl_offset: 300 };
    const r = stopOrderWhy(row, so, 84_800, 1.5681);
    expect(norm(r.waits)).toContain('ждёт цену ≤ 84 510');
    expect(norm(r.waits)).toContain('290 п.');
    expect(r.why).toContain('против позиции');
    expect(r.why).toContain('2 шага');           // подушка лимита ребёнка
  });

  it('тейк на продажу ждёт РОСТА: условие смотрит в другую сторону', () => {
    const so = { kind: 'sl' as Kind, side: 'buy' as Side, tp_offset: 700 };
    expect(norm(stopOrderWhy(row, so, 84_200).waits)).toContain('ждёт цену ≥ 84 510');
  });

  it('следящий тейк называет откат', () => {
    const so = { kind: 'sl' as Kind, side: 'buy' as Side, tp_offset: 700, tp_trail: 50 };
    expect(stopOrderWhy(row, so).why).toContain('откате 50 п.');
  });

  it('чужая запись не получает выдуманного условия', () => {
    const r = stopOrderWhy({ ...row, ours: null }, null, 84_800);
    expect(r.waits).not.toContain('ждёт цену');
    expect(r.why).toContain('руками');
  });
});

// real-trade 22.09.2026 (коммит 7760133): у `orphaned` появился ВТОРОЙ смысл —
// связка в терминале исполнилась, но какая нога сработала, не установлено.
// Подписывать это словами «дочерняя заявка не дожила» значит звать чинить не то,
// а кнопка «Перевзвести» на закрытой позиции откроет обратную.
describe('статус orphaned: два разных смысла', () => {
  it('нативная связка без атрибуции зовёт проверить позицию', () => {
    const s = statusRu({ status: 'orphaned', native_stop_num: '310471054' });
    expect(s).toContain('НЕ подтверждено');
    expect(s).toContain('проверьте позицию');
  });

  it('прежний смысл сохранён: дочерняя заявка не дожила', () => {
    expect(statusRu({ status: 'orphaned' })).toBe('дочерняя заявка не дожила');
  });

  it('перевзводить можно прежний случай и истёкший срок, но НЕ неподтверждённое', () => {
    expect(canRearm({ status: 'orphaned' })).toBe(true);
    expect(canRearm({ status: 'expired' })).toBe(true);
    expect(canRearm({ status: 'orphaned', native_state: 'done' })).toBe(false);
    expect(canRearm({ status: 'orphaned', native_stop_num: '1' })).toBe(false);
    expect(canRearm({ status: 'fired' })).toBe(false);
  });

  it('остальные статусы словами не изменились', () => {
    expect(statusRu({ status: 'native' })).toBe('под охраной терминала');
    expect(statusRu({ status: 'armed' })).toBe('взведена');
  });
});
