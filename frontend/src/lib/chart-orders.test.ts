// Жест мышкой ставит РЕАЛЬНУЮ заявку. Сторона и цена проверяются здесь, потому
// что клик по канве тестом не поймать, а ошибка в стороне это деньги.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { draftBody, kindFromEvent, levelAt, quantize, sideFor } from './chart-orders';

const mods = (o: Partial<{ shiftKey: boolean; ctrlKey: boolean; altKey: boolean; metaKey: boolean }>) =>
  ({ shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, ...o });

describe('модификатор -> тип заявки', () => {
  it('shift условная, ctrl лимитная, alt следящая', () => {
    expect(kindFromEvent(mods({ shiftKey: true }))).toBe('sl');
    expect(kindFromEvent(mods({ ctrlKey: true }))).toBe('tp');
    expect(kindFromEvent(mods({ altKey: true }))).toBe('trail_tp');
  });

  it('cmd на маке работает как ctrl', () => {
    expect(kindFromEvent(mods({ metaKey: true }))).toBe('tp');
  });

  it('без модификатора жеста нет: обычный клик по графику ничего не ставит', () => {
    expect(kindFromEvent(mods({}))).toBeNull();
  });

  it('две клавиши разом дают условную — ошибиться в её пользу дешевле', () => {
    expect(kindFromEvent(mods({ shiftKey: true, altKey: true }))).toBe('sl');
  });
});

describe('сторона выводится из движка, а не из головы', () => {
  const LAST = 87_500;

  it('условная: клик НИЖЕ рынка — продажа (защита лонга), выше — покупка', () => {
    // движок: sl продаёт при цене <= уровня, покупает при >=
    expect(sideFor('sl', 87_000, LAST)).toBe('sell');
    expect(sideFor('sl', 88_000, LAST)).toBe('buy');
  });

  it('лимитная: клик ВЫШЕ рынка — продажа по цели, ниже — покупка', () => {
    expect(sideFor('tp', 88_000, LAST)).toBe('sell');
    expect(sideFor('tp', 87_000, LAST)).toBe('buy');
  });

  it('следящая активируется как лимитная', () => {
    expect(sideFor('trail_tp', 88_000, LAST)).toBe('sell');
    expect(sideFor('trail_tp', 87_000, LAST)).toBe('buy');
  });

  it('зависимая: стороны из уровня НЕ выводится — спрашиваем', () => {
    // у неё нет уровня срабатывания, есть событие; молча подставить «продажу»
    // значило бы угадать сторону сделки за оператора
    expect(sideFor('on_fill', 88_000, LAST)).toBeNull();
  });

  it('без цены рынка сторону не выдумываем', () => {
    expect(sideFor('sl', 87_000, 0)).toBeNull();
    expect(sideFor('sl', 0, LAST)).toBeNull();
  });
});

describe('цена ложится на сетку инструмента', () => {
  it('RI: шаг 10', () => {
    expect(quantize(87_503, 10)).toBe(87_500);
    expect(quantize(87_506, 10)).toBe(87_510);
  });

  it('BR: шаг 0.01 и без хвоста из нулей', () => {
    const v = quantize(82.578, 0.01);
    expect(v).toBe(82.58);
    expect(String(v)).not.toMatch(/0000/);
  });

  it('шаг неизвестен — цену не трогаем, а не выдумываем сетку', () => {
    expect(quantize(87_503.7, 0)).toBe(87_503.7);
  });
});

describe('тело запроса', () => {
  const base = { code: 'RIU6', side: 'sell' as const, qty: 1, price: 87_800 };

  it('уровень едет в trigger_price у всех, кроме зависимой', () => {
    for (const kind of ['sl', 'tp', 'trail_tp'] as const) {
      const b = draftBody({ ...base, kind });
      expect(b.trigger_price, kind).toBe(87_800);
      expect(b.child_price, kind).toBe(0);
    }
  });

  it('у зависимой это цена ДОЧЕРНЕЙ заявки, а уровня нет', () => {
    const b = draftBody({ ...base, kind: 'on_fill' });
    expect(b.trigger_price).toBe(0);
    expect(b.child_price).toBe(87_800);
  });

  it('объём целый и не меньше одного', () => {
    expect(draftBody({ ...base, kind: 'sl', qty: 0 }).qty).toBe(1);
    expect(draftBody({ ...base, kind: 'sl', qty: 3.7 }).qty).toBe(3);
  });

  it('поля чужих типов уходят нулями, а не мусором', () => {
    const b = draftBody({ ...base, kind: 'sl' });
    expect(b.trail_offset).toBe(0);
    expect(b.sl_offset).toBe(0);
    expect(b.tp_offset).toBe(0);
    expect(b.watch_client_id).toBe('');
  });
});

describe('захват уровня мышкой', () => {
  const levels = [{ y: 100 }, { y: 140 }, { y: 300 }];

  it('берётся ближайший в пределах допуска', () => {
    expect(levelAt(102, levels)).toBe(0);
    expect(levelAt(138, levels)).toBe(1);
  });

  it('мимо всех — ничего не захватываем', () => {
    expect(levelAt(200, levels)).toBe(-1);
  });

  it('между двумя близкими берётся тот, что ближе', () => {
    expect(levelAt(103, [{ y: 100 }, { y: 105 }])).toBe(1);
  });
});

// ── Перенос уровня мышкой ────────────────────────────────────────────────────
// Атомарного «подвинуть» у сторожа нет: перенос это снять и взвести заново.
// Значит собственные настройки заявки обязаны уехать вместе с ней, иначе
// перенос молча превратит следящую в обычную и сотрёт защитную пару.
describe('перенос сохраняет саму заявку', () => {
  const src = readFileSync(resolve(process.cwd(), 'src/components/MiniChart.svelte'), 'utf8');

  it('тело собирается по полям, а не спредом всего объекта', () => {
    // в объекте заявки есть so_id и статус — им в запросе не место
    expect(src).not.toMatch(/\{ \.\.\.ask\.o/);
    expect(src).toMatch(/trail_offset: Number\(o\.trail_offset/);
    expect(src).toMatch(/sl_offset: Number\(o\.sl_offset/);
    expect(src).toMatch(/tp_offset: Number\(o\.tp_offset/);
  });

  it('сначала снимаем, потом взводим — двух уровней на бирже быть не должно', () => {
    const i = src.indexOf("method: 'DELETE'");
    const j = src.indexOf("'/api/v1/quik/smart-orders', {", i);
    expect(i).toBeGreaterThan(0);
    expect(j).toBeGreaterThan(i);
  });

  it('если взвести заново не вышло — говорим ГРОМКО, заявки больше нет', () => {
    expect(src).toMatch(/ЗАЯВКА СНЯТА, но не взведена заново/);
  });

  it('ни один жест не ставит заявку сам: всё кончается подтверждением', () => {
    // единственный POST — внутри confirm(), который вызывается кнопкой
    expect(src.match(/method: 'POST'/g) ?? []).toHaveLength(1);
    expect(src).toMatch(/onclick=\{confirm\}/);
  });

  it('тянуть можно только СВОЙ уровень заявки, не расчётные производные', () => {
    expect(src).toMatch(/if \(lv\.dim \|\| !\(lv\.price > 0\)\) continue;/);
  });
});
